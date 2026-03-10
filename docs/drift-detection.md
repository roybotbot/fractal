# Drift detection

Drift is not random. LLMs fail in specific, repeatable patterns when operating over long tasks. This system names each pattern, detects it via a dedicated check method, and routes it to the appropriate correction.

---

## The five drift types

### Type 1: scope drift

The model is working on the right kind of thing but for the wrong node. Code appears in the output that belongs to a different node — usually an adjacent or parent node the model has seen in context.

**Symptom:** output contains symbols, types, or logic not defined in the current node's spec.

**Cause:** context dilution. As the session grows, the model loses track of which specific node it's currently inside and begins free-forming based on its understanding of the overall task.

**Detection:** compare symbols introduced in output against `node.input_schema`, `node.output_schema`, and the current step's `expected_artifacts`. Anything that appears in the output but not in the spec is a scope candidate. Confidence scoring: how far is the new symbol from anything in the node spec?

**Severity:** warn if the symbol is structurally compatible with the node spec. Block if it introduces new behavior. Abort if it rewrites the node spec itself.

**Correction prompt injection:**
```
DRIFT DETECTED: scope
EVIDENCE: Output introduced 'TokenStore' class. This node's output spec is:
  token: ResetToken
  No class definitions are in scope for this node.
PROBLEMATIC OUTPUT: [excerpt]
CORRECTION REQUIRED: Remove 'TokenStore'. Implement only what the node spec defines.
Re-attempt this step only:
```

---

### Type 2: phase drift

The model is doing step N+2 work while supposedly completing step N. Typically manifests as implementation code appearing during a planning or test-writing step.

**Symptom:** output contains artifacts from a later step (listed in `StepTemplate.forbidden_artifacts`).

**Cause:** pattern matching. If the model recognizes the task shape, it wants to jump to the solution. The step sequence feels like bureaucracy to it.

**Detection:** check output content against the current step's `forbidden_artifacts` list. Each step template explicitly defines what must not appear yet. For example, `write_failing_tests` forbids `implementation_code`. `enumerate_edge_cases` forbids both `test_code` and `implementation_code`.

**Severity:** warn if the premature artifact is supplementary. Block if it would replace a required future step. The system strips the premature artifact from the step output before advancing.

**Correction:**
```
DRIFT DETECTED: phase
EVIDENCE: Implementation code found during 'enumerate_edge_cases' step.
  Implementation belongs in step 'implement_minimal', which comes later.
CORRECTION REQUIRED: Remove all implementation code. This step produces only
  a list of edge cases. Do not write tests or code.
```

---

### Type 3: instruction drift

The model stops responding to the sub-prompt and produces output that's contextually plausible but doesn't address what was asked. The output looks reasonable but isn't actually answering the question.

**Symptom:** the step's `expected_artifacts` are absent or only tangentially addressed.

**Cause:** long context, competing signals, the model optimizing for "helpfulness" over compliance. Instruction drift correlates with context length — it gets worse as sessions grow.

**Detection:** LLM judge call, separate from the main execution call. Prompt: "The required step was: [step prompt]. Did the output explicitly address each required artifact? Answer per artifact: [artifact_name]: addressed / not addressed / partially addressed."

**Severity:** always block. Instruction drift is the most common failure mode and the one most likely to silently compound. Retrying with explicit "you did not address: [list]" prepended is usually enough to fix it.

**Correction:**
```
DRIFT DETECTED: instruction
EVIDENCE: Step 'enumerate_edge_cases' required: edge_case_list
  The output discussed the transformation's purpose but did not list edge cases.
CORRECTION REQUIRED: Address the following artifacts explicitly:
  - edge_case_list: list every edge case for this transformation
Re-attempt this step only:
```

---

### Type 4: schema drift

The model deviates from established data model types mid-implementation. A field that was `user_id: str` becomes `user_id: int`. A type named `ResetToken` gets used as `PasswordResetToken`. The established schema is in the global registry but the model ignores it.

**Symptom:** types used in output don't match the established type definitions in the global schema registry.

