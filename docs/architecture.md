# System architecture

## Component map

```
┌─────────────────────────────────────────────────────────────┐
│  CLI / entry point                                          │
│  Accepts: task description (string)                         │
│  Produces: completed session directory                      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Planner                                                    │
│  Input:  task description (string)                          │
│  Output: TaskTree with typed nodes                          │
│                                                             │
│  - Classifies task into PrimitiveType(s)                    │
│  - Generates node content via LLM (slot-filling only)       │
│  - Human checkpoint before autonomous execution begins      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Runner                                                     │
│  Input:  TaskTree                                           │
│  Output: Completed TaskTree + session artifacts             │
│                                                             │
│  - Depth-first tree traversal                               │
│  - For composition nodes: triggers decomposition            │
│  - For leaf nodes: executes step sequence                   │
│  - Routes all signals from detector                         │
│  - Controls all NodeStatus transitions                      │
└──────┬──────────────────────────┬───────────────────────────┘
       │                          │
       ▼                          ▼
┌──────────────┐        ┌─────────────────────────────────────┐
│  LLM client  │        │  Drift detector                     │
│              │        │                                     │
│  Wraps the   │        │  Input:  step output + node context  │
│  Anthropic   │        │  Output: DriftSignal list            │
│  API.        │        │          UncertaintySignal list      │
│  All calls   │        │                                     │
│  go through  │        │  - check_scope()                    │
│  here.       │        │  - check_phase()                    │
│  Context     │        │  - check_instruction_adherence()    │
│  injection   │        │  - check_schema_consistency()       │
│  happens     │        │  - check_completion_honesty()       │
│  here too.   │        │  - check_token_velocity()           │
└──────────────┘        └─────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
       ┌────────────────────┐        ┌────────────────────────┐
       │  Correction engine │        │  Notification system   │
       │                    │        │                        │
       │  Receives:         │        │  Receives:             │
       │  DriftSignal       │        │  UncertaintySignal     │
       │                    │        │                        │
       │  Builds correction │        │  Batches or interrupts │
       │  context prepended │        │  based on type policy  │
       │  to retry prompt   │        │                        │
       │                    │        │  Presents yes/no Q     │
       │  One retry max.    │        │  to human              │
       │  Escalates to      │        │                        │
       │  human on second   │        │  Handles timeout       │
       │  failure.          │        │  with defaults         │
       └────────────────────┘        └────────────────────────┘
                                                │
                                                ▼
                                   ┌────────────────────────┐
                                   │  Drift log             │
                                   │                        │
                                   │  session/drift_log     │
                                   │  .jsonl                │
                                   │                        │
                                   │  Every signal +        │
                                   │  resolution recorded   │
                                   │  for calibration       │
                                   └────────────────────────┘
```

---

## Layer responsibilities

### Schema layer (`schema/`)

Pure data definitions. No LLM calls, no file I/O, no side effects. Everything else in the system depends on these; they depend on nothing.

`primitives.py` — `PrimitiveType` enum (22 types), `NodeCategory` enum, `StepTemplate` dataclass, `GateTemplate` dataclass, step template registry per type, gate template registry per type.

`nodes.py` — `TaskNode` dataclass, `TaskTree` dataclass, `NodeStatus` enum, `StepStatus` enum, `StepRecord`, `GateResult`, `NodeSchema`, `SchemaField`.

`signals.py` — `DriftSignal`, `UncertaintySignal`, `ResolutionRecord`, `DriftType` enum, `UncertaintyType` enum, `Severity` enum, `Resolution` enum, notification policy constants.

`gates.py` — `GATE_TEMPLATES` registry mapping each `PrimitiveType` to its list of `GateTemplate` objects.

### Detector layer (`detector/`)

Receives LLM output and node context. Returns lists of signals. Never modifies node state, never calls the LLM directly (except for `llm_judge` gate checks, which are separate constrained calls).

`drift.py` — five check methods, one per drift type. Each returns `list[DriftSignal]`.

