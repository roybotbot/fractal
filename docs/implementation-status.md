# Implementation status

## What exists

### Schema layer — complete

All core data structures are defined and the design is stable.

`schema/primitives.py` — `PrimitiveType` (22 types), `NodeCategory`, `CATEGORY_MAP`, `StepTemplate`, `GateTemplate`, `STEP_TEMPLATES` registry with full templates for: `data_model`, `transformation`, `mutation`, `interface`, `orchestration`, `query`, `validation`, `unit_test`. Generic fallback for remaining types.

`schema/gates.py` — `GATE_TEMPLATES` registry with gates for: `data_model`, `transformation`, `mutation`, `interface`, `orchestration`, `query`, `unit_test`, `validation`. Generic fallback for remaining types.

`schema/nodes.py` — `TaskNode`, `TaskTree`, `NodeStatus`, `StepStatus`, `StepRecord`, `GateResult`, `NodeSchema`, `SchemaField`. Full tree traversal, dependency resolution, context summary generation.

`schema/signals.py` — `DriftSignal`, `UncertaintySignal`, `ResolutionRecord`, all enums, notification policy constants, timeout defaults.

---

### Detector layer — complete

`detector/checks.py` — 15 gate check implementations with `CHECKS` registry and `run_check()` dispatcher.

`detector/drift.py` — `DriftDetector` with 5 check methods: `check_scope`, `check_phase`, `check_instruction_adherence`, `check_schema_consistency`, `check_completion_honesty`. Optional LLM judge for instruction adherence.

`detector/uncertainty.py` — `UncertaintyDetector` with 6 check methods: `check_ambiguous_scope`, `check_ambiguous_phase`, `check_partial_adherence`, `check_schema_near_miss`, `check_token_velocity`, `check_self_contradiction`.

### Runner layer — complete

`runner/runner.py` — `Runner` class with depth-first traversal, step execution, drift/uncertainty signal routing, gate failure handling, parent completion propagation. Protocol interfaces for detector, notifier, state manager.

`runner/context.py` — `ContextBuilder` + `SchemaRegistry`. System block assembly, node summary injection, global schema registry, step prompt injection, correction context prepend, context window budget.

`runner/gates_runner.py` — `GateRunner`. Dispatch to `checks.py` implementations by `check_type` string, collect results, determine pass/fail per gate.

### Planner layer — complete

`planner/classifier.py` — Constrained LLM classification with retry logic and enum validation. `ClassificationFailure` on exhausted retries.

`planner/decomposer.py` — Composition node decomposition with JSON parsing, type validation, dependency resolution, circular dependency detection.

`planner/planner.py` — Orchestrates classify → root creation → decompose → return `TaskTree`.

### Notification layer — complete

`notify/notifier.py` — `Notifier` with `UncertaintyBuffer`, interrupt vs batch routing, batch flush policy, timeout handler, auto-resolution safety valve.

`notify/display.py` — Terminal display for uncertainty and drift signals. Multi-signal batched display, A/B options, countdown timer.

### Session layer — complete

`session/state.py` — `StateManager` with atomic save/load for `TaskTree` → JSON round-trip. Session listing, metadata tracking.

`session/log.py` — `DriftLog`. Append-only JSONL writer for drift and uncertainty signal resolutions.

### CLI — complete

`__main__.py` — Entry point with `run`, `list`, `resume` commands. Stub LLM client for dry-run mode. Interactive terminal handler for uncertainty signals.

---

## What needs to be built

### Step templates — missing types

The following `PrimitiveType` values currently fall through to the generic template:
`config`, `aggregation`, `event_emit`, `event_handler`, `pipeline`, `router`, `cache`, `auth_guard`, `retry_policy`, `observer`, `integration_test`, `contract_test`, `fixture`

Priority order: `pipeline` and `router` (composition types, needed for the runner's decompose path), `integration_test` and `fixture` (needed by most feature tasks), the rest.

---

### runner/correction.py — not started

`CorrectionEngine`. Block signal → correction context → retry prompt → re-execution → escalation on second failure. Currently inline in `runner/runner.py._handle_block()`.

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
