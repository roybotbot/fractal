"""StateManager — atomic save/load for session state.

Persists the full TaskTree as JSON with atomic write.
Handles save points, resume, and session listing.

Depends on: schema layer only (imports nodes for serialization).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from superpowers_runner.schema.nodes import (
    GateResult,
    NodeSchema,
    NodeStatus,
    SchemaField,
    StepRecord,
    StepStatus,
    TaskNode,
    TaskTree,
)
from superpowers_runner.schema.primitives import GateTemplate, PrimitiveType, StepTemplate


def _serialize_node(node: TaskNode) -> dict[str, Any]:
    """Serialize a TaskNode to a JSON-compatible dict."""
    result: dict[str, Any] = {
        "id": node.id,
        "name": node.name,
        "description": node.description,
        "primitive_type": node.primitive_type.value,
        "status": node.status.value,
        "parent_id": node.parent_id,
        "dependency_ids": list(node.dependency_ids),
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "started_at": node.started_at.isoformat() if node.started_at else None,
        "completed_at": node.completed_at.isoformat() if node.completed_at else None,
        "retry_count": node.retry_count,
        "input_schema": _serialize_schema(node.input_schema),
        "output_schema": _serialize_schema(node.output_schema),
    }

    # Steps — only runtime fields, not the template
    result["steps"] = []
    for step in node.steps:
        result["steps"].append({
            "step_name": step.template.name,
            "status": step.status.value,
            "output": step.output,
            "retry_count": step.retry_count,
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
        })

    # Gate results
    result["gate_results"] = []
    for gr in node.gate_results:
        result["gate_results"].append({
            "gate_name": gr.gate.name,
            "check_type": gr.gate.check_type,
            "passed": gr.passed,
            "evidence": gr.evidence,
            "checked_at": gr.checked_at.isoformat() if gr.checked_at else None,
        })

    # Sub-nodes (recursive)
    result["sub_nodes"] = [_serialize_node(child) for child in node.sub_nodes]

    return result


def _serialize_schema(schema: NodeSchema) -> dict[str, Any]:
    """Serialize a NodeSchema."""
    return {
        "fields": [
            {"name": f.name, "type_annotation": f.type_annotation, "description": f.description}
            for f in schema.fields
        ]
    }


def _deserialize_node(data: dict[str, Any]) -> TaskNode:
    """Deserialize a TaskNode from JSON data.

    Steps and gate_results are deserialized from persisted data.
    We build them before constructing the node so __post_init__
    doesn't overwrite with template defaults.
    """
    # Build steps from persisted data first
    steps: list[StepRecord] = []
    for step_data in data.get("steps", []):
        template = StepTemplate(
            name=step_data["step_name"],
            prompt_template="",
            expected_artifacts=[],
            forbidden_artifacts=[],
        )
        step = StepRecord(
            template=template,
            status=StepStatus(step_data["status"]),
        )
        step.output = step_data.get("output", "")
        step.retry_count = step_data.get("retry_count", 0)
        if step_data.get("completed_at"):
            step.completed_at = datetime.fromisoformat(step_data["completed_at"])
        steps.append(step)

    # Build gate results from persisted data first
    gate_results: list[GateResult] = []
    for gr_data in data.get("gate_results", []):
        gate = GateTemplate(
            name=gr_data["gate_name"],
            check_type=gr_data.get("check_type", ""),
        )
        gr = GateResult(
            gate=gate,
            passed=gr_data["passed"],
            evidence=gr_data.get("evidence", ""),
        )
        if gr_data.get("checked_at"):
            gr.checked_at = datetime.fromisoformat(gr_data["checked_at"])
        gate_results.append(gr)

    # Construct node — __post_init__ won't overwrite because lists are non-empty
    # For truly empty steps/gates, use a sentinel and clear after
    node = TaskNode.__new__(TaskNode)
    node.name = data["name"]
    node.description = data.get("description", "")
    node.primitive_type = PrimitiveType(data["primitive_type"])
    node.id = data["id"]
    node.input_schema = _deserialize_schema(data["input_schema"]) if data.get("input_schema") else NodeSchema()
    node.output_schema = _deserialize_schema(data["output_schema"]) if data.get("output_schema") else NodeSchema()
    node.implementation_notes = data.get("implementation_notes", "")
    node.parent_id = data.get("parent_id")
    node.sub_nodes = []
    node.dependency_ids = set(data.get("dependency_ids", []))
    node.status = NodeStatus(data["status"])
    node.steps = steps
    node.gate_results = gate_results
    node.created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(UTC)
    node.started_at = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
    node.completed_at = datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
    node.retry_count = data.get("retry_count", 0)
    node.max_retries = data.get("max_retries", 3)
    node.skill_phases = data.get("skill_phases", [])

    # Sub-nodes (recursive)
    for child_data in data.get("sub_nodes", []):
        child = _deserialize_node(child_data)
        child.parent_id = node.id
        node.sub_nodes.append(child)

    return node


def _deserialize_schema(data: dict[str, Any]) -> NodeSchema:
    """Deserialize a NodeSchema."""
    fields = []
    for f_data in data.get("fields", []):
        fields.append(SchemaField(
            name=f_data["name"],
            type_annotation=f_data.get("type_annotation", "str"),
            description=f_data.get("description", ""),
        ))
    return NodeSchema(fields=fields)


class StateManager:
    """Manages session state persistence.

    Directory layout:
        session_dir/
            {session_id}/
                tree.json
                drift_log.jsonl
                metadata.json
    """

    def __init__(self, session_dir: str = "sessions") -> None:
        self._session_dir = Path(session_dir)

    def save(self, tree: TaskTree) -> Path:
        """Save tree state atomically. Returns path to saved file."""
        session_path = self._session_dir / tree.session_id
        session_path.mkdir(parents=True, exist_ok=True)

        data = {
            "session_id": tree.session_id,
            "created_at": tree.created_at.isoformat() if tree.created_at else None,
            "root": _serialize_node(tree.root) if tree.root else None,
        }

        tree_path = session_path / "tree.json"
        self._atomic_write(tree_path, json.dumps(data, indent=2))

        # Update metadata
        self._save_metadata(tree)

        return tree_path

    def load(self, session_id: str) -> TaskTree:
        """Load a session from disk."""
        tree_path = self._session_dir / session_id / "tree.json"
        if not tree_path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")

        with open(tree_path) as f:
            data = json.load(f)

        tree = TaskTree(session_id=data["session_id"])
        if data.get("created_at"):
            tree.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("root"):
            tree.root = _deserialize_node(data["root"])

        return tree

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with metadata."""
        sessions: list[dict[str, Any]] = []
        if not self._session_dir.exists():
            return sessions

        for entry in sorted(self._session_dir.iterdir()):
            if entry.is_dir():
                meta_path = entry / "metadata.json"
                if meta_path.exists():
                    with open(meta_path) as f:
                        sessions.append(json.load(f))
                else:
                    # Minimal entry from directory name
                    sessions.append({
                        "session_id": entry.name,
                        "status": "unknown",
                    })

        return sessions

    def session_exists(self, session_id: str) -> bool:
        """Check if a session exists on disk."""
        return (self._session_dir / session_id / "tree.json").exists()

    def _save_metadata(self, tree: TaskTree) -> None:
        """Write session metadata."""
        session_path = self._session_dir / tree.session_id

        total, completed = 0, 0
        if tree.root:
            total, completed = self._count_nodes(tree.root)

        status = "pending"
        if tree.root:
            if tree.root.status == NodeStatus.COMPLETE:
                status = "complete"
            elif tree.root.status == NodeStatus.FAILED:
                status = "failed"
            elif tree.root.status == NodeStatus.IN_PROGRESS:
                status = "in_progress"

        meta = {
            "session_id": tree.session_id,
            "task": tree.root.description if tree.root else "",
            "status": status,
            "created_at": tree.created_at.isoformat() if tree.created_at else None,
            "last_save": datetime.now(UTC).isoformat(),
            "total_nodes": total,
            "completed_nodes": completed,
        }

        meta_path = session_path / "metadata.json"
        self._atomic_write(meta_path, json.dumps(meta, indent=2))

    def _count_nodes(self, node: TaskNode) -> tuple[int, int]:
        """Count total and completed nodes recursively."""
        total = 1
        completed = 1 if node.status == NodeStatus.COMPLETE else 0
        for child in node.sub_nodes:
            ct, cc = self._count_nodes(child)
            total += ct
            completed += cc
        return total, completed

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content atomically via temp file + rename."""
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
