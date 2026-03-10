"""Task node and tree data structures.

The TaskNode is the core data structure. Everything else in the system
exists to create, populate, and traverse these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterator
from uuid import uuid4

from .primitives import (
    COMPOSITION_TYPES,
    GateTemplate,
    PrimitiveType,
    StepTemplate,
    get_steps,
)
from .gates import get_gates


class NodeStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DECOMPOSING = "decomposing"
    BLOCKED = "blocked"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETE = "complete"
    FAILED = "failed"


class StepStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"
    RETRYING = "retrying"
    FAILED = "failed"


@dataclass
class SchemaField:
    name: str
    type_annotation: str  # must be explicit — "Any" triggers gate failure
    required: bool = True
    description: str = ""
    constraints: dict = field(default_factory=dict)


@dataclass
class NodeSchema:
    fields: list[SchemaField] = field(default_factory=list)
    description: str = ""

    def has_any_types(self) -> bool:
        """Returns True if any field uses Any, dict, or object."""
        bad = {"Any", "any", "dict", "object"}
        for f in self.fields:
            if f.type_annotation in bad:
                return True
        return False


@dataclass
class StepRecord:
    template: StepTemplate
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    retry_count: int = 0
    max_retries: int = 1
    correction_context: str = ""
    completed_at: datetime | None = None


@dataclass
class GateResult:
    gate: GateTemplate
    passed: bool = False
    evidence: str = ""
    checked_at: datetime | None = None


def _generate_id() -> str:
    return uuid4().hex[:8]


@dataclass
class TaskNode:
    # Identity
    name: str
    description: str
    primitive_type: PrimitiveType
    id: str = field(default_factory=_generate_id)

    # LLM-filled content
    input_schema: NodeSchema = field(default_factory=NodeSchema)
    output_schema: NodeSchema = field(default_factory=NodeSchema)
    implementation_notes: str = ""

    # Tree structure
    parent_id: str | None = None
    sub_nodes: list[TaskNode] = field(default_factory=list)
    dependency_ids: list[str] = field(default_factory=list)

    # Runtime state
    status: NodeStatus = NodeStatus.PENDING
    steps: list[StepRecord] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)

    # Execution history
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3

    # Superpowers integration
    skill_phases: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Derive steps and gate_results from primitive_type if not set."""
        if not self.steps:
            self.steps = [
                StepRecord(template=t)
                for t in get_steps(self.primitive_type)
            ]
        if not self.gate_results:
            self.gate_results = [
                GateResult(gate=g)
                for g in get_gates(self.primitive_type)
            ]

    @property
    def is_composition(self) -> bool:
        return self.primitive_type in COMPOSITION_TYPES

    @property
    def all_gates_passed(self) -> bool:
        return all(g.passed for g in self.gate_results)

    @property
    def all_children_complete(self) -> bool:
        return all(
            c.status == NodeStatus.COMPLETE for c in self.sub_nodes
        )

    @property
    def depth(self) -> int:
        """Depth in tree. Root is 0. Requires tree traversal from parent."""
        # This is a simple placeholder; actual depth tracking happens via TaskTree
        return 0

    def to_context_summary(self) -> str:
        completed = [s for s in self.steps if s.status == StepStatus.COMPLETE]
        active = [s for s in self.steps if s.status == StepStatus.ACTIVE]
        remaining = [s for s in self.steps if s.status == StepStatus.PENDING]

        current_step = active[0].template.name if active else "none"
        completed_names = ", ".join(s.template.name for s in completed) or "none"
        remaining_names = ", ".join(s.template.name for s in remaining) or "none"

        input_fields = ", ".join(
            f"{f.name}: {f.type_annotation}" for f in self.input_schema.fields
        ) or "none"
        output_fields = ", ".join(
            f"{f.name}: {f.type_annotation}" for f in self.output_schema.fields
        ) or "none"

        return (
            f"NODE: {self.name}\n"
            f"TYPE: {self.primitive_type.value}\n"
            f"DESCRIPTION: {self.description}\n"
            f"CURRENT STEP: {current_step}\n"
            f"COMPLETED STEPS: {completed_names}\n"
            f"REMAINING STEPS: {remaining_names}\n"
            f"INPUT: {input_fields}\n"
            f"OUTPUT: {output_fields}"
        )


@dataclass
class TaskTree:
    session_id: str
    root: TaskNode | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    _index: dict[str, TaskNode] = field(default_factory=dict, repr=False)

    def register(self, node: TaskNode) -> None:
        """Add node to flat index for O(1) lookup."""
        self._index[node.id] = node

    def get(self, node_id: str) -> TaskNode | None:
        return self._index.get(node_id)

    def all_nodes(self) -> list[TaskNode]:
        """Depth-first traversal of all nodes."""
        if self.root is None:
            return []
        result: list[TaskNode] = []
        self._traverse(self.root, result)
        return result

    def _traverse(self, node: TaskNode, result: list[TaskNode]) -> None:
        result.append(node)
        for child in node.sub_nodes:
            self._traverse(child, result)

    def leaf_nodes(self) -> list[TaskNode]:
        return [n for n in self.all_nodes() if not n.is_composition]

    def pending_nodes(self) -> list[TaskNode]:
        return [n for n in self.all_nodes() if n.status == NodeStatus.PENDING]

    def next_executable(self) -> TaskNode | None:
        """First pending node whose dependencies are all complete, depth-first."""
        for node in self.all_nodes():
            if node.status != NodeStatus.PENDING:
                continue
            deps_met = all(
                self._index.get(dep_id) is not None
                and self._index[dep_id].status == NodeStatus.COMPLETE
                for dep_id in node.dependency_ids
            )
            if deps_met:
                return node
        return None

    def is_complete(self) -> bool:
        if self.root is None:
            return False
        return self.root.status == NodeStatus.COMPLETE

    def summary(self) -> str:
        nodes = self.all_nodes()
        from collections import Counter
        status_counts = Counter(n.status.value for n in nodes)
        parts = [f"{status}: {count}" for status, count in sorted(status_counts.items())]
        return f"Nodes: {len(nodes)} total — " + ", ".join(parts)
