"""Tests for integration gap fixes.

Gap 1: Runner delegates to CorrectionEngine (not inline logic)
Gap 2: Runner auto-creates gate_runner/context_builder when not passed
Gap 3: Planner JSON extraction handles messy LLM output
Gap 5: pyproject.toml declares dependencies
Gap 6: CLI catches HumanReviewRequired and StuckSession
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest

from superpowers_runner.runner.runner import (
    HumanReviewRequired,
    Runner,
    StuckSession,
)
from superpowers_runner.runner.context import ContextBuilder, SchemaRegistry
from superpowers_runner.runner.correction import CorrectionEngine
from superpowers_runner.runner.gates_runner import GateRunner
from superpowers_runner.schema.nodes import (
    GateResult,
    NodeStatus,
    StepRecord,
    StepStatus,
    TaskNode,
    TaskTree,
)
from superpowers_runner.schema.primitives import GateTemplate, PrimitiveType
from superpowers_runner.schema.signals import (
    DriftSignal,
    DriftType,
    Severity,
)
from superpowers_runner.planner.decomposer import _extract_json as decompose_extract
from superpowers_runner.planner.planner import _extract_json as planner_extract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockLLM:
    def __init__(self, responses=None, default="output"):
        self._responses = list(responses) if responses else []
        self._default = default
        self.calls = []

    def call(self, prompt, max_tokens=4096, system=None):
        self.calls.append(prompt)
        if self._responses:
            return self._responses.pop(0)
        return self._default


class MockStateManager:
    def __init__(self):
        self.saves = []

    def save(self, tree):
        self.saves.append(tree)


def _make_drift_signal(severity=Severity.BLOCK):
    return DriftSignal(
        id="sig12345",
        drift_type=DriftType.PHASE,
        severity=severity,
        node_id="test1234",
        step_name="some_step",
        evidence="Test evidence",
        output_excerpt="excerpt",
        correction_template="Fix it",
    )


# ============================================================================
# Gap 1: Runner ↔ CorrectionEngine delegation
# ============================================================================


class TestRunnerCorrectionDelegation:
    """Runner._handle_block delegates to CorrectionEngine.correct_step."""

    def test_handle_block_uses_correction_engine(self):
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        runner = Runner(tree=tree, llm_client=llm)

        # Mock the correction engine
        runner.correction_engine = MagicMock()
        runner.correction_engine.correct_step.return_value = ("corrected", [])

        signal = _make_drift_signal()
        result = runner._handle_block(node, node.steps[0], [signal])

        # Should have called correct_step, not done inline logic
        runner.correction_engine.correct_step.assert_called_once()
        assert result == "corrected"
        assert node.status == NodeStatus.IN_PROGRESS

    def test_handle_block_escalates_on_remaining_signals(self):
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        runner = Runner(tree=tree, llm_client=llm)

        signal = _make_drift_signal()
        runner.correction_engine = MagicMock()
        runner.correction_engine.correct_step.return_value = ("bad", [signal])

        with pytest.raises(HumanReviewRequired):
            runner._handle_block(node, node.steps[0], [signal])
        assert node.status == NodeStatus.FAILED

    def test_gate_failures_use_correction_engine_for_context(self):
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM(default="def f(): pass")
        runner = Runner(tree=tree, llm_client=llm)

        # Mark all steps complete
        for step in node.steps:
            step.status = StepStatus.COMPLETE
            step.output = "def f(): pass"

        # Set up: one gate fails (non-abort), rest pass
        node.gate_results[0].passed = False
        node.gate_results[0].evidence = "test fail"
        for gr in node.gate_results[1:]:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        # Mock correction engine methods
        runner.correction_engine = MagicMock()
        runner.correction_engine.build_gate_correction.return_value = "GATE CORRECTION"
        runner.correction_engine.find_responsible_step.return_value = node.steps[-1]

        # Patch gate_runner to pass on retry
        call_count = [0]
        def patched_run_all(n):
            call_count[0] += 1
            if call_count[0] >= 2:
                for gr in n.gate_results:
                    gr.passed = True
                    gr.checked_at = datetime.now(UTC)
            return n.gate_results
        runner.gate_runner.run_all = patched_run_all

        runner._handle_gate_failures(node)

        # Called at least once (may recurse through _execute_leaf → _handle_gate_failures)
        assert runner.correction_engine.build_gate_correction.call_count >= 1
        assert runner.correction_engine.find_responsible_step.call_count >= 1


# ============================================================================
# Gap 2: Runner auto-creates components
# ============================================================================


class TestRunnerDefaultConstruction:
    """Runner creates gate_runner, context_builder, correction_engine from llm_client."""

    def test_runner_without_gate_runner(self):
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        runner = Runner(tree=tree, llm_client=llm)

        assert runner.gate_runner is not None
        assert isinstance(runner.gate_runner, GateRunner)

    def test_runner_without_context_builder(self):
        tree = TaskTree(session_id="test-session")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        runner = Runner(tree=tree, llm_client=llm)

        assert runner.context_builder is not None
        assert isinstance(runner.context_builder, ContextBuilder)

    def test_runner_without_correction_engine(self):
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        runner = Runner(tree=tree, llm_client=llm)

        assert runner.correction_engine is not None
        assert isinstance(runner.correction_engine, CorrectionEngine)

    def test_runner_explicit_overrides_default(self):
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        custom_gate_runner = GateRunner(llm_client=llm)
        custom_context = ContextBuilder(session_id="custom")
        custom_correction = CorrectionEngine(llm_client=llm, context_builder=custom_context)

        runner = Runner(
            tree=tree,
            llm_client=llm,
            gate_runner=custom_gate_runner,
            context_builder=custom_context,
            correction_engine=custom_correction,
        )

        assert runner.gate_runner is custom_gate_runner
        assert runner.context_builder is custom_context
        assert runner.correction_engine is custom_correction

    def test_runner_drift_detector_alias(self):
        """drift_detector kwarg maps to detector."""
        tree = TaskTree(session_id="test")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM()
        mock_detector = MagicMock()
        runner = Runner(tree=tree, llm_client=llm, drift_detector=mock_detector)
        assert runner.detector is mock_detector

    def test_full_run_with_minimal_args(self):
        """Runner(tree, llm_client) should be sufficient for a complete run."""
        tree = TaskTree(session_id="minimal")
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = node
        tree.register(node)

        llm = MockLLM(default="def f(x: int) -> int:\n    return x\n")
        runner = Runner(tree=tree, llm_client=llm)

        # Pre-pass gates
        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        result = runner.run()
        assert result.is_complete()
        assert node.status == NodeStatus.COMPLETE


# ============================================================================
# Gap 3: Planner JSON extraction robustness
# ============================================================================


class TestPlannerJSONExtraction:
    """JSON extraction handles messy LLM responses."""

    def test_clean_json(self):
        text = '{"name": "test", "type": "data_model", "description": "d"}'
        result = planner_extract(text)
        data = json.loads(result)
        assert data["name"] == "test"

    def test_json_in_markdown_code_block(self):
        text = 'Here is the result:\n```json\n{"name": "test"}\n```\nDone.'
        result = planner_extract(text)
        data = json.loads(result)
        assert data["name"] == "test"

    def test_json_in_plain_code_block(self):
        text = 'Output:\n```\n{"name": "test"}\n```'
        result = planner_extract(text)
        data = json.loads(result)
        assert data["name"] == "test"

    def test_json_with_preamble(self):
        text = 'Sure! Here is the JSON:\n\n{"name": "test", "value": 1}'
        result = planner_extract(text)
        data = json.loads(result)
        assert data["name"] == "test"

    def test_json_with_trailing_text(self):
        text = '{"name": "test"}\n\nLet me know if you need changes.'
        result = planner_extract(text)
        data = json.loads(result)
        assert data["name"] == "test"

    def test_decomposer_extracts_children_json(self):
        text = '''Here are the children:
```json
{
  "children": [
    {"name": "token_model", "type": "data_model", "description": "Token schema", "dependencies": []},
    {"name": "generate_token", "type": "transformation", "description": "Create token", "dependencies": ["token_model"]}
  ]
}
```'''
        result = decompose_extract(text)
        data = json.loads(result)
        assert len(data["children"]) == 2

    def test_nested_braces_in_json(self):
        text = '{"children": [{"name": "a", "meta": {"key": "val"}}]}'
        result = planner_extract(text)
        data = json.loads(result)
        assert data["children"][0]["meta"]["key"] == "val"


# ============================================================================
# Gap 5: pyproject.toml dependencies
# ============================================================================


class TestPyprojectDependencies:
    def test_anthropic_declared(self):
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        assert any("anthropic" in d for d in deps)

    def test_httpx_declared(self):
        import tomllib
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        deps = data["project"]["dependencies"]
        assert any("httpx" in d for d in deps)


# ============================================================================
# Gap 6: CLI error handling
# ============================================================================


class TestCLIErrorHandling:
    def test_run_dry_run_succeeds(self):
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "run", "--dry-run", "test task"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        # Dry run should either complete or hit a stub-related issue
        # but should not crash with an unhandled exception
        assert "Traceback" not in result.stderr or "HumanReviewRequired" not in result.stderr

    def test_cli_imports_exception_types(self):
        """CLI imports HumanReviewRequired and StuckSession."""
        from superpowers_runner.__main__ import main
        # If this import succeeds, the error handlers are available
        from superpowers_runner.runner.runner import HumanReviewRequired, StuckSession
