# Logging

Two logs serve different purposes and should never be merged. The execution log is a machine-readable audit trail of structural events. The content log is a human-readable record of every prompt and response at every level of the loop hierarchy. The drift log (covered separately in [drift-log.md](./drift-log.md)) is a third log specifically for signal resolution and detector calibration.

---

## The problem logging solves

A session runs for two hours autonomously. Something goes wrong at node 14. You need to know:

- What exact prompt was sent for the step that produced the bad output
- Whether drift was detected, and if so what the correction context said
- Which gate failed, and what evidence it reported
- How many retries happened and whether they helped
- What the full path through the tree was that led to this node

Without structured logging, the answer to all of these is "reconstruct it from memory and hope." With it, you open two files and have a complete picture in under a minute.

---

## Execution log

**File:** `session/{session_id}/execution_log.jsonl`

Append-only. One JSON object per line. Every structural event in the runner writes an entry. Machine-readable — designed for querying, filtering, and analysis rather than human browsing.

### Event types

```
session_started          session begins
session_complete         all nodes done
session_failed           abort-level failure, human required

node_started             node transitions to IN_PROGRESS or DECOMPOSING
node_complete            node transitions to COMPLETE
node_blocked             drift detected, entering retry
node_failed              node transitions to FAILED
node_awaiting_human      uncertainty signal pending review

step_started             step transitions to ACTIVE
step_complete            step transitions to COMPLETE
step_retrying            step entering retry after block
step_failed              step exceeded max retries

gate_started             gate check begins
gate_passed              gate check returned true
gate_failed              gate check returned false

drift_detected           DriftSignal produced by detector
uncertainty_detected     UncertaintySignal produced by detector
human_resolved           human provided resolution to uncertainty signal
timeout_resolved         timeout elapsed, default resolution applied

llm_call_started         LLM API call begins
llm_call_complete        LLM API call returns
llm_call_failed          LLM API call errored
```

### Entry schema

Every entry has these fields:

```
ts          str     ISO 8601 timestamp with milliseconds
event       str     one of the event types above
session_id  str     session identifier
node_id     str     8-char node id (absent for session-level events)
node_path   str     slash-separated tree path, e.g. "reset_flow/generate_token"
node_type   str     PrimitiveType.value
step        str     step name (absent for node/session/gate events)
attempt     int     retry attempt number, 1-indexed (absent where not applicable)
```

Plus event-specific fields:

```
# step_complete, node_complete
duration_ms     int     wall clock time for the step or node
tokens_in       int     prompt tokens consumed
tokens_out      int     completion tokens produced

# gate_started, gate_passed, gate_failed
gate_name       str     gate template name
check_type      str     check implementation key
evidence        str     what the check found (on failure, what was wrong)

# drift_detected
drift_type      str     DriftType.value
severity        str     Severity.value
signal_id       str     links to drift_log.jsonl entry

# uncertainty_detected
uncertainty_type  str   UncertaintyType.value
confidence        float
signal_id         str   links to drift_log.jsonl entry

# llm_call_started, llm_call_complete
call_purpose    str     "step_execution" | "classification" | "llm_judge" | "correction"
model           str     model identifier

# node_failed, session_failed
reason          str     short description of why
signal_ids      list    drift/uncertainty signal ids that caused failure
```

### Example entries