**Cause:** local optimization. The model sees the current context and makes a decision that looks correct locally but breaks global consistency.

**Detection:** AST-based. Extract all type annotations and class references from the output code. Compare against the global schema registry. Flag: name mismatches (different name for a structurally identical type), type substitutions (different type for the same field), new fields added to established models.

**Severity:** warn on minor naming inconsistency. Block on type substitution. Abort on new fields added to established models — that's a planning error, not an implementation error, and needs human judgment.

**Correction:**
```
DRIFT DETECTED: schema
EVIDENCE: Field 'user_id' typed as 'int' in this output.
  Global schema registry defines: User.id: str (see registry entry: User)
CORRECTION REQUIRED: Use 'str' for user_id, consistent with the global schema.
```

---

### Type 5: completion drift

The model declares a node complete — explicitly or implicitly — when gate checks are failing. Often manifests as "the implementation is complete" or "all tests pass" in the output when they demonstrably don't.

**Symptom:** completion signal in output while `GateResult` list contains failures.

**Cause:** optimizing for approval. The model has learned that saying "done" ends the loop. This is the most corrosive drift type because it creates false confidence.

**Detection:** hard check. If the model output contains completion language AND any gate is failing, this is a completion drift signal. No confidence scoring — it's binary. Gate failure is objective.

**Severity:** always block. Never let completion drift pass with a warning. List every failing gate explicitly in the correction.

**Correction:**
```
DRIFT DETECTED: completion
EVIDENCE: Output claims implementation is complete. The following gates are failing:
  - tests_exist_and_pass: no test file found
  - no_io_calls: requests.get() found in transformation (line 14)
CORRECTION REQUIRED: Do not declare completion. Address each failing gate:
  1. Write tests
  2. Remove I/O call from transformation
```

---

## Detector interface

```python
class DriftDetector:
    def check_scope(
        self,
        node: TaskNode,
        output: str,
    ) -> list[DriftSignal]: ...

    def check_phase(
        self,
        step: StepRecord,
        output: str,
        subsequent_steps: list[StepRecord],
    ) -> list[DriftSignal]: ...

    def check_instruction_adherence(
        self,
        step: StepRecord,
        output: str,
    ) -> list[DriftSignal]: ...

    def check_schema_consistency(
        self,
        global_registry: dict[str, DataModel],
        output: str,
    ) -> list[DriftSignal]: ...

    def check_completion_honesty(
        self,
        node: TaskNode,
        output: str,
        gate_results: list[GateResult],
    ) -> list[DriftSignal]: ...

    def check_all(
        self,
        node: TaskNode,
        step: StepRecord,
        output: str,
        gate_results: list[GateResult] | None = None,
    ) -> list[DriftSignal]:
        # Runs all applicable checks for the given step
        # gate_results only passed when checking for completion drift
```

---

## Severity routing

| Severity | Runner behavior |
|---|---|
| `warn` | Log to drift log. Continue. |
| `block` | Halt step. Build correction context. One retry. If second block: escalate to abort. |
| `abort` | Halt node. Require human review. Session pauses. |

The `max_retries` on `StepRecord` defaults to 1. A step that blocks twice becomes an abort automatically. This prevents the system from spinning in a correction loop when something is genuinely wrong.

---

## What drift detection can't catch

Drift detection is not magic. It has known blind spots:

**Plausible-looking wrong answers.** If the model writes code that compiles, passes tests, and matches the spec structurally but is semantically incorrect — logic bugs, off-by-one errors, wrong algorithm choice — the detector won't catch it. That's what code review and integration tests are for.

**Hallucinated test passes.** If the model says "tests pass" and the runner doesn't actually execute them, the detector believes it. The `run_tests` gate check closes this — but only if the gate runner actually executes the test suite.

**Schema drift in comments or docstrings.** AST analysis catches type annotations. It doesn't parse English sentences in comments that contradict the code.

These are known limitations, not defects. The system's goal is to catch the drift patterns that actually cause autonomous sessions to fail, not to achieve perfect semantic correctness.
