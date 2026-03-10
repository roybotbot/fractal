# Runner loop

The runner is the execution engine. It owns the traversal algorithm, all LLM calls, all state transitions, and all signal routing. Nothing else in the system changes node state.

---

## Entry point

```python
class Runner:
    def __init__(
        self,
        tree: TaskTree,
        llm_client: LLMClient,
        detector: DriftDetector,
        gate_runner: GateRunner,
        notifier: Notifier,
        state_manager: StateManager,
    ): ...

    def run(self) -> TaskTree:
        """
        Execute the full tree to completion.
        Returns the completed tree.
        Raises HumanReviewRequired if an abort-level signal fires.
        """
        while not self.tree.is_complete():
            node = self.tree.next_executable()
            if node is None:
                raise StuckSession("No executable nodes, tree not complete")
            self._execute_node(node)
            self.state_manager.save(self.tree)
        return self.tree
```

State is saved after every node completes. If the process dies mid-session, resume loads the last saved state and continues from `next_executable()`.

---

## Node execution

```python
def _execute_node(self, node: TaskNode):
    node.status = NodeStatus.IN_PROGRESS
    node.started_at = datetime.utcnow()

    if node.is_composition:
        self._decompose(node)
    else:
        self._execute_leaf(node)
```

### Decomposition path

```python
def _decompose(self, node: TaskNode):
    node.status = NodeStatus.DECOMPOSING

    # Run the node's steps — for composition types these are:
    # enumerate_children, define_sequencing, define_rollback,
    # write_integration_tests
    # The children list comes out of the enumerate_children step
    for step in node.steps:
        self._execute_step(node, step)

    # Children are now populated on the node
    # Register them all in the tree index
    for child in node.sub_nodes:
        self.tree.register(child)
        child.parent_id = node.id

    # Children execute via the main while loop
    # The runner will pick them up via next_executable()
    # This node completes only when all children complete
    # That check happens at the end of each child's completion
```

### Leaf execution path

```python
def _execute_leaf(self, node: TaskNode):
    for step in node.steps:
        if step.status == StepStatus.COMPLETE:
            continue  # resume: skip already-completed steps
        self._execute_step(node, step)

    # All steps done — run gates
    gate_results = self.gate_runner.run_all(node)
    node.gate_results = gate_results

    if node.all_gates_passed:
        node.status = NodeStatus.COMPLETE
        node.completed_at = datetime.utcnow()
        self._check_parent_completion(node)
    else:
        self._handle_gate_failures(node, gate_results)
```

---

## Step execution

```python
def _execute_step(self, node: TaskNode, step: StepRecord):
    step.status = StepStatus.ACTIVE

    # Build prompt with full context injection
    prompt = self.context_builder.build(
        node=node,
        step=step,
        global_schema=self.tree.schema_registry,
        correction_context=step.correction_context,
    )

    output = self.llm_client.call(prompt)

    # Run drift detector
    drift_signals = self.detector.check_all(node, step, output)
    uncertain_signals = self.uncertainty_detector.check_all(node, step, output)

    # Route drift signals
    if any(s.severity == Severity.ABORT for s in drift_signals):
        node.status = NodeStatus.FAILED
        raise HumanReviewRequired(node, drift_signals)

    if any(s.severity == Severity.BLOCK for s in drift_signals):
        output = self._handle_block(node, step, drift_signals, output)
        # _handle_block returns corrected output or raises HumanReviewRequired

    # Route uncertainty signals
    immediate = [s for s in uncertain_signals if s.uncertainty_type in INTERRUPT_IMMEDIATELY]
    bufferable = [s for s in uncertain_signals if s.uncertainty_type in BATCH_AND_NOTIFY]

    if immediate:
        resolutions = self.notifier.interrupt(immediate)
        output = self._apply_resolutions(node, step, output, resolutions, immediate)

    if bufferable:
        self.notifier.buffer(bufferable)
        if self.notifier.should_flush():
            buffered = self.notifier.drain()
            resolutions = self.notifier.notify_batch(buffered)
            # Apply resolutions to the buffered steps
            # (some may already be past their step — logged but not re-executed)

    # Mark step complete
    step.output = output
    step.status = StepStatus.COMPLETE
    step.completed_at = datetime.utcnow()
```

---

## Handling a block