```jsonl
{"ts":"2026-03-10T14:23:00.000Z","event":"session_started","session_id":"auth-abc123","node_id":null,"node_path":null,"node_type":null,"step":null,"attempt":null}
{"ts":"2026-03-10T14:23:01.112Z","event":"node_started","session_id":"auth-abc123","node_id":"a1b2","node_path":"password_reset_flow","node_type":"orchestration","step":null,"attempt":null}
{"ts":"2026-03-10T14:23:01.200Z","event":"node_started","session_id":"auth-abc123","node_id":"c3d4","node_path":"password_reset_flow/PasswordResetToken","node_type":"data_model","step":null,"attempt":null}
{"ts":"2026-03-10T14:23:01.210Z","event":"step_started","session_id":"auth-abc123","node_id":"c3d4","node_path":"password_reset_flow/PasswordResetToken","node_type":"data_model","step":"enumerate_fields","attempt":1}
{"ts":"2026-03-10T14:23:01.220Z","event":"llm_call_started","session_id":"auth-abc123","node_id":"c3d4","node_path":"password_reset_flow/PasswordResetToken","node_type":"data_model","step":"enumerate_fields","attempt":1,"call_purpose":"step_execution","model":"claude-sonnet-4-20250514"}
{"ts":"2026-03-10T14:23:03.891Z","event":"llm_call_complete","session_id":"auth-abc123","node_id":"c3d4","node_path":"password_reset_flow/PasswordResetToken","node_type":"data_model","step":"enumerate_fields","attempt":1,"call_purpose":"step_execution","model":"claude-sonnet-4-20250514","tokens_in":812,"tokens_out":298,"duration_ms":2671}
{"ts":"2026-03-10T14:23:03.920Z","event":"step_complete","session_id":"auth-abc123","node_id":"c3d4","node_path":"password_reset_flow/PasswordResetToken","node_type":"data_model","step":"enumerate_fields","attempt":1,"duration_ms":2710,"tokens_in":812,"tokens_out":298}
{"ts":"2026-03-10T14:31:44.001Z","event":"drift_detected","session_id":"auth-abc123","node_id":"e5f6","node_path":"password_reset_flow/generate_reset_token","node_type":"transformation","step":"implement_minimal","attempt":1,"drift_type":"phase","severity":"block","signal_id":"b7c1d3e2"}
{"ts":"2026-03-10T14:31:44.050Z","event":"step_retrying","session_id":"auth-abc123","node_id":"e5f6","node_path":"password_reset_flow/generate_reset_token","node_type":"transformation","step":"implement_minimal","attempt":2}
{"ts":"2026-03-10T14:31:47.300Z","event":"step_complete","session_id":"auth-abc123","node_id":"e5f6","node_path":"password_reset_flow/generate_reset_token","node_type":"transformation","step":"implement_minimal","attempt":2,"duration_ms":3250,"tokens_in":1204,"tokens_out":445}
```

The `node_path` makes the hierarchy immediately readable. You can grep for a specific node path and see its entire execution history as a sequence of events.

---

## Content log

**Directory:** `session/{session_id}/content_log/`

One subdirectory per node, one markdown file per step attempt. Human-readable. Designed for debugging — you open the file for the step that went wrong and see exactly what was sent and what came back.

### Directory structure

```
content_log/
├─ a1b2_password_reset_flow/
│   └─ step_01_enumerate_children.md
├─ c3d4_PasswordResetToken/
│   ├─ step_01_enumerate_fields.md
│   ├─ step_02_define_validation_rules.md
│   ├─ step_03_write_validation_tests.md
│   ├─ step_04_implement_model.md
│   └─ step_05_document_invariants.md
├─ e5f6_generate_reset_token/
│   ├─ step_01_define_input_schema.md
│   ├─ step_02_define_output_schema.md
│   ├─ step_03_enumerate_edge_cases.md
│   ├─ step_04_write_failing_tests.md
│   ├─ step_05_implement_minimal_attempt1.md
│   ├─ step_05_implement_minimal_attempt2.md   ← retry gets its own file
│   └─ step_06_refactor.md
└─ ...
```

Naming conventions:
- Directory: `{node_id}_{node_name}/` — id first for stable sorting even if names are long
- File: `step_{N:02d}_{step_name}.md` — zero-padded index preserves step order in filesystem listings
- Retry: `step_{N:02d}_{step_name}_attempt{M}.md` — attempt 1 is the original, attempt 2+ are retries

### File format

