"""Runner — main execution engine.

Depth-first tree traversal, node execution dispatch, signal routing,
parent completion propagation. All state transitions happen here.

Depends on: schema, detector/checks (via gates_runner), runner/context,
             runner/gates_runner. Uses Protocol interfaces for detector,
             notifier, and state_manager so they can be mocked or swapped.
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
from superpowers_runner.runner.gates_runner import GateRunner


# ---------------------------------------------------------------------------
# Protocol interfaces for dependencies not yet built
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
    """Main execution engine. Drives the full tree to completion."""

    def __init__(
        self,
        tree: TaskTree,
        llm_client: LLMClient,
        gate_runner: GateRunner,
        context_builder: ContextBuilder,
        detector: DriftDetector | None = None,
        uncertainty_detector: UncertaintyDetector | None = None,
        notifier: Notifier | None = None,
        state_manager: StateManager | None = None,
        schema_registry: SchemaRegistry | None = None,
    ) -> None:
        self.tree = tree
        self.llm_client = llm_client
        self.gate_runner = gate_runner
        self.context_builder = context_builder
        self.detector = detector
        self.uncertainty_detector = uncertainty_detector
        self.notifier = notifier
        self.state_manager = state_manager
        self.schema_registry = schema_registry or SchemaRegistry()

    def run(self) -> TaskTree:
        """Execute the full tree to completion.

        Returns the completed tree.
        Raises HumanReviewRequired if an abort-level signal fires.
        Raises StuckSession if no nodes are executable but tree isn't complete.
        """
        while not self.tree.is_complete():
            node = self.tree.next_executable()
            if node is None:
                raise StuckSession("No executable nodes, tree not complete")
            self._execute_node(node)
            if self.state_manager:
                self.state_manager.save(self.tree)
        return self.tree

    # -------------------------------------------------------------------
    # Node execution
    # -------------------------------------------------------------------

    def _execute_node(self, node: TaskNode) -> None:
        node.status = NodeStatus.IN_PROGRESS
        node.started_at = datetime.now(UTC)

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
        # (by the planner/decomposer during the enumerate_children step)
        for child in node.sub_nodes:
            self.tree.register(child)
            child.parent_id = node.id

        # Node stays in DECOMPOSING — it completes when all children complete.
        # Children will be picked up by next_executable() in the main loop.

    def _execute_leaf(self, node: TaskNode) -> None:
        """Execute all steps in order, then run gates."""
        for step in node.steps:
            if step.status == StepStatus.COMPLETE:
                continue  # resume: skip already-completed steps
            self._execute_step(node, step)

        # All steps done — run gates
        self.gate_runner.run_all(node)

        if node.all_gates_passed:
            node.status = NodeStatus.COMPLETE
            node.completed_at = datetime.now(UTC)
            self._check_parent_completion(node)
        else:
            self._handle_gate_failures(node)

    # -------------------------------------------------------------------
    # Step execution
    # -------------------------------------------------------------------

    def _execute_step(self, node: TaskNode, step: StepRecord) -> None:
        step.status = StepStatus.ACTIVE

        # Build prompt
        prompt = self.context_builder.build(
            node=node,
            step=step,
            global_schema=self.schema_registry,
            correction_context=step.correction_context,
        )

        output = self.llm_client.call(prompt)

        # Drift detection (if detector available)
        if self.detector:
            drift_signals = self.detector.check_all(node, step, output)
            output = self._route_drift_signals(node, step, drift_signals, output)

        # Uncertainty detection (if detector available)
        if self.uncertainty_detector and self.notifier:
            uncertain_signals = self.uncertainty_detector.check_all(node, step, output)
            output = self._route_uncertainty_signals(
                node, step, uncertain_signals, output
            )

        # Mark step complete
        step.output = output
        step.status = StepStatus.COMPLETE
        step.completed_at = datetime.now(UTC)

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

        # Block-level signals
        if any(s.severity == Severity.BLOCK for s in signals):
            output = self._handle_block(node, step, signals)

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
    # Block handling (drift correction)
    # -------------------------------------------------------------------

    def _handle_block(
        self,
        node: TaskNode,
        step: StepRecord,
        signals: list[DriftSignal],
    ) -> str:
        """One retry with correction context. Escalates on second failure."""
        if step.retry_count >= step.max_retries:
            node.status = NodeStatus.FAILED
            raise HumanReviewRequired(node, signals)

        step.retry_count += 1
        step.status = StepStatus.RETRYING
        node.status = NodeStatus.BLOCKED

        # Build correction from blocking signals
        correction = "\n".join(
            s.correction_context()
            for s in signals
            if s.severity == Severity.BLOCK
        )
        step.correction_context = correction

        # Re-execute with correction prepended
        prompt = self.context_builder.build(
            node=node,
            step=step,
            global_schema=self.schema_registry,
            correction_context=correction,
        )
        retry_output = self.llm_client.call(prompt)

        # Re-check retry output
        if self.detector:
            retry_signals = self.detector.check_all(node, step, retry_output)
            if any(s.severity == Severity.BLOCK for s in retry_signals):
                node.status = NodeStatus.FAILED
                raise HumanReviewRequired(node, retry_signals)

        node.status = NodeStatus.IN_PROGRESS
        return retry_output

    # -------------------------------------------------------------------
    # Gate failure handling
    # -------------------------------------------------------------------

    def _handle_gate_failures(self, node: TaskNode) -> None:
        """Handle failing gates: abort-level immediately, block-level with retry."""
        failing = [g for g in node.gate_results if not g.passed]

        # Abort-level gate failures
        abort_gates = [g for g in failing if g.gate.on_failure == "abort"]
        if abort_gates:
            node.status = NodeStatus.FAILED
            raise HumanReviewRequired(node, abort_gates)

        # Block-level gate failures — retry the node
        node.status = NodeStatus.BLOCKED
        node.retry_count += 1

        if node.retry_count > node.max_retries:
            node.status = NodeStatus.FAILED
            raise HumanReviewRequired(node, failing)

        correction = self._build_gate_correction(failing)

        # Find the step responsible and re-execute from there
        target_step = self._find_step_responsible_for(node, failing[0])
        target_step.correction_context = correction
        target_step.status = StepStatus.PENDING
        target_step.retry_count += 1

        # Re-execute the leaf from the target step onward
        self._execute_leaf(node)

    def _build_gate_correction(self, failing: list[GateResult]) -> str:
        """Build correction context from failing gate results."""
        parts = ["GATE FAILURES:"]
        for g in failing:
            parts.append(f"  - {g.gate.name}: {g.evidence}")
        parts.append("CORRECTION REQUIRED: Address each failing gate.")
        return "\n".join(parts)

    def _find_step_responsible_for(
        self, node: TaskNode, gate_result: GateResult
    ) -> StepRecord:
        """Find the last step that produced the artifact the gate checks.

        Heuristic: the last completed step is the most likely source.
        For implementation gates, find the step with 'implement' in the name.
        For test gates, find the step with 'test' in the name.
        """
        check_type = gate_result.gate.check_type

        # Test-related gates → find test step
        if "test" in check_type:
            for step in reversed(node.steps):
                if step.status == StepStatus.COMPLETE and "test" in step.template.name:
                    return step

        # AST/structural gates → find implementation step
        if check_type.startswith("ast_") or check_type in (
            "has_docstring", "has_documented_exceptions", "has_rollback_documentation",
        ):
            for step in reversed(node.steps):
                if step.status == StepStatus.COMPLETE and (
                    "implement" in step.template.name
                    or "model" in step.template.name
                    or "document" in step.template.name
                    or "define_rollback" in step.template.name
                ):
                    return step

        # Fallback: last completed step
        for step in reversed(node.steps):
            if step.status == StepStatus.COMPLETE:
                return step

        # Last resort: first step
        return node.steps[0]

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
                # Re-run the step with the signal evidence as context
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
                # Treat as block-level drift
                node.status = NodeStatus.BLOCKED
                # Handled by caller

        return output
