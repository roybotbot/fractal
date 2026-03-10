# Implementation status

## What exists

### Schema layer — complete

All core data structures are defined and the design is stable.

`schema/primitives.py` — `PrimitiveType` (22 types), `NodeCategory`, `CATEGORY_MAP`, `StepTemplate`, `GateTemplate`, `STEP_TEMPLATES` registry with full templates for: `data_model`, `transformation`, `mutation`, `interface`, `orchestration`, `query`, `validation`, `unit_test`. Generic fallback for remaining types.

`schema/gates.py` — `GATE_TEMPLATES` registry with gates for: `data_model`, `transformation`, `mutation`, `interface`, `orchestration`, `query`, `unit_test`, `validation`. Generic fallback for remaining types.

`schema/nodes.py` — `TaskNode`, `TaskTree`, `NodeStatus`, `StepStatus`, `StepRecord`, `GateResult`, `NodeSchema`, `SchemaField`. Full tree traversal, dependency resolution, context summary generation.

`schema/signals.py` — `DriftSignal`, `UncertaintySignal`, `ResolutionRecord`, all enums, notification policy constants, timeout defaults.

---

## What needs to be built

### Step templates — missing types

The following `PrimitiveType` values currently fall through to the generic template:
`config`, `aggregation`, `event_emit`, `event_handler`, `pipeline`, `router`, `cache`, `auth_guard`, `retry_policy`, `observer`, `integration_test`, `contract_test`, `fixture`

Priority order: `pipeline` and `router` (composition types, needed for the runner's decompose path), `integration_test` and `fixture` (needed by most feature tasks), the rest.

---

### Detector layer — not started

`detector/drift.py` — five check methods:

```
check_scope()            — symbol comparison against node spec
check_phase()            — forbidden artifact detection in step output
check_instruction_adherence() — LLM judge: did output address sub-prompt?
check_schema_consistency()   — AST type comparison against global registry
check_completion_honesty()   — gate result vs completion claim check
```

`detector/uncertainty.py` — six check methods, each with confidence scoring:

```
check_ambiguous_scope()     — new symbol that might be a helper
check_ambiguous_phase()     — code-like content in planning step
check_partial_adherence()   — oblique sub-prompt response
check_schema_near_miss()    — structurally compatible, differently named type
check_token_velocity()      — suspiciously low token count
check_self_contradiction()  — assertion in prose not supported by code
```

`detector/checks.py` — gate check implementations:

```
ast_no_any()               — walk AST for Any annotations
ast_no_io()                — walk AST for I/O module imports/calls
ast_has_exception_handling() — verify try/except presence
ast_no_mutations()         — verify no write operations
ast_no_shared_mutable_state() — verify test isolation
run_tests()                — execute test suite, return pass/fail + output
test_count_minimum()       — count test functions, compare to minimum
test_covers_exceptions()   — verify exception path tests exist
test_covers_partial_failure() — verify partial failure integration tests
file_contains_tests()      — structural check for test file
has_docstring()            — verify primary class/function has docstring
has_documented_exceptions() — verify exception annotations in interface
has_rollback_documentation() — verify rollback behavior documented
children_have_types()      — verify all children have valid primitive_type
llm_judge()               — constrained LLM call for semantic checks
```

---

### Runner layer — not started

`runner/runner.py` — main `Runner` class. Depth-first traversal, node execution dispatch, signal routing, parent completion propagation.

`runner/context.py` — `ContextBuilder`. System block assembly, node summary injection, global schema registry injection, step prompt injection, correction context prepend, context window budget management.

`runner/correction.py` — `CorrectionEngine`. Block signal → correction context → retry prompt → re-execution → escalation on second failure.

`runner/gates_runner.py` — `GateRunner`. Dispatch to `checks.py` implementations by `check_type` string, collect results, determine pass/fail per gate.

---

### Planner layer — not started

`planner/classifier.py` — constrained classification call. Returns `PrimitiveType` or raises `ClassificationFailure` after 3 retries.

`planner/decomposer.py` — composition node → JSON child list → type-validated `TaskNode` list. Dependency validation.

`planner/planner.py` — orchestrates classify → root creation → decompose → human checkpoint → return `TaskTree`.

---

### Notification layer — not started

`notify/notifier.py` — `UncertaintyBuffer`, interrupt vs batch routing, batch flush policy (end of node, 3 signals, 120s), timeout handler, auto-resolution safety valve.

`notify/display.py` — terminal display for human review. Multi-signal batched display, A/B selection, show-more option, countdown timer.

---

### Session layer — not started

`session/state.py` — `TaskTree` → JSON serialization/deserialization. Session directory creation, save, load, resume.

`session/log.py` — `DriftLog`. `ResolutionRecord` → JSONL writer. Append-only.

---

### CLI — not started

Entry point. Accepts task description. Instantiates planner, runner, notifier. Handles `HumanReviewRequired` exceptions. Displays session summary on completion.

---

## Next build order

1. `detector/checks.py` — the AST-based checks are straightforward Python and provide immediate value for testing gate logic.
2. `runner/gates_runner.py` — can be tested against hand-constructed nodes with the schema layer.
3. `runner/context.py` — testable without live LLM calls.
4. `runner/runner.py` — the core execution loop. Can be tested with a mock LLM client.
5. `planner/classifier.py` and `planner/decomposer.py` — requires live LLM calls, test with real API.
6. `detector/drift.py` and `detector/uncertainty.py` — requires LLM judge for some checks.
7. `notify/` — relatively self-contained, can be built and tested independently.
8. `session/` — straightforward serialization, build last.
9. CLI — wire everything together.