`uncertainty.py` — six check methods, one per uncertainty type. Each returns `list[UncertaintySignal]`. Includes confidence scoring.

`checks.py` — gate check implementations keyed by `check_type` string. Contains: AST-based checks (`ast_no_any`, `ast_no_io`, `ast_has_exception_handling`, `ast_no_shared_mutable_state`, `ast_no_mutations`), test runner check (`run_tests`), structural checks (`file_contains_tests`, `has_docstring`, `has_documented_exceptions`), LLM judge check (`llm_judge`).

### Runner layer (`runner/`)

Owns execution. All LLM calls, all state transitions, all signal routing happen here.

`runner.py` — main `Runner` class. `run(tree)` entry point. Depth-first traversal. Step execution loop. Signal routing to correction engine or notification system.

`context.py` — builds the LLM context for each call. Injects: current node summary, completed step outputs, global schema registry, current step prompt, correction context (on retry). Manages context window budget.

`correction.py` — receives `DriftSignal`, builds correction-prepended retry prompt, executes one retry, escalates to human on second failure.

`gates_runner.py` — executes gate checks against completed node output. Calls `checks.py` implementations. Returns list of `GateResult`.

### Planner layer (`planner/`)

Takes a raw task string and produces a `TaskTree`. The most LLM-dependent layer and therefore the most rigorously checked.

`planner.py` — main planner. Classifies task, generates root node, runs decomposition loop for composition types.

`classifier.py` — constrained LLM call that maps a task description to a `PrimitiveType`. Output is validated against the enum — if it doesn't match, it retries, not returns a guess.

`decomposer.py` — for composition nodes, generates the child node list via LLM, then type-checks each child. Untyped children are an abort condition.

### Notification layer (`notify/`)

`notifier.py` — `UncertaintyBuffer`, interrupt-vs-batch routing, terminal display formatter, timeout handler, resolution recorder.

`display.py` — terminal UI for the human review prompt.

### Session layer (`session/`)

`state.py` — serialize/deserialize `TaskTree` to JSON. Session directory structure. Resume logic.

`log.py` — drift log writer. `ResolutionRecord` to JSONL.

---

## Data flow for a single step

```
Runner selects next step
    │
    ▼
context.py builds prompt
  (node summary + step template + completed step outputs + schema registry)
    │
    ▼
LLM call → raw output string
    │
    ▼
detector/drift.py runs all five check methods
    │
    ├─ DriftSignals returned?
    │      │
    │      ├─ severity=abort → pause execution, require human review
    │      │
    │      └─ severity=block → correction.py builds retry prompt
    │                          one retry allowed
    │                          second block → escalate to human
    │
    └─ No drift signals
           │
           ▼
    detector/uncertainty.py runs all six check methods
           │
           ├─ UncertaintySignals returned?
           │      │
           │      ├─ INTERRUPT_IMMEDIATELY type → notify human now
           │      │
           │      └─ BATCH_AND_NOTIFY type → buffer
           │                                  flush at: node end,
           │                                  3 signals, or 120s
           │
           └─ No uncertainty signals
                  │
                  ▼
           Mark step COMPLETE
           Advance to next step
```

---

## Context injection strategy

Every LLM call receives a structured context block prepended to the step prompt:

```
=== SYSTEM CONTEXT ===
Session: {session_id}
Node: {node.name} [{node.id}]
Type: {node.primitive_type.value}
Depth: {node.depth} / {tree.max_depth}

=== NODE SPEC ===
Description: {node.description}
Input: {input_schema_summary}
Output: {output_schema_summary}

=== PROGRESS ===
Completed steps: {completed_step_names}
Current step: {current_step.name}
Remaining steps: {remaining_step_names}

=== GLOBAL SCHEMA REGISTRY ===
{all established data models and interfaces}

=== STEP PROMPT ===
{step_template.prompt_template with node injected}
```

The global schema registry is the primary defense against schema drift. Every `DATA_MODEL` and `INTERFACE` node, once completed, gets registered. All subsequent nodes get that registry injected so they can't accidentally diverge from established types.
