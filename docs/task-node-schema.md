# Task node schema

The `TaskNode` is the core data structure of the system. Everything else — the runner, detector, planner, notification system — exists to create, populate, and traverse these.

---

## Full schema

```python
@dataclass
class TaskNode:
    # Identity
    id: str                        # 8-char UUID prefix, generated on creation
    name: str                      # LLM-generated, human-readable
    description: str               # LLM-generated, 1-2 sentences

    # Type — set by planner, immutable after creation
    primitive_type: PrimitiveType  # one of the 22 closed-set types

    # LLM-filled content
    input_schema: NodeSchema       # typed fields, no Any
    output_schema: NodeSchema      # typed fields, no Any
    implementation_notes: str      # planner's guidance to the implementer

    # Tree structure
    parent_id: str | None          # None only for root node
    sub_nodes: list[TaskNode]      # children, populated by decomposer
    dependency_ids: list[str]      # node ids that must complete before this

    # Runtime state — never set by LLM
    status: NodeStatus
    steps: list[StepRecord]        # derived from primitive_type on creation
    gate_results: list[GateResult] # derived from primitive_type on creation

    # Execution history
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    retry_count: int               # node-level retries (not step retries)
    max_retries: int               # default 3

    # Superpowers integration
    skill_phases: list[str]        # which superpowers skill phases apply
```

---

## Field ownership rules

This is the most important constraint in the system. Each field has exactly one owner. Violating ownership is how drift gets introduced.

| Field | Owner | Notes |
|---|---|---|
| `id` | System (auto) | UUID, never touched after creation |
| `name` | Planner (LLM) | Generated during decomposition |
| `description` | Planner (LLM) | Generated during decomposition |
| `primitive_type` | Planner (LLM, constrained) | Validated against enum. If invalid, retry. |
| `input_schema` | Runner (LLM, step-driven) | Filled during `define_input_schema` step |
| `output_schema` | Runner (LLM, step-driven) | Filled during `define_output_schema` step |
| `implementation_notes` | Planner (LLM) | Optional guidance |
| `parent_id` | System (tree construction) | Set when node is added to parent |
| `sub_nodes` | Decomposer (LLM, typed) | Each child must have a valid type |
| `dependency_ids` | Planner (LLM) | Validated: referenced ids must exist in tree |
| `status` | Runner only | LLM never sets or reads this |
| `steps` | System (`__post_init__`) | Derived from `primitive_type`, immutable |
| `gate_results` | Gates runner | Populated after all steps complete |
| `retry_count` | Runner | Incremented on block, reset on complete |
| `skill_phases` | System (type mapping) | Derived from `primitive_type` |

---

## Supporting types

### `NodeSchema`

```python
@dataclass
class NodeSchema:
    fields: list[SchemaField]
    description: str

    def has_any_types(self) -> bool:
        # Returns True if any field uses Any, dict, or object
        # Used by ast_no_any gate check
```

### `SchemaField`

```python
@dataclass
class SchemaField:
    name: str
    type_annotation: str   # must be explicit — "Any" triggers gate failure
    required: bool
    description: str
    constraints: dict      # min, max, pattern, enum_values, etc.
```

### `StepRecord`

```python
@dataclass
class StepRecord:
    template: StepTemplate   # immutable, derived from primitive_type
    status: StepStatus       # PENDING → ACTIVE → COMPLETE (or RETRYING/FAILED)
    output: str              # LLM output for this step
    retry_count: int         # step-level retries (separate from node retries)
    correction_context: str  # prepended to retry prompt when non-empty
    completed_at: datetime | None
```

### `GateResult`

```python
@dataclass
class GateResult:
    gate: GateTemplate   # immutable, derived from primitive_type
    passed: bool
    evidence: str        # what the check found — used in drift correction
    checked_at: datetime | None
```

---

## Node status transitions

```
PENDING
  │
  ├─ (leaf node)    → IN_PROGRESS
  │                       │
  │                       ├─ drift detected    → BLOCKED → IN_PROGRESS (retry)
  │                       │                              → FAILED (max retries)
  │                       │
  │                       ├─ uncertain signal  → AWAITING_HUMAN → IN_PROGRESS
  │                       │
  │                       └─ all steps done, all gates passed → COMPLETE
  │
  └─ (composition)  → DECOMPOSING
                          │
                          └─ all children complete → COMPLETE
```

`FAILED` is terminal without human intervention. A failed node blocks its parent from completing. The session pauses at that point and requires explicit human resolution — either fix the node or restructure the tree.

---

## `TaskTree`

The tree wraps the root node and provides:

```python
@dataclass
class TaskTree:
    session_id: str
    root: TaskNode | None
    created_at: datetime

    def register(node)          # adds to flat index for O(1) lookup
    def get(node_id) -> TaskNode | None
    def all_nodes() -> list[TaskNode]   # depth-first traversal
    def leaf_nodes() -> list[TaskNode]
    def pending_nodes() -> list[TaskNode]
    def next_executable() -> TaskNode | None  # first pending with deps met
    def is_complete() -> bool
    def summary() -> str        # node counts by status
```

The flat `_index` is important. The runner frequently needs to look up nodes by id (dependency checking, parent resolution, drift signal attribution). Traversing the tree every time would be O(n) per lookup. The index makes it O(1).

`next_executable()` is the runner's main query. It returns the first pending node whose dependencies are all complete, in depth-first order. This means the tree always executes leaves before parents, and respects explicit dependency ordering within each level.

---

## Context injection

Every LLM call receives `node.to_context_summary()` as the first block of its context:

```
NODE: generate_reset_token
TYPE: transformation
DESCRIPTION: Generates a cryptographically secure, time-limited password reset token
CURRENT STEP: write_failing_tests
COMPLETED STEPS: define_input_schema, define_output_schema, enumerate_edge_cases
REMAINING STEPS: implement_minimal, refactor
INPUT: user_id: str, expiry_seconds: int
OUTPUT: token: ResetToken
```

This is short by design. The model needs to orient itself without the context summary consuming most of its context budget. The global schema registry (injected separately) is where the full type definitions live.

---

## Serialization

`TaskTree` serializes to JSON for session persistence. All `datetime` fields serialize to ISO 8601. `PrimitiveType`, `NodeStatus`, `StepStatus` serialize to their string values. `StepTemplate` and `GateTemplate` are reconstructed from the type registry on deserialization — they're not stored verbatim, because they're immutable and derived from type.

This means if step templates are updated between sessions, resumed sessions get the new templates. That's intentional: templates improve, old sessions should benefit. The completed step outputs are preserved, so the runner can resume from where it left off with updated templates for remaining steps.
