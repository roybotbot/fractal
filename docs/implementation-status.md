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

### Step templates — complete

All 21 `PrimitiveType` values now have explicit step templates and gate templates. No type falls through to the generic fallback.

`schema/primitives.py` — 21 step template sets: `data_model`, `transformation`, `mutation`, `interface`, `orchestration`, `query`, `validation`, `unit_test`, `config`, `aggregation`, `event_emit`, `event_handler`, `pipeline`, `router`, `cache`, `auth_guard`, `retry_policy`, `observer`, `integration_test`, `contract_test`, `fixture`.

`schema/gates.py` — 21 gate template sets with type-specific checks. All composition types (`pipeline`, `router`, `orchestration`) have `children_have_types` gate with `abort` on failure.

### runner/correction.py — complete

`CorrectionEngine` class with: `correct_step()` (block signal → correction → retry → escalate), `build_correction_context()` (signal → correction string), `build_gate_correction()` (gate failures → correction string), `find_responsible_step()` (gate → step mapping heuristic).

### Client layer — complete

`client/llm.py` — `LLMClient` wrapping Anthropic Python SDK. Auth resolution: explicit key → ANTHROPIC_API_KEY env → stored OAuth token. OAuth tokens use Bearer auth. Supports model override per call.

`client/oauth.py` — PKCE authorization code flow via claude.ai/console.anthropic.com. Token storage in `~/.superpowers_runner/auth.json` with 600 permissions. Auto-refresh on expiry.

---

## What needs to be built

### Integration gaps

1. **Runner ↔ CorrectionEngine wiring** — `runner.py._handle_block()` has inline correction logic that duplicates `CorrectionEngine`. Should delegate to the engine instead.

2. **Runner constructor mismatch** — `runner.py.__init__` takes explicit `gate_runner` and `context_builder` params, but CLI creates Runner without them. Need a factory or default construction.

3. **Planner ↔ LLM integration** — `planner.py` uses raw LLM calls. The classify and decompose prompts work with the stub but haven't been validated against real Claude output parsing.

4. **Context window budget** — `context.py.ContextBuilder` has a `max_context_tokens` param but no actual token counting or truncation. Needs tiktoken or character-based estimation.

5. **pyproject.toml dependencies** — `anthropic` and `httpx` are used but not declared as dependencies.

### Hardening

6. **Error recovery** — Runner catches `HumanReviewRequired` but the CLI doesn't handle it gracefully (just crashes). Need try/except in `cmd_run` and `cmd_resume`.

7. **Drift log integration** — `DriftLog` exists but Runner doesn't write to it. Signals are detected but not persisted.

8. **Step template reattachment on resume** — `StateManager` deserializes steps with placeholder templates (empty prompt). On resume, the runner should reattach real templates from the type registry.

9. **`dependency_ids` type inconsistency** — `nodes.py` uses `list[str]` annotation but `set()` at runtime in some places. Session serialization converts to `set()` on load.

### Nice-to-have

10. **Streaming output** — LLMClient.call() blocks until complete. Could add streaming for long generations.

11. **Token usage tracking** — Track input/output token counts per step for cost estimation.

12. **Calibration tooling** — `drift-log.md` describes calibration queries over JSONL. Could add a `calibrate` CLI command.