```markdown
# step: implement_minimal
# node: generate_reset_token [e5f6]
# path: password_reset_flow/generate_reset_token
# node_type: transformation
# attempt: 1
# started: 2026-03-10T14:29:11.000Z
# completed: 2026-03-10T14:29:14.890Z
# duration_ms: 3890
# tokens_in: 1021
# tokens_out: 387
# outcome: drift_detected (phase/block)

---

## Prompt

=== SYSTEM CONTEXT ===
You are operating inside a structured task execution system.
Complete ONLY the current step. Do not proceed to subsequent steps.
Do not implement work that belongs to a different node.

=== NODE CONTEXT ===
NODE: generate_reset_token
TYPE: transformation
...

=== STEP PROMPT ===
For node 'generate_reset_token': implement the transformation.
Minimal code to make all tests pass.
No side effects. No I/O. Pure function only.
Do not add anything not required by the tests.

---

## Response

[full LLM response text]

---

## Signals

### Drift detected

type: phase
severity: block
evidence: Implementation introduced requests.get() call on line 14.
  This node is typed transformation — no I/O permitted.
correction: Remove requests.get(). Pure function only.
  No imports from requests, httpx, aiohttp, os, sys, or open().
```

For retry files, the correction context is visible in the prompt section:

```markdown
# step: implement_minimal
# attempt: 2
# outcome: complete

---

## Prompt

=== CORRECTION CONTEXT ===
DRIFT DETECTED: phase
EVIDENCE: Implementation introduced requests.get() call on line 14.
PROBLEMATIC OUTPUT: [excerpt]
CORRECTION REQUIRED: Remove requests.get(). Pure function only.
──────────────────────────────────────────────────────────────
Now re-attempt this step only:

=== SYSTEM CONTEXT ===
...
```

The retry file shows exactly what correction was injected. If the retry also failed, you see exactly what the model was told and what it produced — which tells you whether the correction prompt itself was the problem.

---

## The `node_path` field

`node_path` is the single most important field in both logs. It encodes where in the loop hierarchy an event happened.

```
password_reset_flow                              ← depth 0, root orchestration
password_reset_flow/generate_reset_token         ← depth 1, leaf node
password_reset_flow/generate_reset_token         ← same node, different steps
```

If the system ever has nested orchestrations — an orchestration containing a pipeline containing leaf nodes — the path expresses that clearly:

```
checkout_flow/payment_stage/validate_card        ← depth 2
checkout_flow/payment_stage/charge_card          ← depth 2, sibling
checkout_flow/fulfillment_stage/reserve_stock    ← depth 2, different parent
```

Computed once at node registration:

```python
def compute_node_path(tree: TaskTree, node: TaskNode) -> str:
    path = []
    current = node
    while current is not None:
        path.append(current.name)
        parent_id = current.parent_id
        current = tree.get(parent_id) if parent_id else None
    return "/".join(reversed(path))
```

Stored as `node.path` at registration. Never recomputed. Every log write reads `node.path` directly.

---

## Logger interface

All logging goes through a single `ExecutionLogger` instance passed into the runner at construction. This keeps log writes out of the runner's core logic — the runner calls `self.logger.step_started(node, step)` and the logger handles formatting, file writes, and both log targets simultaneously.

