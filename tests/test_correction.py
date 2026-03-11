"""Tests for runner/correction.py."""

from __future__ import annotations

import pytest

from superpowers_runner.runner.correction import CorrectionEngine
from superpowers_runner.schema.nodes import (
    GateResult,
    NodeSchema,
    SchemaField,
    StepRecord,
    StepStatus,
    TaskNode,
)
from superpowers_runner.schema.primitives import GateTemplate, PrimitiveType, StepTemplate
from superpowers_runner.schema.signals import DriftSignal, DriftType, Severity


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockLLM:
    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ["corrected output"]
        self._call_count = 0
        self.last_prompt = ""

    def call(self, prompt: str, max_tokens: int = 4096, system: str | None = None) -> str:
        self.last_prompt = prompt
        result = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1
        return result


class MockContextBuilder:
    def build(self, **kwargs) -> str:
        correction = kwargs.get("correction_context", "")
        return f"PROMPT: {correction}"


class MockDetector:
    def __init__(self, retry_signals: list[DriftSignal] | None = None):
        self._retry_signals = retry_signals or []

    def check_all(self, node, step, output, gate_results=None) -> list[DriftSignal]:
        return self._retry_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node() -> TaskNode:
    return TaskNode(
        name="test_node",
        description="Test",
        primitive_type=PrimitiveType.TRANSFORMATION,
    )


def _make_step(name: str = "implement_minimal") -> StepRecord:
    return StepRecord(
        template=StepTemplate(
            name=name,
            prompt_template="Do it",
            expected_artifacts=["implementation_code"],
            forbidden_artifacts=[],
        ),
        status=StepStatus.ACTIVE,
    )


def _make_drift(
    dtype: DriftType = DriftType.SCOPE,
    severity: Severity = Severity.BLOCK,
) -> DriftSignal:
    return DriftSignal(
        id="d001",
        drift_type=dtype,
        severity=severity,
        node_id="node01",
        step_name="implement",
        evidence="TokenStore not in spec",
        output_excerpt="class TokenStore:",
        correction_template="Remove TokenStore",
    )


def _make_gate_result(
    name: str = "no_any_types",
    check_type: str = "ast_no_any",
    passed: bool = False,
    evidence: str = "Any found",
) -> GateResult:
    return GateResult(
        gate=GateTemplate(name=name, check_type=check_type),
        passed=passed,
        evidence=evidence,
    )


# ============================================================================
# CorrectionEngine — correct_step
# ============================================================================


class TestCorrectStep:
    def test_successful_correction(self):
        engine = CorrectionEngine(
            llm_client=MockLLM(["fixed output"]),
            context_builder=MockContextBuilder(),
            detector=MockDetector(retry_signals=[]),
        )
        step = _make_step()
        node = _make_node()
        signals = [_make_drift()]

        output, remaining = engine.correct_step(node, step, signals)
        assert output == "fixed output"
        assert remaining == []
        assert step.retry_count == 1

    def test_correction_fails_on_retry(self):
        persistent_signal = _make_drift(severity=Severity.BLOCK)
        engine = CorrectionEngine(
            llm_client=MockLLM(["still bad"]),
            context_builder=MockContextBuilder(),
            detector=MockDetector(retry_signals=[persistent_signal]),
        )
        step = _make_step()
        node = _make_node()
        signals = [_make_drift()]

        output, remaining = engine.correct_step(node, step, signals)
        assert output == "still bad"
        assert len(remaining) == 1
        assert remaining[0].severity == Severity.BLOCK

    def test_max_retries_exceeded(self):
        engine = CorrectionEngine(
            llm_client=MockLLM(),
            context_builder=MockContextBuilder(),
        )
        step = _make_step()
        step.retry_count = step.max_retries  # already at max
        node = _make_node()

        output, remaining = engine.correct_step(node, step, [_make_drift()])
        assert output == ""
        assert len(remaining) == 1

    def test_correction_sets_step_status(self):
        engine = CorrectionEngine(
            llm_client=MockLLM(),
            context_builder=MockContextBuilder(),
            detector=MockDetector(),
        )
        step = _make_step()
        node = _make_node()
        engine.correct_step(node, step, [_make_drift()])
        assert step.status == StepStatus.RETRYING

    def test_correction_includes_context(self):
        llm = MockLLM()
        engine = CorrectionEngine(
            llm_client=llm,
            context_builder=MockContextBuilder(),
            detector=MockDetector(),
        )
        step = _make_step()
        node = _make_node()
        signals = [_make_drift()]
        engine.correct_step(node, step, signals)
        assert "DRIFT CORRECTION REQUIRED" in llm.last_prompt

    def test_no_detector_assumes_success(self):
        engine = CorrectionEngine(
            llm_client=MockLLM(["output"]),
            context_builder=MockContextBuilder(),
            detector=None,
        )
        step = _make_step()
        node = _make_node()
        output, remaining = engine.correct_step(node, step, [_make_drift()])
        assert output == "output"
        assert remaining == []


