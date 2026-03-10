# Uncertainty and human-in-the-loop

The drift detector operates in three states, not two. Clean and drifted are both confident states. Uncertain is a distinct third state that requires different handling.

Silently resolving uncertainty — either by proceeding or blocking — is wrong in both directions. Proceeding when something is genuinely wrong accumulates debt. Blocking when nothing's actually wrong destroys the value of autonomous execution.

---

## The six uncertainty types

### `ambiguous_scope`

A new symbol appeared in the output that wasn't in the node spec, but it might be a legitimate local helper rather than scope creep. The detector can identify the symbol exists. It can't determine intent without knowing the task context.

**Question template:** "Output introduced `{symbol}` which is not in the node spec. Is this a legitimate local helper (A) or scope drift that should be removed (B)?"

**Default on timeout:** proceed (logged)

### `ambiguous_phase`

Output contains what looks like implementation code during a planning step, but it might be pseudocode used for illustration rather than actual implementation.

**Question template:** "Code block found during `{step_name}` step. Is this pseudocode for illustration purposes (A) or premature implementation that should be removed (B)?"

**Default on timeout:** proceed (logged)

### `partial_adherence`

The output addressed the step's sub-prompt but obliquely. Not missing entirely (that would be instruction drift), not fully addressed (that would be clean). The model gestured at the requirement without completing it.

**Question template:** "Step `{step_name}` required `{artifact}`. The output partially addressed this. Is the coverage sufficient to proceed (A) or should the step be retried with more explicit requirements (B)?"

**Default on timeout:** retry (the cost of proceeding with incomplete artifacts compounds)

### `schema_near_miss`

A type in the output is structurally compatible with an established schema entry but has a different name. Could be an intentional rename, could be an accidental parallel type that will diverge.

**Question template:** "Output uses `{new_type}` which is structurally identical to established type `{existing_type}`. Is this an intentional rename (A) or should the established name be used (B)?"

**Default on timeout:** retry (naming consistency matters for downstream nodes)

**Note:** this type fires immediately, doesn't buffer. Downstream nodes building on the wrong name is expensive to unwind.

### `suspiciously_fast`

The step completed in significantly fewer tokens than similar steps in this session or historically. Might be a genuinely simple case. Might be the model cutting corners.

Threshold: completion at less than 40% of the rolling average token count for this step type.

**Question template:** "Step `{step_name}` completed in {token_count} tokens (avg: {avg_tokens}). Edge cases defined: {edge_case_count}. Is this node genuinely simple (A) or did it skip required work (B)?"

**Default on timeout:** retry

### `self_contradiction`

The output contains a statement asserting something is handled, but the code doesn't show it. Classic pattern: "error handling is implemented" in prose, no try/except in the code block.

**Question template:** "Output states `{assertion}` but the code doesn't demonstrate it. Is the assertion accurate (A) or should the step be retried (B)?"

**Default on timeout:** escalate (self-contradiction is a red flag for hallucinated completeness)

**Note:** this type fires immediately, doesn't buffer.

---

## Signal schema

```python
@dataclass
class UncertaintySignal:
    id: str                          # 8-char UUID prefix
    uncertainty_type: UncertaintyType
    node_id: str
    step_name: str
    confidence: float                # 0.0–1.0, how likely this is a real problem
    evidence: str                    # specific excerpt, concrete not vague
    output_excerpt: str              # the relevant section of LLM output
    question: str                    # single specific yes/no question for human
    option_a: str                    # typically "proceed" or "this is fine"
    option_b: str                    # typically "retry" or "this is wrong"
    default_resolution: Resolution   # applied if human doesn't respond in time
    timeout_seconds: int             # default 300
    relevant_node_spec: str          # context to show alongside the question
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by: "human" | "timeout" | None
    resolution: Resolution | None
    human_note: str                  # optional free text from human
```

