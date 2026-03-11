# State and persistence

Long sessions will span context limits, process restarts, and potentially days of work. The state system ensures nothing is lost and the session can resume from exactly where it stopped.

---

## What gets persisted

`tree.json` — the full `TaskTree` at the last save point. Contains every node, every step record with its output, every gate result, all status values, all timestamps. This is the complete session state.

`drift_log.jsonl` — append-only. Never rewritten. One JSON line per signal+resolution. Independent of `tree.json` — even if the tree is corrupted, the log survives.

`execution_log.jsonl` — append-only. One JSON line per structural event: step started/complete, drift detected, gate passed/failed, node status transitions, LLM call timings. See [logging.md](./logging.md) for the full event schema and query patterns.

`content_log/` — one markdown file per step attempt per node. Full prompt and response text at every loop level. Retry attempts get separate files so the correction context is visible alongside the model's response to it. See [logging.md](./logging.md) for file naming conventions and format.

`metadata.json` — session-level metadata: task description, session ID, start time, last save time, total node count, completed node count, python version, runner version. Used for session listing and diagnostics, not for resume.

---

## Serialization format

`TaskTree` → JSON structure:

```json
{
  "session_id": "auth-system-abc123",
  "created_at": "2026-03-10T14:00:00Z",
  "root": {
    "id": "a1b2c3d4",
    "name": "password_reset_flow",
    "primitive_type": "orchestration",
    "status": "in_progress",
    "parent_id": null,
    "dependency_ids": [],
    "created_at": "2026-03-10T14:00:01Z",
    "started_at": "2026-03-10T14:00:05Z",
    "completed_at": null,
    "retry_count": 0,
    "sub_nodes": [
      {
        "id": "e5f6a7b8",
        "name": "PasswordResetToken",
        "primitive_type": "data_model",
        "status": "complete",
        ...
        "steps": [
          {
            "step_name": "enumerate_fields",
            "status": "complete",
            "output": "Fields:\n- value: str (required)...",
            "retry_count": 0,
            "completed_at": "2026-03-10T14:02:11Z"
          },
          ...
        ],
        "gate_results": [
          {
            "gate_name": "no_any_types",
            "passed": true,
            "evidence": "No Any annotations found",
            "checked_at": "2026-03-10T14:04:30Z"
          },
          ...
        ]
      }
    ]
  }
}
```

Note: `StepTemplate` and `GateTemplate` objects are not serialized — they're reconstructed from the type registry on load. Only the runtime fields (`status`, `output`, `retry_count`, `completed_at`) are persisted in `StepRecord`. This means updated templates take effect on resume, which is the intended behavior.

---

## Save points

The state is saved after every node completion. Not after every step — that would be excessive I/O for long nodes. Not less frequently — losing a completed node's work on crash would require re-executing it.

```python
# In runner.py:
node.status = NodeStatus.COMPLETE
node.completed_at = datetime.utcnow()
self._check_parent_completion(node)
self.state_manager.save(self.tree)  # atomic write
```

Save is atomic: write to a temp file, then rename. On any platform that supports atomic renames, this ensures `tree.json` is never in a partially-written state.

---

## Resume

```python
def resume(session_id: str) -> TaskTree:
    path = f"session/{session_id}/tree.json"
    with open(path) as f:
        data = json.load(f)
    tree = deserialize_tree(data)
    return tree

# In runner.py:
def run(self) -> TaskTree:
    while not self.tree.is_complete():
        node = self.tree.next_executable()
        # next_executable() skips COMPLETE nodes automatically
        # _execute_leaf() skips steps with status COMPLETE
        ...
```

Resume is transparent. The runner doesn't know or care whether it's starting fresh or resuming. `next_executable()` returns the first pending node with met dependencies. Completed nodes and completed steps are skipped. The session continues exactly where it stopped.

---

## Context injection on resume

When resuming mid-node, the context builder needs to know what's already been done. It includes the completed step outputs in the context block sent to the LLM:

```
[COMPLETED STEPS - for context only, do not repeat this work]
enumerate_fields: [output from that step]
define_validation_rules: [output from that step]

[CURRENT STEP]
write_validation_tests: ...
```

The "for context only" label is intentional and important. Without it, the model sometimes re-does completed work rather than building on it.

Context window budget: if completed step outputs are too large to include verbatim, they're summarized. The summarizer extracts key artifacts (field lists, schema definitions, validation rules) and discards prose. Summaries are marked as summaries to avoid the model confusing them with current step content.

---

## Session listing

```
$ python -m superpowers_runner list

Session ID            Task                           Status      Last save
auth-system-abc123    user can reset password        in_progress 2026-03-10 14:31
payment-flow-def456   checkout with card payment     complete    2026-03-09 18:14
user-reg-ghi789       user registration flow         failed      2026-03-10 09:22
```

Failed sessions show which node failed and what the failure was. The human can inspect the `drift_log.jsonl` for that session to understand what went wrong before deciding whether to resume (after fixing the underlying issue) or discard.