```python
def _handle_block(
    self,
    node: TaskNode,
    step: StepRecord,
    signals: list[DriftSignal],
    original_output: str,
) -> str:
    if step.retry_count >= step.max_retries:
        # Step has already retried once — escalate
        node.status = NodeStatus.FAILED
        raise HumanReviewRequired(node, signals)

    step.retry_count += 1
    step.status = StepStatus.RETRYING
    node.status = NodeStatus.BLOCKED

    # Build correction context from all blocking signals
    correction = "\n".join(s.correction_context() for s in signals if s.severity == Severity.BLOCK)
    step.correction_context = correction

    # Re-execute the step with correction prepended
    prompt = self.context_builder.build(
        node=node,
        step=step,
        global_schema=self.tree.schema_registry,
        correction_context=correction,
    )

    retry_output = self.llm_client.call(prompt)

    # Re-run drift checks on retry output
    retry_signals = self.detector.check_all(node, step, retry_output)
    if any(s.severity == Severity.BLOCK for s in retry_signals):
        # Failed again — escalate
        node.status = NodeStatus.FAILED
        raise HumanReviewRequired(node, retry_signals)

    node.status = NodeStatus.IN_PROGRESS
    return retry_output
```

One retry per step. The correction context is specific about what was wrong. If the retry fails again, something is genuinely wrong — human judgment is needed.

---

## Gate failure handling

```python
def _handle_gate_failures(self, node: TaskNode, gate_results: list[GateResult]):
    failing = [g for g in gate_results if not g.passed]

    # Abort-level gate failures
    abort_gates = [g for g in failing if g.gate.on_failure == "abort"]
    if abort_gates:
        node.status = NodeStatus.FAILED
        raise HumanReviewRequired(node, abort_gates)

    # Block-level gate failures
    # Find the last step that produced the artifact the gate checks
    # Re-run that step with gate failure as correction context
    node.status = NodeStatus.BLOCKED
    node.retry_count += 1

    if node.retry_count > node.max_retries:
        node.status = NodeStatus.FAILED
        raise HumanReviewRequired(node, failing)

    correction = self._build_gate_correction(failing)

    # Inject gate failure context into the relevant step's correction_context
    # and re-execute from that step
    target_step = self._find_step_responsible_for(node, failing[0])
    target_step.correction_context = correction
    target_step.status = StepStatus.PENDING
    target_step.retry_count += 1

    self._execute_leaf(node)
```

---

## Parent completion check

When a leaf node completes, the runner checks whether its parent composition node can now complete:

```python
def _check_parent_completion(self, node: TaskNode):
    if node.parent_id is None:
        return
    parent = self.tree.get(node.parent_id)
    if parent and parent.all_children_complete:
        # All children done — run parent's integration tests
        # then mark parent complete
        parent_gates = self.gate_runner.run_all(parent)
        if all(g.passed for g in parent_gates):
            parent.status = NodeStatus.COMPLETE
            parent.completed_at = datetime.utcnow()
            self._check_parent_completion(parent)  # propagate up
```

Completion propagates up the tree. A leaf completing might complete its parent, which might complete its grandparent. The tree finishes when the root node completes.

---

## Context building

The context builder assembles every LLM call's input:

```
[SYSTEM BLOCK — always present]
You are operating inside a structured task execution system.
Complete ONLY the current step. Do not proceed to subsequent steps.
Do not implement work that belongs to a different node.

[CORRECTION CONTEXT — present on retry only]
{correction_context}
─────────────────────────────────────────

[NODE CONTEXT]
{node.to_context_summary()}

[GLOBAL SCHEMA REGISTRY]
{schema_registry.to_string()}

[STEP PROMPT]
{step_template.prompt_template.format(node=node)}
```

The system block is immutable and always first. Its three sentences are the most important thing in the context. They're short enough to survive context dilution — long enough into the session, the model will re-read them before every step.

Context window budget: the runner tracks token usage. If the accumulated step outputs + schema registry would push the context over budget, it summarizes older completed step outputs rather than including them verbatim. The current step output is always included verbatim.

---

## Session state saves

```python
# After every node completion:
self.state_manager.save(self.tree)

# Save includes:
# - Full node tree with all step outputs
# - All gate results
# - All signal logs
# - Session metadata (start time, last save, node count)
```

Resume loads the last save, reconstructs the tree, and calls `run()`. The `next_executable()` method skips completed nodes. Steps with `StepStatus.COMPLETE` are skipped in `_execute_leaf`. The session continues exactly where it left off.