The `question` field is the most important design constraint. It must be:
- A single question
- Answerable with A or B
- Specific about what was observed, not vague about what might be wrong
- Short enough to read in 10 seconds

Bad: "Does this output look correct to you?"
Good: "Output introduced `TokenStore` class not in node spec. Is this a local helper (A) or scope creep (B)?"

---

## Notification policy

### Interrupt immediately

These two types can't buffer because downstream nodes will build on a wrong foundation:

`schema_near_miss` — if the wrong type name propagates to 3 more nodes, renaming it later touches everything.

`self_contradiction` — usually signals the model hallucinated completion. Proceeding compounds the problem.

### Batch and notify

These four types can wait for a natural checkpoint:

`ambiguous_scope`, `ambiguous_phase`, `partial_adherence`, `suspiciously_fast`

Batch flushes at whichever comes first:
- End of current node
- 3 signals accumulated
- 120 seconds elapsed

### Auto-resolution safety valve

If the system auto-resolves `MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT` (default: 5) uncertainty signals via timeout without any human response, it forces an interrupt regardless of signal type. Either the human is gone or the uncertainty rate is abnormally high. Either way, something needs a human's eye.

---

## Terminal display

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HUMAN REVIEW NEEDED  [2 signals]
Session: auth-system-abc123
Node: generate_reset_token (transformation)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1/2] AMBIGUOUS SCOPE  (confidence: 0.41)
Step: implement_minimal

Output introduced 'TokenStore' — not in node spec.
Node spec outputs: token: ResetToken

> Is TokenStore a legitimate local helper (A) or scope drift (B)?
  A / B / show-more: _

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[2/2] SUSPICIOUSLY FAST  (confidence: 0.38)
Step: enumerate_edge_cases

This step completed in 180 tokens. Session avg: 620 tokens.
Edge cases listed: 1 (null input).

> Is this node genuinely simple (A) or did it skip edge cases (B)?
  A / B / show-more: _

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Timeout in 5:00  |  No response: proceed on [1], retry on [2]
```

`show-more` reveals the full output excerpt and node spec context. For humans who want more than 10 seconds on a decision.

---

## Resolution handling

Each signal gets a `Resolution`:

`PROCEED` — mark signal resolved, continue execution as-is.

`RETRY` — re-run the step with the uncertainty signal's `evidence` prepended as context: "A human reviewer flagged this: [evidence]. [question context]. Please address this in your response."

`ESCALATE` — treat as a `DriftSignal` with severity `block`. Routes to the correction engine.

---

## Timeout behavior per type

| Type | Default | Rationale |
|---|---|---|
| `ambiguous_scope` | PROCEED | Low risk, logged, can review in drift log |
| `ambiguous_phase` | PROCEED | Usually pseudocode, not actual implementation |
| `partial_adherence` | RETRY | Incomplete artifacts compound downstream |
| `schema_near_miss` | RETRY | Type naming consistency is load-bearing |
| `suspiciously_fast` | RETRY | Skipped work is expensive to find later |
| `self_contradiction` | ESCALATE | Hallucinated completeness is a serious signal |

---

## Resolution records

Every resolution — human or timeout — gets written to `session/drift_log.jsonl`:

```json
{
  "signal_id": "a3f9b2c1",
  "uncertainty_type": "ambiguous_scope",
  "confidence": 0.41,
  "resolution": "proceed",
  "resolved_by": "human",
  "human_note": "TokenStore is a private helper, fine",
  "session_id": "auth-system-abc123",
  "node_id": "d4e5f6a7",
  "step_name": "implement_minimal",
  "timestamp": "2026-03-10T14:23:11Z"
}
```

Over many sessions this becomes a calibration dataset. Signals of type X that humans resolve as PROCEED 90% of the time → the confidence threshold for that type is too low. Signals of type Y that humans escalate 80% of the time → they should be blocks, not uncertain.

See [drift log and calibration](./drift-log.md) for the calibration methodology.
