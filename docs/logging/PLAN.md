# Logging implementation plan

Spec: `docs/logging/logging.md`
Module: `session/logger.py`
Tests: `tests/test_logger.py`
Dependencies: schema layer only

---

## Constraint

Schema layer is finalized — `TaskNode` cannot be modified. The `node_path` field specified in the logging doc will be tracked inside `ExecutionLogger` via an internal `dict[str, str]` mapping `node_id → path`.

---

## Step 1: `compute_node_path` utility

A standalone function. Takes a `TaskTree` and `TaskNode`, walks parent_id chain, returns slash-separated path string. Called once per node at registration time.

```python
def compute_node_path(tree: TaskTree, node: TaskNode) -> str
```

Since `TaskNode` has no `path` attribute, the logger maintains `_node_paths: dict[str, str]`.

---

## Step 2: `ExecutionLogger` class — execution log target

**File:** `session/{session_id}/execution_log.jsonl`

Core method: `_write_execution_event(event, node, **kwargs)`
- Builds dict with common fields: `ts`, `event`, `session_id`, `node_id`, `node_path`, `node_type`, `step`, `attempt`
- Writes one JSON line
- `flush()` after every write — non-negotiable per spec

23 event types organized as:
- Session: `session_started`, `session_complete`, `session_failed`
- Node: `node_started`, `node_complete`, `node_blocked`, `node_failed`, `node_awaiting_human`
- Step: `step_started`, `step_complete`, `step_retrying`, `step_failed`
- Gate: `gate_started`, `gate_passed`, `gate_failed`
- Signal: `drift_detected`, `uncertainty_detected`, `human_resolved`, `timeout_resolved`
- LLM: `llm_call_started`, `llm_call_complete`, `llm_call_failed`

Each method maps to exactly one event type. No conditionals inside event methods.

---

## Step 3: `ExecutionLogger` — content log target

**Directory:** `session/{session_id}/content_log/`

Core methods:
- `log_prompt(node, step, attempt, prompt)` — stores prompt text, writes header
- `log_response(node, step, attempt, response, signals)` — appends response and signal section to the file

Internal method: `_write_content_file(node, step, attempt, content)`
- Directory: `{node_id}_{node_name}/`
- File: `step_{N:02d}_{step_name}.md` (attempt 1) or `step_{N:02d}_{step_name}_attempt{M}.md`
- Header format matches spec: `# step:`, `# node:`, `# path:`, `# node_type:`, `# attempt:`, etc.
- `mkdir(parents=True, exist_ok=True)` for node directories

---

## Step 4: `register_node` method

Called by the runner when a node is first encountered. Computes `node_path` via `compute_node_path()` and stores it.

```python
def register_node(self, tree: TaskTree, node: TaskNode) -> None
```

---

## Step 5: Wire into Runner

Add optional `logger: ExecutionLogger | None = None` parameter to `Runner.__init__`.

Insert logging calls at every state transition point:
- `_execute_node`: `logger.node_started(node)`
- `_execute_step`: `logger.step_started(node, step)` at start, `logger.step_complete(...)` at end
- `_route_drift_signals`: `logger.drift_detected(node, step, signal)` for each signal
- `_handle_block`: `logger.step_retrying(node, step)`
- `_execute_leaf` after gates: `logger.gate_passed/failed(node, gate, evidence)`
- `_check_parent_completion`: `logger.node_complete(node, duration_ms)`
- `run()`: `logger.session_started(...)` at top, `logger.session_complete(...)` at end

The runner must NOT depend on the logger being present — all calls guarded with `if self.logger:`.

---

## Step 6: Wire into CLI

`cmd_run` creates `ExecutionLogger` and passes to `Runner`.
`cmd_resume` reopens the existing session's logger (append mode).

---

## Test plan (test_logger.py)

1. **compute_node_path** — root path, child path, grandchild path, orphan node
2. **Execution log writes** — session_started writes JSONL, event fields correct, flush called
3. **Multiple events** — events append, don't overwrite
4. **Content log** — correct directory structure, correct filename, retry filename
5. **Content file format** — header fields present, prompt/response sections
6. **register_node** — path cached, subsequent calls use cache
7. **Event-specific fields** — drift_detected has drift_type/severity/signal_id, gate_passed has gate_name/check_type/evidence
8. **Null guards** — node=None events (session-level) work correctly
9. **Round-trip** — write events, read back with `json.loads`, verify structure
10. **NullLogger** — optional NullLogger that satisfies the Protocol but writes nothing

Target: 30-40 tests.

---

## Build order

1. `compute_node_path()` + tests
2. `ExecutionLogger` execution log methods + tests
3. `ExecutionLogger` content log methods + tests
4. Wire into Runner (modify `runner.py`)
5. Wire into CLI (modify `__main__.py`)
6. Full suite green
