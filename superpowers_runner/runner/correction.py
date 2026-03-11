"""CorrectionEngine — drift signal → correction context → retry.

Handles block-level signals by building correction context, re-executing
the step, and escalating to human review on second failure.

Depends on: schema layer, runner/context.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Protocol

from superpowers_runner.schema.nodes import (
    GateResult,
    NodeStatus,
    StepRecord,
    StepStatus,
    TaskNode,
)
from superpowers_runner.schema.signals import DriftSignal, Severity


class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


class DriftDetector(Protocol):
    def check_all(
        self,
        node: TaskNode,
        step: StepRecord,
        output: str,
        gate_results: list[GateResult] | None = None,
    ) -> list[DriftSignal]: ...


class ContextBuilderLike(Protocol):
    def build(self, **kwargs: object) -> str: ...


class CorrectionEngine:
    """Handles block-level drift correction.

    Flow:
    1. Receive block signals from a step execution
    2. Build correction context from signal evidence + templates
    3. Re-execute the step with correction prepended
    4. Re-check the retry output
    5. If still blocking → escalate (return signals for human review)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        context_builder: ContextBuilderLike,
        detector: DriftDetector | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._context_builder = context_builder
        self._detector = detector

    def correct_step(
        self,
        node: TaskNode,
        step: StepRecord,
        signals: list[DriftSignal],
        global_schema: object | None = None,
    ) -> tuple[str, list[DriftSignal]]:
        """Attempt to correct a step blocked by drift signals.

        Returns (corrected_output, remaining_signals).
        remaining_signals is empty on success, non-empty on persistent failure.
        """
        if step.retry_count >= step.max_retries:
            return "", signals

        step.retry_count += 1
        step.status = StepStatus.RETRYING

        # Build correction from blocking signals
        correction = self.build_correction_context(signals)
        step.correction_context = correction

        # Re-execute with correction prepended
        prompt = self._context_builder.build(
            node=node,
            step=step,
            global_schema=global_schema,
            correction_context=correction,
        )
        retry_output = self._llm_client.call(prompt)

        # Re-check retry output
        if self._detector:
            retry_signals = self._detector.check_all(node, step, retry_output)
            remaining = [s for s in retry_signals if s.severity == Severity.BLOCK]
            if remaining:
                return retry_output, remaining

        return retry_output, []

    def correct_gate_failures(
        self,
        node: TaskNode,
        failing_gates: list[GateResult],
    ) -> str:
        """Build correction context from failing gate results.

        Does not re-execute — that's the runner's job.
        """
        return self.build_gate_correction(failing_gates)

    def build_correction_context(self, signals: list[DriftSignal]) -> str:
        """Build correction context string from drift signals."""
        parts = ["DRIFT CORRECTION REQUIRED:"]
        parts.append("")

        for i, signal in enumerate(signals, 1):
            if signal.severity != Severity.BLOCK:
                continue
            parts.append(f"[{i}] {signal.drift_type.value.upper()} DRIFT")
            parts.append(f"    Evidence: {signal.evidence}")
            if signal.output_excerpt:
                parts.append(f"    Problematic output: {signal.output_excerpt[:200]}")
            parts.append(f"    Required: {signal.correction_template}")
            parts.append("")

        parts.append("Address each issue above. Do not repeat the problematic patterns.")
        return "\n".join(parts)

    def build_gate_correction(self, failing: list[GateResult]) -> str:
        """Build correction context from failing gate results."""
        parts = ["GATE FAILURES — CORRECTION REQUIRED:"]
        parts.append("")
        for g in failing:
            parts.append(f"  - {g.gate.name} ({g.gate.check_type}): {g.evidence}")
        parts.append("")
        parts.append("Address each failing gate before proceeding.")
        return "\n".join(parts)

    def find_responsible_step(
        self, node: TaskNode, gate_result: GateResult
    ) -> StepRecord:
        """Find the step most likely responsible for a gate failure.

        Heuristic mapping:
        - Test-related gates → last test step
        - AST/structural gates → last implementation step
        - Documentation gates → last doc/define step
        - Fallback → last completed step
        """
        check_type = gate_result.gate.check_type

        # Test-related gates
        if "test" in check_type:
            for step in reversed(node.steps):
                if step.status == StepStatus.COMPLETE and "test" in step.template.name:
                    return step

        # AST/structural gates
        if check_type.startswith("ast_") or check_type in (
            "has_docstring", "has_documented_exceptions", "has_rollback_documentation",
        ):
            for step in reversed(node.steps):
                if step.status == StepStatus.COMPLETE and any(
                    kw in step.template.name
                    for kw in ("implement", "model", "document", "define_rollback")
                ):
                    return step

        # LLM judge gates (check by gate name patterns)
        if check_type == "llm_judge":
            gate_name = gate_result.gate.name.lower()
            if "test" in gate_name:
                for step in reversed(node.steps):
                    if step.status == StepStatus.COMPLETE and "test" in step.template.name:
                        return step
            elif any(kw in gate_name for kw in ("logic", "idempotency", "rollback")):
                for step in reversed(node.steps):
                    if step.status == StepStatus.COMPLETE and (
                        "implement" in step.template.name
                        or "define" in step.template.name
                    ):
                        return step

        # Children-typed gate → enumeration step
        if check_type == "children_have_types":
            for step in reversed(node.steps):
                if step.status == StepStatus.COMPLETE and (
                    "enumerate" in step.template.name
                    or "children" in step.template.name
                ):
                    return step

        # Fallback: last completed step
        for step in reversed(node.steps):
            if step.status == StepStatus.COMPLETE:
                return step

        # Last resort: first step
        return node.steps[0]