# ============================================================================
# CorrectionEngine — build_correction_context
# ============================================================================


class TestBuildCorrectionContext:
    def test_includes_all_block_signals(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        s1 = _make_drift(dtype=DriftType.SCOPE)
        s2 = _make_drift(dtype=DriftType.PHASE)
        context = engine.build_correction_context([s1, s2])
        assert "SCOPE DRIFT" in context
        assert "PHASE DRIFT" in context

    def test_skips_non_block_signals(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        warn_signal = _make_drift(severity=Severity.WARN)
        context = engine.build_correction_context([warn_signal])
        assert "DRIFT" in context  # header is there
        # But no specific signal content since it's WARN not BLOCK
        assert "SCOPE DRIFT" not in context

    def test_includes_correction_template(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        signal = _make_drift()
        context = engine.build_correction_context([signal])
        assert "Remove TokenStore" in context


# ============================================================================
# CorrectionEngine — build_gate_correction
# ============================================================================


class TestBuildGateCorrection:
    def test_includes_all_failing_gates(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        g1 = _make_gate_result(name="no_any_types", evidence="Any found on line 5")
        g2 = _make_gate_result(name="tests_exist", evidence="No test file found")
        context = engine.build_gate_correction([g1, g2])
        assert "no_any_types" in context
        assert "tests_exist" in context
        assert "Any found on line 5" in context

    def test_includes_check_type(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        g = _make_gate_result(check_type="run_tests")
        context = engine.build_gate_correction([g])
        assert "run_tests" in context


# ============================================================================
# CorrectionEngine — find_responsible_step
# ============================================================================


class TestFindResponsibleStep:
    def test_test_gate_finds_test_step(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        node = _make_node()
        # Mark some steps as complete
        for step in node.steps:
            step.status = StepStatus.COMPLETE

        gate = _make_gate_result(check_type="run_tests")
        step = engine.find_responsible_step(node, gate)
        assert "test" in step.template.name

    def test_ast_gate_finds_implementation_step(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        node = _make_node()
        for step in node.steps:
            step.status = StepStatus.COMPLETE

        gate = _make_gate_result(check_type="ast_no_any")
        step = engine.find_responsible_step(node, gate)
        assert "implement" in step.template.name

    def test_fallback_to_last_completed(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        node = _make_node()
        # Only complete first step
        node.steps[0].status = StepStatus.COMPLETE

        gate = _make_gate_result(check_type="unknown_check")
        step = engine.find_responsible_step(node, gate)
        assert step == node.steps[0]

    def test_last_resort_first_step(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        node = _make_node()
        # No completed steps
        gate = _make_gate_result(check_type="unknown_check")
        step = engine.find_responsible_step(node, gate)
        assert step == node.steps[0]

    def test_docstring_gate_finds_doc_step(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        # Use DATA_MODEL which has a document_invariants step
        node = TaskNode(
            name="test_model",
            description="Test",
            primitive_type=PrimitiveType.DATA_MODEL,
        )
        for step in node.steps:
            step.status = StepStatus.COMPLETE

        gate = _make_gate_result(check_type="has_docstring")
        step = engine.find_responsible_step(node, gate)
        assert "document" in step.template.name or "implement" in step.template.name

    def test_children_gate_finds_enumerate_step(self):
        engine = CorrectionEngine(MockLLM(), MockContextBuilder())
        node = TaskNode(
            name="test_orch",
            description="Test",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )
        for step in node.steps:
            step.status = StepStatus.COMPLETE

        gate = _make_gate_result(check_type="children_have_types")
        step = engine.find_responsible_step(node, gate)
        assert "enumerate" in step.template.name or "children" in step.template.name
