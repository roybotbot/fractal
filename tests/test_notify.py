"""Tests for notify/notifier.py and notify/display.py."""

from __future__ import annotations

import time

import pytest

from superpowers_runner.notify.notifier import Notifier
from superpowers_runner.notify.display import (
    format_drift_signal,
    format_drift_signals,
    format_uncertainty_batch,
)
from superpowers_runner.schema.signals import (
    BATCH_FLUSH_SIGNAL_COUNT,
    DEFAULT_TIMEOUT_RESOLUTION,
    DriftSignal,
    DriftType,
    MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT,
    Resolution,
    Severity,
    UncertaintySignal,
    UncertaintyType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_uncertainty(
    utype: UncertaintyType = UncertaintyType.AMBIGUOUS_SCOPE,
    confidence: float = 0.4,
    question: str = "Is this A or B?",
    default_resolution: Resolution = Resolution.PROCEED,
) -> UncertaintySignal:
    return UncertaintySignal(
        id="test01",
        uncertainty_type=utype,
        node_id="node01",
        step_name="implement",
        confidence=confidence,
        evidence="Test evidence",
        output_excerpt="output...",
        question=question,
        option_a="Option A",
        option_b="Option B",
        default_resolution=default_resolution,
    )


def _make_drift(
    dtype: DriftType = DriftType.SCOPE,
    severity: Severity = Severity.BLOCK,
) -> DriftSignal:
    return DriftSignal(
        id="drift01",
        drift_type=dtype,
        severity=severity,
        node_id="node01",
        step_name="implement",
        evidence="Scope drift detected",
        output_excerpt="class TokenStore:",
        correction_template="Remove TokenStore",
    )


# ============================================================================
# Notifier — interrupt mode
# ============================================================================


class TestNotifierInterrupt:
    def test_interrupt_with_human_response(self):
        signal = _make_uncertainty()

        def mock_human(signals):
            return [(Resolution.PROCEED, "looks fine")]

        notifier = Notifier(human_input=mock_human)
        resolutions = notifier.interrupt([signal])
        assert resolutions == [Resolution.PROCEED]
        assert signal.resolved_by == "human"
        assert signal.human_note == "looks fine"
        assert signal.resolution == Resolution.PROCEED

    def test_interrupt_timeout_applies_defaults(self):
        signal = _make_uncertainty(
            utype=UncertaintyType.AMBIGUOUS_SCOPE,
            default_resolution=Resolution.PROCEED,
        )

        def mock_timeout(signals):
            return None  # timeout

        notifier = Notifier(human_input=mock_timeout)
        resolutions = notifier.interrupt([signal])
        assert resolutions == [Resolution.PROCEED]
        assert signal.resolved_by == "timeout"

    def test_interrupt_resets_auto_count_on_human_response(self):
        notifier = Notifier(human_input=lambda s: [(Resolution.PROCEED, "")])

        # First: simulate some timeouts via buffer defaults
        notifier._auto_resolution_count = 3
        assert notifier.auto_resolution_count == 3

        signal = _make_uncertainty()
        notifier.interrupt([signal])
        assert notifier.auto_resolution_count == 0

    def test_interrupt_increments_auto_count_on_timeout(self):
        notifier = Notifier(human_input=lambda s: None)
        signal = _make_uncertainty()
        notifier.interrupt([signal])
        assert notifier.auto_resolution_count == 1

    def test_multiple_signals_interrupt(self):
        s1 = _make_uncertainty(utype=UncertaintyType.SCHEMA_NEAR_MISS)
        s2 = _make_uncertainty(utype=UncertaintyType.SELF_CONTRADICTION)

        def mock_human(signals):
            return [
                (Resolution.RETRY, "rename it"),
                (Resolution.ESCALATE, "looks wrong"),
            ]

        notifier = Notifier(human_input=mock_human)
        resolutions = notifier.interrupt([s1, s2])
        assert resolutions == [Resolution.RETRY, Resolution.ESCALATE]
        assert s1.human_note == "rename it"
        assert s2.human_note == "looks wrong"


# ============================================================================
# Notifier — batch mode
# ============================================================================


class TestNotifierBatch:
    def test_buffer_adds_signals(self):
        notifier = Notifier()
        signal = _make_uncertainty()
        notifier.buffer([signal])
        assert notifier.buffer_size == 1

    def test_should_flush_on_count(self):
        notifier = Notifier()
        for _ in range(BATCH_FLUSH_SIGNAL_COUNT):
            notifier.buffer([_make_uncertainty()])
        assert notifier.should_flush()

    def test_should_not_flush_under_count(self):
        notifier = Notifier()
        notifier.buffer([_make_uncertainty()])
        assert not notifier.should_flush()

    def test_drain_clears_buffer(self):
        notifier = Notifier()
        notifier.buffer([_make_uncertainty()])
        notifier.buffer([_make_uncertainty()])
        drained = notifier.drain()
        assert len(drained) == 2
        assert notifier.buffer_size == 0

    def test_flush_for_node_end(self):
        notifier = Notifier()
        assert not notifier.flush_for_node_end()
        notifier.buffer([_make_uncertainty()])
        assert notifier.flush_for_node_end()

    def test_notify_batch_with_response(self):
        s1 = _make_uncertainty()
        s2 = _make_uncertainty()

        def mock_human(signals):
            return [(Resolution.PROCEED, ""), (Resolution.RETRY, "incomplete")]

        notifier = Notifier(human_input=mock_human)
        resolutions = notifier.notify_batch([s1, s2])
        assert resolutions == [Resolution.PROCEED, Resolution.RETRY]
        assert s2.human_note == "incomplete"

    def test_notify_batch_timeout(self):
        s1 = _make_uncertainty(default_resolution=Resolution.PROCEED)
        notifier = Notifier(human_input=lambda s: None)
        resolutions = notifier.notify_batch([s1])
        assert resolutions == [Resolution.PROCEED]
        assert s1.resolved_by == "timeout"

    def test_notify_batch_empty(self):
        notifier = Notifier()
        resolutions = notifier.notify_batch([])
        assert resolutions == []


# ============================================================================
# Notifier — safety valve
# ============================================================================


class TestSafetyValve:
    def test_force_interrupt_after_threshold(self):
        notifier = Notifier(human_input=lambda s: None)

        for _ in range(MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT):
            notifier.interrupt([_make_uncertainty()])

        assert notifier.force_interrupt_needed
        # Buffer should also trigger flush
        notifier.buffer([_make_uncertainty()])
        assert notifier.should_flush()

    def test_human_response_resets_safety_valve(self):
        notifier = Notifier(human_input=lambda s: None)

        for _ in range(3):
            notifier.interrupt([_make_uncertainty()])
        assert notifier.auto_resolution_count == 3

        # Now human responds
        notifier._human_input = lambda s: [(Resolution.PROCEED, "")]
        notifier.interrupt([_make_uncertainty()])
        assert notifier.auto_resolution_count == 0
        assert not notifier.force_interrupt_needed


# ============================================================================
# Notifier — default resolution per type
# ============================================================================


class TestDefaultResolutions:
    def test_ambiguous_scope_defaults_proceed(self):
        signal = _make_uncertainty(utype=UncertaintyType.AMBIGUOUS_SCOPE)
        notifier = Notifier(human_input=lambda s: None)
        [res] = notifier.interrupt([signal])
        assert res == Resolution.PROCEED

    def test_partial_adherence_defaults_retry(self):
        signal = _make_uncertainty(utype=UncertaintyType.PARTIAL_ADHERENCE)
        notifier = Notifier(human_input=lambda s: None)
        [res] = notifier.interrupt([signal])
        assert res == Resolution.RETRY

    def test_self_contradiction_defaults_escalate(self):
        signal = _make_uncertainty(utype=UncertaintyType.SELF_CONTRADICTION)
        notifier = Notifier(human_input=lambda s: None)
        [res] = notifier.interrupt([signal])
        assert res == Resolution.ESCALATE

    def test_schema_near_miss_defaults_retry(self):
        signal = _make_uncertainty(utype=UncertaintyType.SCHEMA_NEAR_MISS)
        notifier = Notifier(human_input=lambda s: None)
        [res] = notifier.interrupt([signal])
        assert res == Resolution.RETRY

    def test_suspiciously_fast_defaults_retry(self):
        signal = _make_uncertainty(utype=UncertaintyType.SUSPICIOUSLY_FAST)
        notifier = Notifier(human_input=lambda s: None)
        [res] = notifier.interrupt([signal])
        assert res == Resolution.RETRY


# ============================================================================
# Display — uncertainty batch
# ============================================================================


class TestDisplayUncertainty:
    def test_format_single_signal(self):
        signal = _make_uncertainty(
            utype=UncertaintyType.AMBIGUOUS_SCOPE,
            confidence=0.41,
            question="Is TokenStore a helper (A) or scope drift (B)?",
        )
        output = format_uncertainty_batch(
            [signal],
            session_id="auth-abc123",
            node_name="generate_token",
            node_type="transformation",
        )
        assert "HUMAN REVIEW NEEDED" in output
        assert "[1 signal]" in output
        assert "auth-abc123" in output
        assert "generate_token (transformation)" in output
        assert "AMBIGUOUS SCOPE" in output
        assert "0.41" in output
        assert "A / B / show-more" in output

    def test_format_multiple_signals(self):
        s1 = _make_uncertainty(utype=UncertaintyType.AMBIGUOUS_SCOPE)
        s2 = _make_uncertainty(utype=UncertaintyType.SUSPICIOUSLY_FAST)
        output = format_uncertainty_batch([s1, s2])
        assert "[2 signals]" in output
        assert "[1/2]" in output
        assert "[2/2]" in output

    def test_format_timeout_display(self):
        signal = _make_uncertainty()
        output = format_uncertainty_batch([signal], timeout_seconds=300)
        assert "5:00" in output

    def test_format_no_session(self):
        signal = _make_uncertainty()
        output = format_uncertainty_batch([signal])
        assert "Session:" not in output

    def test_format_step_name(self):
        signal = _make_uncertainty()
        signal.step_name = "enumerate_edge_cases"
        output = format_uncertainty_batch([signal])
        assert "enumerate_edge_cases" in output


# ============================================================================
# Display — drift signals
# ============================================================================


class TestDisplayDrift:
    def test_format_single_drift(self):
        signal = _make_drift(dtype=DriftType.SCOPE, severity=Severity.BLOCK)
        output = format_drift_signal(signal)
        assert "SCOPE" in output
        assert "BLOCK" in output
        assert "Scope drift detected" in output
        assert "Remove TokenStore" in output

    def test_format_multiple_drifts(self):
        s1 = _make_drift(dtype=DriftType.SCOPE)
        s2 = _make_drift(dtype=DriftType.PHASE)
        output = format_drift_signals([s1, s2])
        assert "SCOPE" in output
        assert "PHASE" in output

    def test_format_empty_drifts(self):
        output = format_drift_signals([])
        assert output == ""

    def test_format_includes_step_and_node(self):
        signal = _make_drift()
        output = format_drift_signal(signal)
        assert "implement" in output
        assert "node01" in output
