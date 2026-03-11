"""Runner — main execution engine.

Depth-first tree traversal, node execution dispatch, signal routing,
parent completion propagation. All state transitions happen here.

Depends on: schema, detector/checks (via gates_runner), runner/context,
             runner/gates_runner, runner/correction, session/logger.
"""

from __future__ import annotations

import time
from datetime import datetime, UTC
from typing import Protocol

from superpowers_runner.schema.nodes import (
    GateResult,
    NodeStatus,
    StepRecord,
    StepStatus,
    TaskNode,
    TaskTree,
)
from superpowers_runner.schema.signals import (
    BATCH_AND_NOTIFY,
    DriftSignal,
    INTERRUPT_IMMEDIATELY,
    Resolution,
    Severity,
    UncertaintySignal,
)
from superpowers_runner.runner.context import ContextBuilder, SchemaRegistry
from superpowers_runner.runner.correction import CorrectionEngine
from superpowers_runner.runner.gates_runner import GateRunner


# ---------------------------------------------------------------------------
# Protocol interfaces for injectable dependencies
# ---------------------------------------------------------------------------


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


class UncertaintyDetector(Protocol):
    def check_all(
        self,
        node: TaskNode,
        step: StepRecord,
        output: str,
    ) -> list[UncertaintySignal]: ...


class Notifier(Protocol):
    def interrupt(self, signals: list[UncertaintySignal]) -> list[Resolution]: ...
    def buffer(self, signals: list[UncertaintySignal]) -> None: ...
    def should_flush(self) -> bool: ...
    def drain(self) -> list[UncertaintySignal]: ...
    def notify_batch(self, signals: list[UncertaintySignal]) -> list[Resolution]: ...


class StateManager(Protocol):
    def save(self, tree: TaskTree) -> None: ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HumanReviewRequired(Exception):
    """Raised when execution must pause for human review."""

    def __init__(
        self,
        node: TaskNode,
        signals: list[DriftSignal] | list[GateResult] | None = None,
    ) -> None:
        self.node = node
        self.signals = signals or []
        details = []
        for s in self.signals:
            if isinstance(s, DriftSignal):
                details.append(f"{s.drift_type.value}: {s.evidence}")
            elif isinstance(s, GateResult):
                details.append(f"gate '{s.gate.name}': {s.evidence}")
        msg = f"Human review required for node '{node.name}'"
        if details:
            msg += " — " + "; ".join(details)
        super().__init__(msg)


