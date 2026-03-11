"""Tests for session/logger.py — ExecutionLogger and compute_node_path."""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import pytest

from superpowers_runner.schema.nodes import (
    GateResult,
    StepRecord,
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
from superpowers_runner.session.logger import (
    ExecutionLogger,
    NullLogger,
    compute_node_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tree_with_child() -> tuple[TaskTree, TaskNode, TaskNode]:
    tree = TaskTree(session_id="test-session")
    root = TaskNode(name="root_flow", description="root", primitive_type=PrimitiveType.ORCHESTRATION)
    child = TaskNode(name="child_model", description="child", primitive_type=PrimitiveType.DATA_MODEL, parent_id=root.id)
    root.sub_nodes = [child]
    tree.root = root
    tree.register(root)
    tree.register(child)
    return tree, root, child


def _make_tree_with_grandchild() -> tuple[TaskTree, TaskNode, TaskNode, TaskNode]:
    tree = TaskTree(session_id="test-session")
    root = TaskNode(name="checkout_flow", description="root", primitive_type=PrimitiveType.ORCHESTRATION)
    parent = TaskNode(name="payment_stage", description="mid", primitive_type=PrimitiveType.PIPELINE, parent_id=root.id)
    child = TaskNode(name="validate_card", description="leaf", primitive_type=PrimitiveType.VALIDATION, parent_id=parent.id)
    root.sub_nodes = [parent]
    parent.sub_nodes = [child]
    tree.root = root
    tree.register(root)
    tree.register(parent)
    tree.register(child)
    return tree, root, parent, child


def _make_drift_signal(node_id: str = "n1234") -> DriftSignal:
    return DriftSignal(
        id="drift001",
        drift_type=DriftType.PHASE,
        severity=Severity.BLOCK,
        node_id=node_id,
        step_name="implement_minimal",
        evidence="requests.get() call on line 14",
        output_excerpt="import requests",
        correction_template="Remove I/O. Pure function only.",
    )


def _make_uncertainty_signal(node_id: str = "n1234") -> UncertaintySignal:
    return UncertaintySignal(
        id="unc001",
        uncertainty_type=UncertaintyType.AMBIGUOUS_SCOPE,
        node_id=node_id,
        step_name="implement_minimal",
        confidence=0.4,
        evidence="New helper function",
        output_excerpt="def _helper():",
        question="Is _helper() a legitimate addition?",
        option_a="Yes, keep it",
        option_b="No, remove it",
        default_resolution=Resolution.PROCEED,
    )


# ============================================================================
# compute_node_path
# ============================================================================


class TestComputeNodePath:
    def test_root_path(self):
        tree = TaskTree(session_id="s")
        root = TaskNode(name="root", description="r", primitive_type=PrimitiveType.ORCHESTRATION)
        tree.root = root
        tree.register(root)
        assert compute_node_path(tree, root) == "root"

    def test_child_path(self):
        tree, root, child = _make_tree_with_child()
        assert compute_node_path(tree, child) == "root_flow/child_model"

    def test_grandchild_path(self):
        tree, root, parent, child = _make_tree_with_grandchild()
        assert compute_node_path(tree, child) == "checkout_flow/payment_stage/validate_card"

    def test_mid_level_path(self):
        tree, root, parent, child = _make_tree_with_grandchild()
        assert compute_node_path(tree, parent) == "checkout_flow/payment_stage"

    def test_orphan_node(self):
        """Node not in tree returns just its name."""
        tree = TaskTree(session_id="s")
        orphan = TaskNode(name="orphan", description="o", primitive_type=PrimitiveType.MUTATION)
        assert compute_node_path(tree, orphan) == "orphan"

    def test_orphan_with_parent_id_not_in_tree(self):
        """Node with parent_id pointing to non-existent node."""
        tree = TaskTree(session_id="s")
        node = TaskNode(name="child", description="c", primitive_type=PrimitiveType.MUTATION, parent_id="nonexist")
        tree.register(node)
        assert compute_node_path(tree, node) == "child"


# ============================================================================
# ExecutionLogger — registration
# ============================================================================


class TestLoggerRegistration:
    def test_register_node_caches_path(self, tmp_path):
        tree, root, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "test-session")

        logger.register_node(tree, root)
        logger.register_node(tree, child)

        assert logger.get_node_path(root) == "root_flow"
        assert logger.get_node_path(child) == "root_flow/child_model"
        logger.close()

    def test_register_node_idempotent(self, tmp_path):
        tree, root, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "test-session")

        logger.register_node(tree, root)
        logger.register_node(tree, root)  # second call is no-op
        assert logger.get_node_path(root) == "root_flow"
        logger.close()

    def test_unregistered_node_returns_none(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "test-session")
        node = TaskNode(name="n", description="d", primitive_type=PrimitiveType.MUTATION)
        assert logger.get_node_path(node) is None
        logger.close()


