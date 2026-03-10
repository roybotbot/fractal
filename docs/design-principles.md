# Design principles

The decisions in this system aren't arbitrary. Each one is a response to a specific failure mode observed in unconstrained LLM coding sessions.

---

## The LLM is a content generator, not a process controller

The most important design decision. The LLM fills in names, descriptions, implementations, and test code. It never decides its own step sequence. It never sets its own completion status. It never evaluates its own output quality.

This sounds obvious, but most "agentic" systems violate it. They give the model a task and a set of tools and let it decide what to do next. That works fine for short tasks in familiar domains. It fails for long tasks because the model's attention drifts from the original constraints as the context fills.

The fix is to remove the model's control over process entirely. The runner decides what step runs next. The gates decide when a node is done. The detector decides if the output is acceptable. The model's job is to write good content inside those constraints.

---

## Types determine shape, not content

The 22 primitive types exist so that step templates and gate checks can be derived mechanically from the type assignment. The alternative — having the planner generate custom steps for each node — would mean the process enforcement is only as good as the planner's output, which is LLM-generated and therefore subject to drift.

With typed nodes: a `mutation` always gets the same 6 steps in the same order with the same gates. The LLM can't generate a mutation node that doesn't require failure mode enumeration. It's structural, not requested.

The cost is rigidity. Some nodes might not need every step in their type's template. The system doesn't try to optimize this. A few unnecessary steps in a rare case is a much better problem than missing required steps in a common case.

---

## Gates are the ground truth

The drift detector operates on LLM output, which is text. Text analysis has error rates. An LLM judge gate check is itself an LLM call, which can be wrong.

Gates based on running actual code — `run_tests`, `ast_no_any`, `ast_no_io` — have no error rate. The test either passes or it doesn't. The AST either contains `Any` or it doesn't.

This is why the `run_tests` gate exists and is not optional. The model claiming tests pass is not evidence. The test runner returning exit code 0 is evidence. The gap between those two things is where completion drift lives.

---

## Three signal states, not two

Clean/drifted is the obvious framing. But the real world generates a third state constantly: situations where something looks off but the detector isn't confident enough to block.

Silently resolving uncertainty into either clean or drifted produces the wrong behavior in both directions:

Treating uncertain as clean means problems that the detector caught — but not confidently — accumulate unnoticed. Over a 2-hour session, 10 uncertain signals all resolved as "proceed" might mean 10 compounding problems.

Treating uncertain as drifted means the system halts constantly on noise. An autonomous session that stops every few steps for review isn't autonomous.

The notification system routes these to a human with a specific, answerable question. A 10-second decision per uncertain signal is much better than either alternative.

---

## Human checkpoints at structural transitions, not random intervals

The two mandatory human checkpoints are: after the plan is generated (before execution), and when a node reaches `FAILED` status.

The first is a structural transition — from planning to execution. The human approves the shape of the work before any code is written. Mistakes here are cheap to fix; mistakes caught after implementation are expensive.

The second is a terminal state. A failed node means something went wrong that the correction mechanism couldn't resolve. Human judgment is genuinely needed.

Checkpoints at arbitrary time intervals (every N minutes, every N nodes) are worse. They interrupt at arbitrary points with arbitrary context. The human has to figure out where the system is and why they're being asked to look. Structural checkpoints interrupt at moments where the decision is clear.

---

## Correction context is specific, not general

When a step fails and gets a retry, the correction context prepended to the retry prompt names the specific thing that was wrong. Not "please try again." Not "your previous response was incorrect."

"DRIFT DETECTED: phase. EVIDENCE: Implementation code found during enumerate_edge_cases step. CORRECTION REQUIRED: Remove all implementation code. This step produces only a list of edge cases."

Specific corrections work because they tell the model exactly what to avoid. General corrections fail because the model pattern-matches to "something was wrong" and tries to produce a different-looking response without understanding what the problem was.

---

## Drift log as calibration data

Every uncertainty signal and its resolution is written to a JSONL log. This is boring infrastructure that becomes valuable over time.

After 50 sessions: which uncertainty types are resolved as "proceed" 90% of the time? Those have thresholds set too low — they're generating noise. Which types are escalated to blocks 80% of the time? Those should become blocks, not uncertainties.

The detector's confidence thresholds should be empirically derived from this data, not set by intuition. The system should get better at knowing when to interrupt and when to proceed as it accumulates evidence.

This is the same feedback loop that makes any monitoring system useful. Without it, you're tuning the detector by guessing. With it, you're tuning it from evidence.

---

## What this system deliberately doesn't do

It doesn't guarantee semantic correctness. Code that compiles, passes tests, and matches the spec structurally might still have logic bugs. The gates check structure and test execution. They don't check that the logic is right.

It doesn't replace code review. The gates and drift detection are a floor, not a ceiling. Human review of completed work is still valuable. The system doesn't make the argument that it removes the need for review.

It doesn't optimize for speed. The mandatory step sequences add overhead compared to just asking the model to complete the task. The bet is that the overhead is less expensive than rework caused by drift — especially for long tasks where drift reliably accumulates.
