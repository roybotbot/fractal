"""Tests for session/state.py and session/log.py."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, UTC

import pytest

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
from superpowers_runner.schema.signals import (
    DriftSignal,
    DriftType,
    Resolution,
    Severity,
    UncertaintySignal,
    UncertaintyType,
)
from superpowers_runner.session.log import DriftLog
from superpowers_runner.session.state import StateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tree(session_id: str = "test-session-001") -> TaskTree:
    tree = TaskTree(session_id=session_id)
    root = TaskNode(
        name="password_reset",
        description="Implement password reset flow",
        primitive_type=PrimitiveType.ORCHESTRATION,
    )
    root.status = NodeStatus.IN_PROGRESS

    child = TaskNode(
        name="ResetToken",
        description="Token model",
        primitive_type=PrimitiveType.DATA_MODEL,
    )
    child.status = NodeStatus.COMPLETE
    child.parent_id = root.id
    child.input_schema = NodeSchema(
        fields=[SchemaField(name="user_id", type_annotation="str")]
    )
    child.output_schema = NodeSchema(
        fields=[SchemaField(name="token", type_annotation="str")]
    )

    # Mark first step (auto-populated by __post_init__) as complete
    child.steps[0].status = StepStatus.COMPLETE
    child.steps[0].output = "Fields: user_id, token, expires_at"
    child.steps[0].completed_at = datetime.now(UTC)

    # Mark first gate result (auto-populated by __post_init__) as passed
    child.gate_results[0].passed = True
    child.gate_results[0].evidence = "No Any annotations found"
    child.gate_results[0].checked_at = datetime.now(UTC)

    root.sub_nodes.append(child)
    tree.root = root
    return tree


def _make_uncertainty() -> UncertaintySignal:
    return UncertaintySignal(
        id="abc12345",
        uncertainty_type=UncertaintyType.AMBIGUOUS_SCOPE,
        node_id="node01",
        step_name="implement",
        confidence=0.41,
        evidence="TokenStore not in spec",
        output_excerpt="class TokenStore:",
        question="Is this a helper (A) or scope drift (B)?",
        option_a="Helper",
        option_b="Scope drift",
        default_resolution=Resolution.PROCEED,
    )


def _make_drift() -> DriftSignal:
    return DriftSignal(
        id="drift001",
        drift_type=DriftType.SCOPE,
        severity=Severity.BLOCK,
        node_id="node01",
        step_name="implement",
        evidence="Scope drift detected",
        output_excerpt="class TokenStore:",
        correction_template="Remove TokenStore",
    )


# ============================================================================
# StateManager — save / load round-trip
# ============================================================================


class TestStateRoundTrip:
    def test_save_and_load(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        assert loaded.session_id == tree.session_id
        assert loaded.root is not None
        assert loaded.root.name == "password_reset"
        assert loaded.root.status == NodeStatus.IN_PROGRESS

    def test_preserves_children(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        assert len(loaded.root.sub_nodes) == 1
        child = loaded.root.sub_nodes[0]
        assert child.name == "ResetToken"
        assert child.status == NodeStatus.COMPLETE
        assert child.parent_id == loaded.root.id

    def test_preserves_steps(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        original_step_count = len(tree.root.sub_nodes[0].steps)
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        child = loaded.root.sub_nodes[0]
        assert len(child.steps) == original_step_count
        # First step was marked complete in _make_tree
        step = child.steps[0]
        assert step.template.name == "enumerate_fields"
        assert step.status == StepStatus.COMPLETE
        assert "user_id" in step.output

    def test_preserves_gate_results(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        original_gate_count = len(tree.root.sub_nodes[0].gate_results)
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        child = loaded.root.sub_nodes[0]
        assert len(child.gate_results) == original_gate_count
        # First gate was marked passed in _make_tree
        gr = child.gate_results[0]
        assert gr.passed is True

    def test_preserves_schemas(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        child = loaded.root.sub_nodes[0]
        assert len(child.input_schema.fields) == 1
        assert child.input_schema.fields[0].name == "user_id"
        assert len(child.output_schema.fields) == 1
        assert child.output_schema.fields[0].name == "token"

    def test_preserves_dependency_ids(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        tree.root.sub_nodes[0].dependency_ids = {"dep1", "dep2"}
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        child = loaded.root.sub_nodes[0]
        assert child.dependency_ids == {"dep1", "dep2"}


# ============================================================================
# StateManager — atomic write
# ============================================================================


class TestAtomicWrite:
    def test_tree_json_exists_after_save(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        path = mgr.save(tree)
        assert path.exists()
        assert path.name == "tree.json"

    def test_metadata_exists_after_save(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        mgr.save(tree)
        meta_path = tmp_path / tree.session_id / "metadata.json"
        assert meta_path.exists()

    def test_metadata_content(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        mgr.save(tree)
        meta_path = tmp_path / tree.session_id / "metadata.json"
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["session_id"] == tree.session_id
        assert meta["status"] == "in_progress"
        assert meta["total_nodes"] == 2  # root + child
        assert meta["completed_nodes"] == 1  # child only


# ============================================================================
# StateManager — session management
# ============================================================================


class TestSessionManagement:
    def test_session_exists(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        assert not mgr.session_exists(tree.session_id)
        mgr.save(tree)
        assert mgr.session_exists(tree.session_id)

    def test_list_sessions_empty(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        assert mgr.list_sessions() == []

    def test_list_sessions(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        mgr.save(_make_tree("session-001"))
        mgr.save(_make_tree("session-002"))
        sessions = mgr.list_sessions()
        assert len(sessions) == 2
        ids = {s["session_id"] for s in sessions}
        assert ids == {"session-001", "session-002"}

    def test_load_nonexistent_raises(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError):
            mgr.load("nonexistent")

    def test_overwrite_save(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = _make_tree()
        mgr.save(tree)

        # Update and save again
        tree.root.status = NodeStatus.COMPLETE
        mgr.save(tree)

        loaded = mgr.load(tree.session_id)
        assert loaded.root.status == NodeStatus.COMPLETE


# ============================================================================
# StateManager — edge cases
# ============================================================================


class TestStateEdgeCases:
    def test_empty_tree(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = TaskTree(session_id="empty")
        mgr.save(tree)
        loaded = mgr.load("empty")
        assert loaded.root is None

    def test_deep_nesting(self, tmp_path):
        mgr = StateManager(session_dir=str(tmp_path))
        tree = TaskTree(session_id="deep")
        root = TaskNode(name="root", description="", primitive_type=PrimitiveType.ORCHESTRATION)
        current = root
        for i in range(5):
            child = TaskNode(
                name=f"child_{i}",
                description="",
                primitive_type=PrimitiveType.TRANSFORMATION,
            )
            child.parent_id = current.id
            current.sub_nodes.append(child)
            current = child

        tree.root = root
        mgr.save(tree)

        loaded = mgr.load("deep")
        node = loaded.root
        depth = 0
        while node.sub_nodes:
            node = node.sub_nodes[0]
            depth += 1
        assert depth == 5


# ============================================================================
# DriftLog — uncertainty signals
# ============================================================================


class TestDriftLogUncertainty:
    def test_log_and_read(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        signal = _make_uncertainty()
        signal.resolution = Resolution.PROCEED
        signal.resolved_by = "human"
        log.log_uncertainty(signal, "test-session")

        records = log.read_all()
        assert len(records) == 1
        assert records[0]["signal_id"] == "abc12345"
        assert records[0]["uncertainty_type"] == "ambiguous_scope"
        assert records[0]["resolution"] == "proceed"
        assert records[0]["resolved_by"] == "human"

    def test_append_only(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        s1 = _make_uncertainty()
        s1.resolution = Resolution.PROCEED
        s2 = _make_uncertainty()
        s2.id = "xyz98765"
        s2.resolution = Resolution.RETRY

        log.log_uncertainty(s1, "test-session")
        log.log_uncertainty(s2, "test-session")

        records = log.read_all()
        assert len(records) == 2
        assert records[0]["signal_id"] == "abc12345"
        assert records[1]["signal_id"] == "xyz98765"

    def test_read_empty(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        records = log.read_all()
        assert records == []


# ============================================================================
# DriftLog — drift signals
# ============================================================================


class TestDriftLogDrift:
    def test_log_drift(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        signal = _make_drift()
        log.log_drift(signal, "test-session", resolution="retried", retry_succeeded=True)

        records = log.read_all()
        assert len(records) == 1
        assert records[0]["drift_type"] == "scope"
        assert records[0]["severity"] == "block"
        assert records[0]["resolution"] == "retried"
        assert records[0]["retry_succeeded"] is True

    def test_mixed_log(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        u = _make_uncertainty()
        u.resolution = Resolution.PROCEED
        d = _make_drift()

        log.log_uncertainty(u, "test-session")
        log.log_drift(d, "test-session")

        records = log.read_all()
        assert len(records) == 2


# ============================================================================
# DriftLog — filtering
# ============================================================================


class TestDriftLogFiltering:
    def test_read_by_uncertainty_type(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")

        s1 = _make_uncertainty()
        s1.uncertainty_type = UncertaintyType.AMBIGUOUS_SCOPE
        s1.resolution = Resolution.PROCEED
        s2 = _make_uncertainty()
        s2.id = "other001"
        s2.uncertainty_type = UncertaintyType.SCHEMA_NEAR_MISS
        s2.resolution = Resolution.RETRY

        log.log_uncertainty(s1, "test-session")
        log.log_uncertainty(s2, "test-session")

        scope_records = log.read_by_type("ambiguous_scope")
        assert len(scope_records) == 1
        assert scope_records[0]["signal_id"] == "abc12345"

    def test_read_by_drift_type(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        d = _make_drift()
        log.log_drift(d, "test-session")

        records = log.read_by_type("scope")
        assert len(records) == 1

    def test_read_by_type_no_match(self, tmp_path):
        log = DriftLog(str(tmp_path), "test-session")
        d = _make_drift()
        log.log_drift(d, "test-session")

        records = log.read_by_type("nonexistent")
        assert len(records) == 0
