# Project structure

```
superpowers_runner/
│
├─ schema/
│   ├─ primitives.py      PrimitiveType enum, NodeCategory, StepTemplate,
│   │                     GateTemplate, STEP_TEMPLATES registry, get_steps()
│   ├─ gates.py           GATE_TEMPLATES registry, get_gates()
│   ├─ nodes.py           TaskNode, TaskTree, NodeStatus, StepStatus,
│   │                     StepRecord, GateResult, NodeSchema, SchemaField
│   └─ signals.py         DriftSignal, UncertaintySignal, ResolutionRecord,
│                         DriftType, UncertaintyType, Severity, Resolution,
│                         notification policy constants
│
├─ detector/
│   ├─ drift.py           DriftDetector class — five check methods
│   ├─ uncertainty.py     UncertaintyDetector class — six check methods
│   └─ checks.py          Gate check implementations keyed by check_type string
│
├─ runner/
│   ├─ runner.py          Runner class — main execution engine
│   ├─ context.py         ContextBuilder — assembles LLM call inputs
│   ├─ correction.py      CorrectionEngine — handles block signals, builds retry prompts
│   └─ gates_runner.py    GateRunner — executes gate checks against completed nodes
│
├─ planner/
│   ├─ planner.py               Planner class — two entry points:
│   │                           from_task_description() and from_superpowers_plan()
│   ├─ classifier.py            Task → PrimitiveType classification
│   ├─ decomposer.py            Composition node → typed child nodes
│   └─ superpowers_parser.py    superpowers plan.md → list[SuperpowersPlanTask]
│                               Tolerant markdown parser, flags unparseable sections
│
├─ notify/
│   ├─ notifier.py        UncertaintyBuffer, interrupt/batch routing, timeout handler
│   └─ display.py         Terminal UI for human review prompts
│
├─ session/
│   ├─ state.py           TaskTree serialization, session save/load, resume logic
│   ├─ log.py             DriftLog — ResolutionRecord writer to JSONL
│   └─ logger.py          ExecutionLogger — execution_log.jsonl writer,
│                         content_log/ file writer, node_path computation
│
├─ skills/
│   └─ (superpowers SKILL.md files — injected per node based on skill_phases)
│
└─ session/
    └─ {session_id}/
        ├─ tree.json       Full TaskTree state
        ├─ drift_log.jsonl All signal + resolution records
        └─ artifacts/      Step outputs, generated code files
```

---

## Module dependency rules

```
schema      ← depends on nothing
detector    ← depends on schema
checks      ← depends on schema
logger      ← depends on schema
runner      ← depends on schema, detector, checks, logger
planner     ← depends on schema
notify      ← depends on schema, logger
session     ← depends on schema
```

Nothing in `schema` imports from any other layer. This is enforced — any circular import from schema to runner or detector indicates a design mistake.

`detector` and `checks` are separate for a reason. Checks are the implementations of gate evaluation. The detector is the runtime analysis of step output. They have different callers (gate_runner calls checks; runner calls detector) and different responsibilities.

---

## LLM client interface

All LLM calls go through a single `LLMClient` interface. This makes it straightforward to swap models, add caching, or inject the Anthropic API vs a local model.

```python
class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...
```

The `system` parameter is used for constrained calls (classifier, decomposer, LLM judge) where the system prompt is different from the main execution system prompt.

---

## Session directory layout

Each session gets a directory under `session/{session_id}/`:

```
session/
└─ auth-system-abc123/
    ├─ tree.json              TaskTree state at last save point
    ├─ drift_log.jsonl        One JSON object per line, one per signal+resolution
    ├─ execution_log.jsonl    One JSON object per line, one per structural event
    ├─ metadata.json          Session start time, task description, node count
    ├─ content_log/           One subdirectory per node, one file per step attempt
    │   ├─ {node_id}_{name}/
    │   │   ├─ step_01_{step_name}.md
    │   │   ├─ step_02_{step_name}.md
    │   │   ├─ step_03_{step_name}_attempt1.md
    │   │   ├─ step_03_{step_name}_attempt2.md   ← retry gets its own file
    │   │   └─ ...
    │   └─ ...
    └─ artifacts/
        ├─ {node_id}/
        │   ├─ step_outputs/
        │   │   ├─ define_input_schema.md
        │   │   └─ ...
        │   └─ implementation/
        │       ├─ {node_name}.py
        │       └─ test_{node_name}.py
        └─ ...
```

Step outputs are saved as markdown files. Implementation outputs are saved as `.py` files in the correct relative path for the project. After session completion, the `artifacts/` directory contains a working Python project.
