"""Display — terminal rendering for uncertainty signals and drift signals.

Formats signals into the structured display format from the spec.
Pure formatting — no I/O or user interaction.

Depends on: schema layer only.
"""

from __future__ import annotations

from superpowers_runner.schema.signals import (
    DriftSignal,
    UncertaintySignal,
)


_SEPARATOR = "━" * 56


def format_uncertainty_batch(
    signals: list[UncertaintySignal],
    session_id: str = "",
    node_name: str = "",
    node_type: str = "",
    timeout_seconds: int = 300,
) -> str:
    """Format a batch of uncertainty signals for terminal display.

    Follows the spec format from uncertainty-hitl.md.
    """
    lines: list[str] = []
    lines.append(_SEPARATOR)
    lines.append(f"HUMAN REVIEW NEEDED  [{len(signals)} signal{'s' if len(signals) != 1 else ''}]")
    if session_id:
        lines.append(f"Session: {session_id}")
    if node_name:
        type_suffix = f" ({node_type})" if node_type else ""
        lines.append(f"Node: {node_name}{type_suffix}")
    lines.append(_SEPARATOR)

    for i, signal in enumerate(signals, 1):
        lines.append("")
        label = signal.uncertainty_type.value.upper().replace("_", " ")
        lines.append(f"[{i}/{len(signals)}] {label}  (confidence: {signal.confidence:.2f})")
        if signal.step_name:
            lines.append(f"Step: {signal.step_name}")
        lines.append("")
        lines.append(signal.evidence)
        lines.append("")
        lines.append(f"> {signal.question}")
        lines.append("  A / B / show-more: _")
        lines.append(_SEPARATOR)

    # Timeout line
    timeout_display = _format_timeout(timeout_seconds)
    default_actions = []
    for i, signal in enumerate(signals, 1):
        action = signal.default_resolution.value
        default_actions.append(f"{action} on [{i}]")
    lines.append(f"Timeout in {timeout_display}  |  No response: {', '.join(default_actions)}")

    return "\n".join(lines)


def format_drift_signal(signal: DriftSignal) -> str:
    """Format a single drift signal for terminal display."""
    lines: list[str] = []
    label = signal.drift_type.value.upper().replace("_", " ")
    severity = signal.severity.value.upper()
    lines.append(f"⚠ DRIFT DETECTED: {label} [{severity}]")
    if signal.step_name:
        lines.append(f"  Step: {signal.step_name}")
    if signal.node_id:
        lines.append(f"  Node: {signal.node_id}")
    lines.append(f"  {signal.evidence}")
    if signal.correction_template:
        lines.append(f"  Correction: {signal.correction_template}")
    return "\n".join(lines)


def format_drift_signals(signals: list[DriftSignal]) -> str:
    """Format multiple drift signals."""
    if not signals:
        return ""
    blocks = [format_drift_signal(s) for s in signals]
    return "\n\n".join(blocks)


def _format_timeout(seconds: int) -> str:
    """Format seconds as M:SS."""
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"