```python
class ExecutionLogger:
    def __init__(self, session_dir: Path): ...

    # Session events
    def session_started(self, session_id: str, task: str): ...
    def session_complete(self, session_id: str, duration_ms: int): ...
    def session_failed(self, session_id: str, reason: str, signal_ids: list[str]): ...

    # Node events
    def node_started(self, node: TaskNode): ...
    def node_complete(self, node: TaskNode, duration_ms: int): ...
    def node_blocked(self, node: TaskNode, signals: list[DriftSignal]): ...
    def node_failed(self, node: TaskNode, reason: str, signal_ids: list[str]): ...
    def node_awaiting_human(self, node: TaskNode, signals: list[UncertaintySignal]): ...

    # Step events
    def step_started(self, node: TaskNode, step: StepRecord): ...
    def step_complete(self, node: TaskNode, step: StepRecord, tokens_in: int, tokens_out: int, duration_ms: int): ...
    def step_retrying(self, node: TaskNode, step: StepRecord): ...
    def step_failed(self, node: TaskNode, step: StepRecord): ...

    # Gate events
    def gate_started(self, node: TaskNode, gate: GateTemplate): ...
    def gate_passed(self, node: TaskNode, gate: GateTemplate, evidence: str): ...
    def gate_failed(self, node: TaskNode, gate: GateTemplate, evidence: str): ...

    # Signal events
    def drift_detected(self, node: TaskNode, step: StepRecord, signal: DriftSignal): ...
    def uncertainty_detected(self, node: TaskNode, step: StepRecord, signal: UncertaintySignal): ...
    def human_resolved(self, signal: UncertaintySignal, resolution: Resolution): ...
    def timeout_resolved(self, signal: UncertaintySignal, resolution: Resolution): ...

    # LLM call events
    def llm_call_started(self, node: TaskNode, step: StepRecord, purpose: str): ...
    def llm_call_complete(self, node: TaskNode, step: StepRecord, purpose: str, tokens_in: int, tokens_out: int, duration_ms: int): ...
    def llm_call_failed(self, node: TaskNode, step: StepRecord, purpose: str, error: str): ...

    # Content log — called by context builder after prompt assembly,
    # and by runner after response received
    def log_prompt(self, node: TaskNode, step: StepRecord, attempt: int, prompt: str): ...
    def log_response(self, node: TaskNode, step: StepRecord, attempt: int, response: str, signals: list): ...
```

Two internal methods handle the actual writes:

```python
def _write_execution_event(self, event: str, node: TaskNode | None, **kwargs):
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event,
        "session_id": self.session_id,
        "node_id": node.id if node else None,
        "node_path": node.path if node else None,
        "node_type": node.primitive_type.value if node else None,
        **kwargs,
    }
    self._execution_log.write(json.dumps(entry) + "\n")
    self._execution_log.flush()  # flush after every write — don't lose events on crash

def _write_content_file(self, node: TaskNode, step: StepRecord, attempt: int, content: str):
    dir_name = f"{node.id}_{node.name}"
    step_index = node.steps.index(step) + 1
    if attempt == 1:
        filename = f"step_{step_index:02d}_{step.template.name}.md"
    else:
        filename = f"step_{step_index:02d}_{step.template.name}_attempt{attempt}.md"
    path = self.content_log_dir / dir_name / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
```

`flush()` after every execution log write is non-negotiable. If the process dies, the last events before the crash must be in the log. Buffered writes lose exactly the events you need most.

---

## Querying the execution log

Common queries against `execution_log.jsonl`:

**All events for a specific node:**
```bash
jq 'select(.node_path == "password_reset_flow/generate_reset_token")' execution_log.jsonl
```

**All drift events in the session:**
```bash
jq 'select(.event == "drift_detected")' execution_log.jsonl
```

**Step durations sorted slowest first:**
```bash
jq 'select(.event == "step_complete") | {step, node_path, duration_ms}' execution_log.jsonl \
  | jq -s 'sort_by(-.duration_ms)'
```

**Total token usage:**
```bash
jq 'select(.event == "llm_call_complete") | .tokens_in + .tokens_out' execution_log.jsonl \
  | jq -s 'add'
```

**Retry rate by step name:**
```bash
jq 'select(.event == "step_retrying") | .step' execution_log.jsonl | sort | uniq -c | sort -rn
```

**Full timeline for a failed node:**
```bash
jq 'select(.node_id == "e5f6")' execution_log.jsonl
```

---

## Module location

`session/logger.py` — `ExecutionLogger` class. Both log targets. All event methods. Content file writer.

Added to module dependency rules:

```
logger  ← depends on schema (for node/step/gate/signal types)
runner  ← depends on schema, detector, checks, logger
notify  ← depends on schema, logger
```

The logger has no dependency on the runner — it's passed in, not imported. This keeps the dependency graph clean and makes the logger independently testable.