class StuckSession(Exception):
    """Raised when no nodes are executable but the tree isn't complete."""
    pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class Runner:
    """Main execution engine. Drives the full tree to completion.

    Constructor accepts optional gate_runner, context_builder, and
    correction_engine. If not provided, defaults are created from
    the llm_client.
    """

    def __init__(
        self,
        tree: TaskTree,
        llm_client: LLMClient,
        gate_runner: GateRunner | None = None,
        context_builder: ContextBuilder | None = None,
        correction_engine: CorrectionEngine | None = None,
        detector: DriftDetector | None = None,
        uncertainty_detector: UncertaintyDetector | None = None,
        notifier: Notifier | None = None,
        state_manager: StateManager | None = None,
        schema_registry: SchemaRegistry | None = None,
        logger: object | None = None,
        # Legacy aliases — mapped to detector/uncertainty_detector
        drift_detector: DriftDetector | None = None,
    ) -> None:
        self.tree = tree
        self.llm_client = llm_client

        # Build defaults for optional components
        self.gate_runner = gate_runner or GateRunner(llm_client=llm_client)
        self.context_builder = context_builder or ContextBuilder(
            session_id=tree.session_id,
        )
        self.schema_registry = schema_registry or SchemaRegistry()

        # Detector: accept either param name
        self.detector = detector or drift_detector
        self.uncertainty_detector = uncertainty_detector
        self.notifier = notifier
        self.state_manager = state_manager
        self.logger = logger

        # CorrectionEngine: build from available components
        self.correction_engine = correction_engine or CorrectionEngine(
            llm_client=llm_client,
            context_builder=self.context_builder,
            detector=self.detector,
        )

    def run(self) -> TaskTree:
        """Execute the full tree to completion.

        Returns the completed tree.
        Raises HumanReviewRequired if an abort-level signal fires.
        Raises StuckSession if no nodes are executable but tree isn't complete.
        """
        session_start = time.monotonic()
        if self.logger:
            self.logger.session_started(self.tree.session_id, "")

        try:
            while not self.tree.is_complete():
                node = self.tree.next_executable()
                if node is None:
                    raise StuckSession("No executable nodes, tree not complete")
                self._execute_node(node)
                if self.state_manager:
                    self.state_manager.save(self.tree)
        except HumanReviewRequired:
            if self.logger:
                signal_ids = []
                self.logger.session_failed(
                    self.tree.session_id, "human review required", signal_ids
                )
            raise
        except StuckSession:
            if self.logger:
                self.logger.session_failed(
                    self.tree.session_id, "stuck session", []
                )
            raise

        if self.logger:
            duration_ms = int((time.monotonic() - session_start) * 1000)
            self.logger.session_complete(self.tree.session_id, duration_ms)

        return self.tree

    # -------------------------------------------------------------------
    # Node execution
    # -------------------------------------------------------------------

    def _execute_node(self, node: TaskNode) -> None:
        node.status = NodeStatus.IN_PROGRESS
        node.started_at = datetime.now(UTC)

        if self.logger:
            self.logger.register_node(self.tree, node)
            self.logger.node_started(node)

        if node.is_composition:
            self._decompose(node)
        else:
            self._execute_leaf(node)

    def _decompose(self, node: TaskNode) -> None:
        """Run composition node steps then register children for execution."""
        node.status = NodeStatus.DECOMPOSING

        for step in node.steps:
            if step.status == StepStatus.COMPLETE:
                continue
            self._execute_step(node, step)

        # Children should now be populated on the node
        for child in node.sub_nodes:
            self.tree.register(child)
            child.parent_id = node.id

    def _execute_leaf(self, node: TaskNode) -> None:
        """Execute all steps in order, then run gates."""
        for step in node.steps:
            if step.status == StepStatus.COMPLETE:
                continue  # resume: skip already-completed steps
            self._execute_step(node, step)

        # All steps done — run gates
        self.gate_runner.run_all(node)

        # Log gate results
        if self.logger:
            for gr in node.gate_results:
                if gr.passed:
                    self.logger.gate_passed(node, gr.gate, gr.evidence)
                else:
                    self.logger.gate_failed(node, gr.gate, gr.evidence)

        if node.all_gates_passed:
            node.status = NodeStatus.COMPLETE
            node.completed_at = datetime.now(UTC)
            if self.logger:
                duration_ms = 0
                if node.started_at:
                    duration_ms = int((node.completed_at - node.started_at).total_seconds() * 1000)
                self.logger.node_complete(node, duration_ms)
            self._check_parent_completion(node)
        else:
            self._handle_gate_failures(node)

    # -------------------------------------------------------------------
    # Step execution
    # -------------------------------------------------------------------

    def _execute_step(self, node: TaskNode, step: StepRecord) -> None:
        step.status = StepStatus.ACTIVE
        step_start = time.monotonic()
        attempt = step.retry_count + 1

        if self.logger:
            self.logger.step_started(node, step)

        # Build prompt
        prompt = self.context_builder.build(
            node=node,
            step=step,
            global_schema=self.schema_registry,
            correction_context=step.correction_context,
        )

        if self.logger:
            self.logger.log_prompt(node, step, attempt, prompt)
            self.logger.llm_call_started(node, step, "step_execution")

        llm_start = time.monotonic()
        output = self.llm_client.call(prompt)
        llm_ms = int((time.monotonic() - llm_start) * 1000)

        if self.logger:
            self.logger.llm_call_complete(
                node, step, "step_execution", duration_ms=llm_ms,
            )

        # Drift detection
        signals_for_content: list = []
        if self.detector:
            drift_signals = self.detector.check_all(node, step, output)
            if drift_signals and self.logger:
                for sig in drift_signals:
                    self.logger.drift_detected(node, step, sig)
                signals_for_content.extend(drift_signals)
            output = self._route_drift_signals(node, step, drift_signals, output)

        # Uncertainty detection
        if self.uncertainty_detector and self.notifier:
            uncertain_signals = self.uncertainty_detector.check_all(node, step, output)
            if uncertain_signals and self.logger:
                for sig in uncertain_signals:
                    self.logger.uncertainty_detected(node, step, sig)
                signals_for_content.extend(uncertain_signals)
            output = self._route_uncertainty_signals(
                node, step, uncertain_signals, output
            )

        # Mark step complete
        step.output = output
        step.status = StepStatus.COMPLETE
        step.completed_at = datetime.now(UTC)
        step_ms = int((time.monotonic() - step_start) * 1000)

        if self.logger:
            outcome = "complete"
            if signals_for_content:
                outcome = "signals_detected"
            self.logger.log_response(
                node, step, attempt, output,
                signals=signals_for_content or None,
                outcome=outcome, duration_ms=step_ms,
            )
            self.logger.step_complete(node, step, duration_ms=step_ms)

    # -------------------------------------------------------------------
    # Signal routing
    # -------------------------------------------------------------------

    def _route_drift_signals(
        self,
        node: TaskNode,
        step: StepRecord,
        signals: list[DriftSignal],
        output: str,
    ) -> str:
        if not signals:
            return output

        # Abort-level signals
        if any(s.severity == Severity.ABORT for s in signals):
            node.status = NodeStatus.FAILED
            raise HumanReviewRequired(node, signals)

        # Block-level signals — delegate to CorrectionEngine
        block_signals = [s for s in signals if s.severity == Severity.BLOCK]
        if block_signals:
            output = self._handle_block(node, step, block_signals)

        return output

    def _route_uncertainty_signals(
        self,
        node: TaskNode,
        step: StepRecord,
        signals: list[UncertaintySignal],
        output: str,
    ) -> str:
        if not signals or not self.notifier:
            return output

        immediate = [
            s for s in signals
            if s.uncertainty_type in INTERRUPT_IMMEDIATELY
        ]
        bufferable = [
            s for s in signals
            if s.uncertainty_type in BATCH_AND_NOTIFY
        ]

        if immediate:
            resolutions = self.notifier.interrupt(immediate)
            output = self._apply_resolutions(
                node, step, output, resolutions, immediate
            )

        if bufferable:
            self.notifier.buffer(bufferable)
            if self.notifier.should_flush():
                buffered = self.notifier.drain()
                self.notifier.notify_batch(buffered)

        return output

    # -------------------------------------------------------------------
    # Block handling — delegates to CorrectionEngine
    # -------------------------------------------------------------------

    def _handle_block(
        self,
        node: TaskNode,
        step: StepRecord,
        signals: list[DriftSignal],
    ) -> str:
        """Delegate block-level correction to CorrectionEngine."""
        if self.logger:
            self.logger.node_blocked(node, signals)
            self.logger.step_retrying(node, step)

        corrected_output, remaining = self.correction_engine.correct_step(
            node=node,
            step=step,
            signals=signals,
            global_schema=self.schema_registry,
        )

        if remaining:
            # Correction failed — escalate
            node.status = NodeStatus.FAILED
            if self.logger:
                self.logger.node_failed(
                    node, "correction failed", [s.id for s in remaining]
                )
            raise HumanReviewRequired(node, remaining)

        node.status = NodeStatus.IN_PROGRESS
        return corrected_output

    # -------------------------------------------------------------------
    # Gate failure handling — delegates to CorrectionEngine
    # -------------------------------------------------------------------

    def _handle_gate_failures(self, node: TaskNode) -> None:
        """Handle failing gates: abort-level immediately, block-level with retry."""
        failing = [g for g in node.gate_results if not g.passed]

        # Abort-level gate failures
        abort_gates = [g for g in failing if g.gate.on_failure == "abort"]
        if abort_gates:
            node.status = NodeStatus.FAILED
            if self.logger:
                self.logger.node_failed(
                    node, "abort gate failure", []
                )
            raise HumanReviewRequired(node, abort_gates)

        # Block-level gate failures — retry the node
        node.status = NodeStatus.BLOCKED
        node.retry_count += 1

        if node.retry_count > node.max_retries:
            node.status = NodeStatus.FAILED
            if self.logger:
                self.logger.node_failed(
                    node, "max gate retries exceeded", []
                )
            raise HumanReviewRequired(node, failing)

        # Use CorrectionEngine for correction context
        correction = self.correction_engine.build_gate_correction(failing)

        # Use CorrectionEngine for step mapping
        target_step = self.correction_engine.find_responsible_step(node, failing[0])
        target_step.correction_context = correction
        target_step.status = StepStatus.PENDING
        target_step.retry_count += 1

        # Re-execute the leaf from the target step onward
        self._execute_leaf(node)

    # -------------------------------------------------------------------
    # Parent completion
    # -------------------------------------------------------------------

    def _check_parent_completion(self, node: TaskNode) -> None:
        """Check if parent can complete now that this child is done."""
        if node.parent_id is None:
            return

        parent = self.tree.get(node.parent_id)
        if parent is None:
            return

        if not parent.all_children_complete:
            return

        # All children done — run parent's gates
        self.gate_runner.run_all(parent)

        if parent.all_gates_passed:
            parent.status = NodeStatus.COMPLETE
            parent.completed_at = datetime.now(UTC)
            if self.logger:
                duration_ms = 0
                if parent.started_at:
                    duration_ms = int((parent.completed_at - parent.started_at).total_seconds() * 1000)
                self.logger.node_complete(parent, duration_ms)
            self._check_parent_completion(parent)  # propagate up

    # -------------------------------------------------------------------
    # Uncertainty resolution
    # -------------------------------------------------------------------

    def _apply_resolutions(
        self,
        node: TaskNode,
        step: StepRecord,
        output: str,
        resolutions: list[Resolution],
        signals: list[UncertaintySignal],
    ) -> str:
        """Apply human resolutions to uncertainty signals."""
        for resolution, signal in zip(resolutions, signals):
            if resolution == Resolution.RETRY:
                step.correction_context = (
                    f"A reviewer flagged this: {signal.evidence}\n"
                    f"{signal.question}\n"
                    f"Please address this in your response."
                )
                prompt = self.context_builder.build(
                    node=node,
                    step=step,
                    global_schema=self.schema_registry,
                    correction_context=step.correction_context,
                )
                output = self.llm_client.call(prompt)

            elif resolution == Resolution.ESCALATE:
                node.status = NodeStatus.BLOCKED

        return output