# ============================================================================
# ExecutionLogger — execution log (JSONL)
# ============================================================================


class TestExecutionLog:
    def test_session_started(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        logger.session_started("s1", "test task")
        logger.close()

        entries = logger.read_execution_log()
        assert len(entries) == 1
        e = entries[0]
        assert e["event"] == "session_started"
        assert e["session_id"] == "s1"
        assert e["task"] == "test task"
        assert e["node_id"] is None
        assert e["node_path"] is None

    def test_session_complete(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        logger.session_complete("s1", 5000)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "session_complete"
        assert e["duration_ms"] == 5000

    def test_session_failed(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        logger.session_failed("s1", "abort signal", ["sig1", "sig2"])
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "session_failed"
        assert e["reason"] == "abort signal"
        assert e["signal_ids"] == ["sig1", "sig2"]

    def test_node_started(self, tmp_path):
        tree, root, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        logger.node_started(child)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "node_started"
        assert e["node_id"] == child.id
        assert e["node_path"] == "root_flow/child_model"
        assert e["node_type"] == "data_model"

    def test_node_complete(self, tmp_path):
        tree, root, _ = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, root)
        logger.node_complete(root, 12345)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "node_complete"
        assert e["duration_ms"] == 12345

    def test_node_blocked(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        sig = _make_drift_signal(child.id)
        logger.node_blocked(child, [sig])
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "node_blocked"
        assert e["signal_ids"] == ["drift001"]

    def test_node_failed(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        logger.node_failed(child, "max retries", ["sig1"])
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "node_failed"
        assert e["reason"] == "max retries"

    def test_node_awaiting_human(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        sig = _make_uncertainty_signal(child.id)
        logger.node_awaiting_human(child, [sig])
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "node_awaiting_human"
        assert e["signal_ids"] == ["unc001"]

    def test_multiple_events_append(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        logger.session_started("s1", "task")
        logger.session_complete("s1", 1000)
        logger.close()

        entries = logger.read_execution_log()
        assert len(entries) == 2
        assert entries[0]["event"] == "session_started"
        assert entries[1]["event"] == "session_complete"

    def test_ts_field_present(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        logger.session_started("s1", "t")
        logger.close()

        e = logger.read_execution_log()[0]
        assert "ts" in e
        assert e["ts"].endswith("Z")

    def test_file_path(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        assert logger.execution_log_path == tmp_path / "s1" / "execution_log.jsonl"
        logger.close()


# ============================================================================
# ExecutionLogger — step events
# ============================================================================


class TestStepEvents:
    def test_step_started(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.step_started(child, step)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "step_started"
        assert e["step"] == step.template.name
        assert e["attempt"] == 1

    def test_step_complete_with_tokens(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.step_complete(child, step, tokens_in=500, tokens_out=200, duration_ms=3000)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "step_complete"
        assert e["tokens_in"] == 500
        assert e["tokens_out"] == 200
        assert e["duration_ms"] == 3000

    def test_step_retrying(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        step.retry_count = 1
        logger.step_retrying(child, step)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "step_retrying"
        assert e["attempt"] == 2  # retry_count + 1

    def test_step_failed(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.step_failed(child, step)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "step_failed"


# ============================================================================
# ExecutionLogger — gate events
# ============================================================================


class TestGateEvents:
    def test_gate_started(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        gate = GateTemplate(name="no_any_types", check_type="ast_no_any")
        logger.gate_started(child, gate)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "gate_started"
        assert e["gate_name"] == "no_any_types"
        assert e["check_type"] == "ast_no_any"

    def test_gate_passed(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        gate = GateTemplate(name="no_any_types", check_type="ast_no_any")
        logger.gate_passed(child, gate, "No Any annotations found")
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "gate_passed"
        assert e["evidence"] == "No Any annotations found"

    def test_gate_failed(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        gate = GateTemplate(name="tests_pass", check_type="run_tests")
        logger.gate_failed(child, gate, "2 of 5 tests failed")
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "gate_failed"
        assert e["evidence"] == "2 of 5 tests failed"
        assert e["gate_name"] == "tests_pass"


# ============================================================================
# ExecutionLogger — signal events
# ============================================================================


class TestSignalEvents:
    def test_drift_detected(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        signal = _make_drift_signal(child.id)
        logger.drift_detected(child, step, signal)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "drift_detected"
        assert e["drift_type"] == "phase"
        assert e["severity"] == "block"
        assert e["signal_id"] == "drift001"

    def test_uncertainty_detected(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        signal = _make_uncertainty_signal(child.id)
        logger.uncertainty_detected(child, step, signal)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "uncertainty_detected"
        assert e["uncertainty_type"] == "ambiguous_scope"
        assert e["confidence"] == 0.4
        assert e["signal_id"] == "unc001"

    def test_human_resolved(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        signal = _make_uncertainty_signal()
        logger.human_resolved(signal, Resolution.PROCEED)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "human_resolved"
        assert e["signal_id"] == "unc001"
        assert e["resolution"] == "proceed"
        assert e["node_id"] is None  # session-level event

    def test_timeout_resolved(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        signal = _make_uncertainty_signal()
        logger.timeout_resolved(signal, Resolution.RETRY)
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "timeout_resolved"
        assert e["resolution"] == "retry"


# ============================================================================
# ExecutionLogger — LLM call events
# ============================================================================


class TestLLMCallEvents:
    def test_llm_call_started(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.llm_call_started(child, step, "step_execution", model="claude-sonnet-4-20250514")
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "llm_call_started"
        assert e["call_purpose"] == "step_execution"
        assert e["model"] == "claude-sonnet-4-20250514"

    def test_llm_call_complete(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.llm_call_complete(
            child, step, "step_execution",
            tokens_in=800, tokens_out=300, duration_ms=2500, model="claude-sonnet-4-20250514",
        )
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "llm_call_complete"
        assert e["tokens_in"] == 800
        assert e["tokens_out"] == 300
        assert e["duration_ms"] == 2500

    def test_llm_call_failed(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.llm_call_failed(child, step, "step_execution", "timeout after 120s")
        logger.close()

        e = logger.read_execution_log()[0]
        assert e["event"] == "llm_call_failed"
        assert e["error"] == "timeout after 120s"


# ============================================================================
# ExecutionLogger — content log
# ============================================================================


class TestContentLog:
    def test_log_prompt_creates_file(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]

        logger.log_prompt(child, step, attempt=1, prompt="Test prompt text")
        logger.close()

        # Check file exists at expected path
        dir_name = f"{child.id}_{child.name}"
        step_name = step.template.name
        expected = tmp_path / "s1" / "content_log" / dir_name / f"step_01_{step_name}.md"
        assert expected.exists()

        content = expected.read_text()
        assert "# step: " in content
        assert f"# node: {child.name}" in content
        assert "## Prompt" in content
        assert "Test prompt text" in content

    def test_log_response_appends(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]

        logger.log_prompt(child, step, 1, "Prompt")
        logger.log_response(child, step, 1, "Response text", outcome="complete")
        logger.close()

        dir_name = f"{child.id}_{child.name}"
        step_name = step.template.name
        path = tmp_path / "s1" / "content_log" / dir_name / f"step_01_{step_name}.md"
        content = path.read_text()

        assert "## Prompt" in content
        assert "Prompt" in content
        assert "## Response" in content
        assert "Response text" in content
        assert "# outcome: complete" in content

    def test_retry_gets_separate_file(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]

        logger.log_prompt(child, step, 1, "First attempt")
        logger.log_prompt(child, step, 2, "Retry attempt")
        logger.close()

        dir_name = f"{child.id}_{child.name}"
        step_name = step.template.name
        dir_path = tmp_path / "s1" / "content_log" / dir_name

        file1 = dir_path / f"step_01_{step_name}.md"
        file2 = dir_path / f"step_01_{step_name}_attempt2.md"
        assert file1.exists()
        assert file2.exists()
        assert "First attempt" in file1.read_text()
        assert "Retry attempt" in file2.read_text()

    def test_content_header_fields(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]

        logger.log_prompt(child, step, 1, "prompt", started_at="2026-03-10T14:00:00Z")
        logger.close()

        dir_name = f"{child.id}_{child.name}"
        step_name = step.template.name
        path = tmp_path / "s1" / "content_log" / dir_name / f"step_01_{step_name}.md"
        content = path.read_text()

        assert f"# step: {step_name}" in content
        assert f"# node: {child.name} [{child.id}]" in content
        assert "# path: root_flow/child_model" in content
        assert "# node_type: data_model" in content
        assert "# attempt: 1" in content
        assert "# started: 2026-03-10T14:00:00Z" in content

    def test_response_with_drift_signal(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        signal = _make_drift_signal(child.id)

        logger.log_prompt(child, step, 1, "prompt")
        logger.log_response(child, step, 1, "bad output", signals=[signal], outcome="drift_detected (phase/block)")
        logger.close()

        dir_name = f"{child.id}_{child.name}"
        step_name = step.template.name
        path = tmp_path / "s1" / "content_log" / dir_name / f"step_01_{step_name}.md"
        content = path.read_text()

        assert "## Signals" in content
        assert "### Drift detected" in content
        assert "type: phase" in content
        assert "severity: block" in content
        assert "requests.get()" in content

    def test_response_with_uncertainty_signal(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        signal = _make_uncertainty_signal(child.id)

        logger.log_prompt(child, step, 1, "prompt")
        logger.log_response(child, step, 1, "output", signals=[signal])
        logger.close()

        dir_name = f"{child.id}_{child.name}"
        step_name = step.template.name
        path = tmp_path / "s1" / "content_log" / dir_name / f"step_01_{step_name}.md"
        content = path.read_text()

        assert "### Uncertainty detected" in content
        assert "type: ambiguous_scope" in content
        assert "confidence: 0.4" in content

    def test_step_index_zero_padded(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)

        # Log for 3rd step (index 3 → "03")
        if len(child.steps) >= 3:
            step = child.steps[2]
            logger.log_prompt(child, step, 1, "prompt")
            logger.close()

            dir_name = f"{child.id}_{child.name}"
            step_name = step.template.name
            path = tmp_path / "s1" / "content_log" / dir_name / f"step_03_{step_name}.md"
            assert path.exists()
        else:
            logger.close()


# ============================================================================
# ExecutionLogger — round-trip
# ============================================================================


class TestRoundTrip:
    def test_full_event_sequence(self, tmp_path):
        """Write a realistic event sequence, read back, verify structure."""
        tree, root, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, root)
        logger.register_node(tree, child)

        step = child.steps[0]

        logger.session_started("s1", "test task")
        logger.node_started(root)
        logger.node_started(child)
        logger.step_started(child, step)
        logger.llm_call_started(child, step, "step_execution", model="claude-sonnet-4-20250514")
        logger.llm_call_complete(child, step, "step_execution", tokens_in=500, tokens_out=200, duration_ms=2000)
        logger.step_complete(child, step, tokens_in=500, tokens_out=200, duration_ms=2100)
        logger.node_complete(child, 2200)
        logger.node_complete(root, 2300)
        logger.session_complete("s1", 2400)
        logger.close()

        entries = logger.read_execution_log()
        assert len(entries) == 10

        events = [e["event"] for e in entries]
        assert events == [
            "session_started",
            "node_started", "node_started",
            "step_started",
            "llm_call_started", "llm_call_complete",
            "step_complete",
            "node_complete", "node_complete",
            "session_complete",
        ]

        # All entries have ts and session_id
        for e in entries:
            assert "ts" in e
            assert e["session_id"] == "s1"

    def test_execution_log_is_valid_jsonl(self, tmp_path):
        """Every line in the log must be valid JSON."""
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)

        logger.session_started("s1", "task")
        logger.node_started(child)
        logger.session_complete("s1", 1000)
        logger.close()

        with open(logger.execution_log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    json.loads(line)  # must not raise


# ============================================================================
# ExecutionLogger — append mode (resume)
# ============================================================================


class TestAppendMode:
    def test_reopen_appends(self, tmp_path):
        """Opening a second logger on same session appends, doesn't overwrite."""
        tree, _, child = _make_tree_with_child()

        logger1 = ExecutionLogger(tmp_path, "s1")
        logger1.session_started("s1", "task")
        logger1.close()

        logger2 = ExecutionLogger(tmp_path, "s1")
        logger2.session_complete("s1", 5000)
        logger2.close()

        entries = logger2.read_execution_log()
        assert len(entries) == 2
        assert entries[0]["event"] == "session_started"
        assert entries[1]["event"] == "session_complete"


# ============================================================================
# NullLogger
# ============================================================================


class TestNullLogger:
    def test_all_methods_are_noop(self):
        """NullLogger methods don't raise."""
        logger = NullLogger()
        tree = TaskTree(session_id="s")
        node = TaskNode(name="n", description="d", primitive_type=PrimitiveType.MUTATION)
        step = node.steps[0]
        gate = GateTemplate(name="g", check_type="ast_no_any")
        drift_sig = _make_drift_signal()
        unc_sig = _make_uncertainty_signal()

        # None of these should raise
        logger.register_node(tree, node)
        logger.session_started("s", "t")
        logger.session_complete("s", 0)
        logger.session_failed("s", "r", [])
        logger.node_started(node)
        logger.node_complete(node, 0)
        logger.node_blocked(node, [drift_sig])
        logger.node_failed(node, "r", [])
        logger.node_awaiting_human(node, [unc_sig])
        logger.step_started(node, step)
        logger.step_complete(node, step)
        logger.step_retrying(node, step)
        logger.step_failed(node, step)
        logger.gate_started(node, gate)
        logger.gate_passed(node, gate, "e")
        logger.gate_failed(node, gate, "e")
        logger.drift_detected(node, step, drift_sig)
        logger.uncertainty_detected(node, step, unc_sig)
        logger.human_resolved(unc_sig, Resolution.PROCEED)
        logger.timeout_resolved(unc_sig, Resolution.RETRY)
        logger.llm_call_started(node, step, "p")
        logger.llm_call_complete(node, step, "p")
        logger.llm_call_failed(node, step, "p", "e")
        logger.log_prompt(node, step, 1, "p")
        logger.log_response(node, step, 1, "r")
        logger.close()

    def test_get_node_path_returns_none(self):
        logger = NullLogger()
        node = TaskNode(name="n", description="d", primitive_type=PrimitiveType.MUTATION)
        assert logger.get_node_path(node) is None


# ============================================================================
# Runner integration — logger wired in
# ============================================================================


class TestRunnerLoggerIntegration:
    """Verify the runner writes to the logger during execution."""

    def test_runner_produces_execution_log(self, tmp_path):
        from superpowers_runner.runner.runner import Runner

        tree = TaskTree(session_id="log-test")
        node = TaskNode(name="test_node", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        class MockLLM:
            def call(self, prompt, max_tokens=4096, system=None):
                return "def f(x: int) -> int:\n    return x\n"

        logger = ExecutionLogger(tmp_path, "log-test")

        # Pre-pass gates
        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner = Runner(tree=tree, llm_client=MockLLM(), logger=logger)
        runner.run()
        logger.close()

        entries = logger.read_execution_log()
        events = [e["event"] for e in entries]

        assert "session_started" in events
        assert "node_started" in events
        assert "step_started" in events
        assert "step_complete" in events
        assert "session_complete" in events

        # node_path should be set
        node_events = [e for e in entries if e.get("node_id") == node.id]
        assert all(e["node_path"] == "test_node" for e in node_events)

    def test_runner_produces_content_log(self, tmp_path):
        from superpowers_runner.runner.runner import Runner

        tree = TaskTree(session_id="content-test")
        node = TaskNode(name="my_node", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        class MockLLM:
            def call(self, prompt, max_tokens=4096, system=None):
                return "output text"

        logger = ExecutionLogger(tmp_path, "content-test")

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner = Runner(tree=tree, llm_client=MockLLM(), logger=logger)
        runner.run()
        logger.close()

        # Content log directory for this node should exist
        content_dir = tmp_path / "content-test" / "content_log"
        node_dirs = list(content_dir.iterdir())
        assert len(node_dirs) >= 1

        # At least one step file should exist
        step_files = list(node_dirs[0].glob("step_*.md"))
        assert len(step_files) >= 1

        # File should contain prompt and response
        content = step_files[0].read_text()
        assert "## Prompt" in content
        assert "## Response" in content

    def test_runner_without_logger_still_works(self, tmp_path):
        from superpowers_runner.runner.runner import Runner

        tree = TaskTree(session_id="no-log")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        class MockLLM:
            def call(self, prompt, max_tokens=4096, system=None):
                return "output"

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner = Runner(tree=tree, llm_client=MockLLM())
        runner.run()
        assert tree.is_complete()


# ============================================================================
# ExecutionLogger — directory creation
# ============================================================================


class TestDirectoryCreation:
    def test_creates_session_dir(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "new-session")
        assert (tmp_path / "new-session").is_dir()
        logger.close()

    def test_creates_content_log_dir(self, tmp_path):
        logger = ExecutionLogger(tmp_path, "s1")
        assert (tmp_path / "s1" / "content_log").is_dir()
        logger.close()

    def test_creates_node_subdir_on_content_write(self, tmp_path):
        tree, _, child = _make_tree_with_child()
        logger = ExecutionLogger(tmp_path, "s1")
        logger.register_node(tree, child)
        step = child.steps[0]
        logger.log_prompt(child, step, 1, "text")
        logger.close()

        dir_name = f"{child.id}_{child.name}"
        assert (tmp_path / "s1" / "content_log" / dir_name).is_dir()
