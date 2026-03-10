# Drift log and calibration

The drift log is the system's memory across sessions. It turns every human decision into evidence for improving the detector.

---

## What gets logged

Every signal — drift or uncertainty — and its resolution gets written as one JSON line to `session/{session_id}/drift_log.jsonl`.

```json
{"signal_id": "a3f9b2c1", "uncertainty_type": "ambiguous_scope", "confidence": 0.41, "resolution": "proceed", "resolved_by": "human", "human_note": "TokenStore is a private helper, fine to proceed", "session_id": "auth-system-abc123", "node_id": "d4e5f6a7", "step_name": "implement_minimal", "timestamp": "2026-03-10T14:23:11Z"}
{"signal_id": "b7c1d3e2", "drift_type": "phase", "severity": "block", "resolution": "retried", "retry_succeeded": true, "node_id": "e8f9a0b1", "step_name": "enumerate_edge_cases", "timestamp": "2026-03-10T14:31:44Z"}
```

Drift signals and uncertainty signals use slightly different fields but both go to the same log. The `signal_id` links back to the in-memory signal object for debugging.

---

## Log schema

For uncertainty signals:

```
signal_id            str   — 8-char UUID prefix
uncertainty_type     str   — UncertaintyType.value
confidence           float — detector's confidence score at detection time
resolution           str   — Resolution.value (proceed/retry/escalate)
resolved_by          str   — "human" or "timeout"
human_note           str   — optional free text, empty string if not provided
session_id           str
node_id              str
step_name            str
timestamp            str   — ISO 8601
```

For drift signals:

```
signal_id            str
drift_type           str   — DriftType.value
severity             str   — Severity.value
resolution           str   — "retried", "escalated", "human_resolved"
retry_succeeded      bool  — did the retry fix it?
session_id           str
node_id              str
step_name            str
timestamp            str
```

---

## Calibration queries

The log is a flat JSONL file. Query it with any tool that handles JSONL — pandas, jq, a simple Python script.

**Which uncertainty types generate the most noise?**

```python
import json
from collections import Counter

with open("drift_log.jsonl") as f:
    records = [json.loads(line) for line in f if "uncertainty_type" in line]

proceed_by_type = Counter(
    r["uncertainty_type"]
    for r in records
    if r["resolution"] == "proceed"
)
total_by_type = Counter(r["uncertainty_type"] for r in records)

for type_name, total in total_by_type.most_common():
    proceed_rate = proceed_by_type[type_name] / total
    print(f"{type_name}: {proceed_rate:.0%} proceed rate ({total} signals)")
```

A type with 90%+ proceed rate is generating noise. Lower its confidence threshold or make its default_resolution `proceed` unconditionally.

**Which step names generate the most drift blocks?**

```python
block_by_step = Counter(
    r["step_name"]
    for r in records
    if r.get("severity") == "block"
)
for step, count in block_by_step.most_common(10):
    print(f"{step}: {count} blocks")
```

Steps with high block counts are either poorly prompted or represent genuinely hard transitions. High block counts on `enumerate_edge_cases` → the step prompt needs to be more explicit about what "enumerate" means. High block counts on `implement_minimal` → the LLM keeps adding extra code; tighten the prompt.

**Retry success rate by drift type:**

```python
retry_records = [r for r in records if r.get("resolution") == "retried"]
success_by_type = Counter(
    r["drift_type"]
    for r in retry_records
    if r.get("retry_succeeded")
)
total_retry_by_type = Counter(r["drift_type"] for r in retry_records)

for dtype, total in total_retry_by_type.most_common():
    rate = success_by_type[dtype] / total
    print(f"{dtype}: {rate:.0%} retry success ({total} retries)")
```

Low retry success rate on a drift type → the correction context for that type isn't specific enough. Rewrite the correction template.

---

## Threshold adjustment

Each uncertainty type has a `confidence_threshold` in the detector. Below the threshold, the signal is uncertain. Above it, it's a drift block.

The threshold should be set based on log data:

```python
# For a given uncertainty type, what confidence level
# corresponds to a 70% human-escalate rate?
# Set the threshold just below that — let the detector catch those,
# leave lower-confidence signals as uncertain for human review.

type_records = [r for r in records if r["uncertainty_type"] == target_type]
escalated = [r for r in type_records if r["resolution"] == "escalate"]

# Bin by confidence and compute escalate rate per bin
import numpy as np
confidences = [r["confidence"] for r in type_records]
escalated_flags = [r["resolution"] == "escalate" for r in type_records]

bins = np.arange(0, 1.1, 0.1)
for i in range(len(bins) - 1):
    bin_records = [
        (c, e) for c, e in zip(confidences, escalated_flags)
        if bins[i] <= c < bins[i+1]
    ]
    if bin_records:
        rate = sum(e for _, e in bin_records) / len(bin_records)
        print(f"{bins[i]:.1f}–{bins[i+1]:.1f}: {rate:.0%} escalate rate ({len(bin_records)} signals)")
```

This gives you empirical calibration data rather than intuition-based threshold setting.

---

## Human notes as qualitative signal

The `human_note` field is optional but valuable. When a human reviews a signal and has something to say — "this is always fine in a transformation context", "this specific pattern is never a problem" — that note gets stored.

Over time, recurring notes suggest categorical rules. "TokenStore is a private helper" appearing 10 times across sessions suggests the detector should recognize that pattern and either lower its confidence or exclude it from scope drift detection entirely.

Manual review of human notes every few hundred sessions is worth the time.
