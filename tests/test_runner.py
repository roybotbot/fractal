"""Tests for runner/runner.py — the core execution engine.

Tested with mock LLM client, detectors, notifier, and state manager
as specified in the build order docs.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, UTC

import pytest

from superpowers_runner.runner.runner import (
    HumanReviewRequired,
    Runner,
    StuckSession,
)
from superpowers_runner.runner.context import ContextBuilder, SchemaRegistry
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
    Resolution,
    Severity,
    UncertaintySignal,
    UncertaintyType,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockLLM:
    """Mock LLM that returns canned responses in order."""

    def __init__(self, responses: list[str] | None = None, default: str = "output"):
        self._responses = list(responses) if responses else []
        self._default = default
        self.calls: list[str] = []

    def call(self, prompt: str, max_tokens: int = 4096, system: str | None = None) -> str:
        self.calls.append(prompt)
        if self._responses:
            return self._responses.pop(0)
        return self._default


class MockDetector:
    """Mock drift detector that returns configured signals."""

    def __init__(self, signals: list[DriftSignal] | None = None):
        self._signals = signals or []
        self.call_count = 0

    def check_all(self, node, step, output, gate_results=None):
        self.call_count += 1
        return list(self._signals)


class MockUncertaintyDetector:
    """Mock uncertainty detector."""

    def __init__(self, signals: list[UncertaintySignal] | None = None):
        self._signals = signals or []

    def check_all(self, node, step, output):
        return list(self._signals)


class MockNotifier:
    """Mock notifier that auto-resolves."""

    def __init__(self, resolution: Resolution = Resolution.PROCEED):
        self._resolution = resolution
        self._buffer: list[UncertaintySignal] = []
        self.interrupted: list[UncertaintySignal] = []
        self.batched: list[UncertaintySignal] = []

    def interrupt(self, signals):
        self.interrupted.extend(signals)
        return [self._resolution] * len(signals)

    def buffer(self, signals):
        self._buffer.extend(signals)

    def should_flush(self):
        return len(self._buffer) >= 3

    def drain(self):
        result = list(self._buffer)
        self._buffer.clear()
        return result

    def notify_batch(self, signals):
        self.batched.extend(signals)
        return [self._resolution] * len(signals)


class MockStateManager:
    """Mock state manager that records saves."""

    def __init__(self):
        self.saves: list[TaskTree] = []

    def save(self, tree: TaskTree) -> None:
        self.saves.append(tree)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tree_with_leaf(
    primitive_type: PrimitiveType = PrimitiveType.TRANSFORMATION,
    name: str = "test_node",
) -> tuple[TaskTree, TaskNode]:
    """Create a tree with a single leaf node."""
    tree = TaskTree(session_id="test-session")
    node = TaskNode(name=name, description="test", primitive_type=primitive_type)
    tree.root = node
    tree.register(node)
    return tree, node


def _make_runner(
    tree: TaskTree,
    llm: MockLLM | None = None,
    detector: MockDetector | None = None,
    uncertainty_detector: MockUncertaintyDetector | None = None,
    notifier: MockNotifier | None = None,
    state_manager: MockStateManager | None = None,
) -> Runner:
    """Create a Runner with sensible mock defaults."""
    llm = llm or MockLLM()
    gate_runner = GateRunner(llm_client=llm)
    context_builder = ContextBuilder(session_id=tree.session_id)
    return Runner(
        tree=tree,
        llm_client=llm,
        gate_runner=gate_runner,
        context_builder=context_builder,
        detector=detector,
        uncertainty_detector=uncertainty_detector,
        notifier=notifier,
        state_manager=state_manager,
    )


def _make_drift_signal(
    severity: Severity = Severity.BLOCK,
    drift_type: DriftType = DriftType.PHASE,
    node_id: str = "test1234",
) -> DriftSignal:
    return DriftSignal(
        id="sig12345",
        drift_type=drift_type,
        severity=severity,
        node_id=node_id,
        step_name="some_step",
        evidence="Test evidence",
        output_excerpt="excerpt",
        correction_template="Fix it",
    )


# ============================================================================
# Basic execution — single leaf node
# ============================================================================


class TestRunnerBasicExecution:
    def test_executes_single_leaf_to_completion(self):
        """A single leaf node should execute all steps and complete."""
        tree, node = _make_tree_with_leaf()

        # LLM returns clean code for each step
        step_count = len(node.steps)
        llm = MockLLM(default="def f(x: int) -> int:\n    return x\n")
        runner = _make_runner(tree, llm=llm)

        # Pre-mark all gates as passed so we don't need real gate checks
        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        assert node.status == NodeStatus.COMPLETE
        assert node.completed_at is not None
        assert tree.is_complete()
        assert len(llm.calls) == step_count

    def test_all_steps_get_output(self):
        tree, node = _make_tree_with_leaf()
        responses = [f"output_{i}" for i in range(len(node.steps))]
        llm = MockLLM(responses=responses)
        runner = _make_runner(tree, llm=llm)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        for i, step in enumerate(node.steps):
            assert step.status == StepStatus.COMPLETE
            assert step.output == f"output_{i}"
            assert step.completed_at is not None

    def test_node_transitions_to_in_progress(self):
        tree, node = _make_tree_with_leaf()
        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        # Before run
        assert node.status == NodeStatus.PENDING

        runner.run()

        # After run: complete (passed through IN_PROGRESS)
        assert node.status == NodeStatus.COMPLETE
        assert node.started_at is not None


# ============================================================================
# State saves
# ============================================================================


class TestRunnerStateSaves:
    def test_state_saved_after_node_completion(self):
        tree, node = _make_tree_with_leaf()
        llm = MockLLM()
        state = MockStateManager()
        runner = _make_runner(tree, llm=llm, state_manager=state)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        assert len(state.saves) >= 1
        assert state.saves[0] is tree

    def test_no_state_manager_no_crash(self):
        """Runner should work without a state manager."""
        tree, node = _make_tree_with_leaf()
        llm = MockLLM()
        runner = _make_runner(tree, llm=llm, state_manager=None)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()  # Should not raise
        assert tree.is_complete()


# ============================================================================
# Resume — skip completed steps
# ============================================================================


class TestRunnerResume:
    def test_skips_completed_steps(self):
        tree, node = _make_tree_with_leaf()

        # Mark first 3 steps as already complete (simulating resume)
        for i in range(3):
            node.steps[i].status = StepStatus.COMPLETE
            node.steps[i].output = f"prior_output_{i}"
            node.steps[i].completed_at = datetime.now(UTC)

        remaining = len(node.steps) - 3
        llm = MockLLM(default="new_output")
        runner = _make_runner(tree, llm=llm)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        # Only remaining steps should have been called
        assert len(llm.calls) == remaining
        # Prior outputs preserved
        assert node.steps[0].output == "prior_output_0"
        assert node.steps[3].output == "new_output"


# ============================================================================
# StuckSession
# ============================================================================


class TestRunnerStuckSession:
    def test_raises_stuck_when_no_executable(self):
        tree = TaskTree(session_id="stuck-test")
        node = TaskNode(
            name="blocked",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        node.status = NodeStatus.BLOCKED  # Not PENDING, not COMPLETE
        tree.root = node
        tree.register(node)

        runner = _make_runner(tree)

        with pytest.raises(StuckSession):
            runner.run()


# ============================================================================
# Drift detection routing
# ============================================================================


class TestRunnerDriftRouting:
    def test_abort_signal_raises_human_review(self):
        tree, node = _make_tree_with_leaf()
        abort_signal = _make_drift_signal(severity=Severity.ABORT)
        detector = MockDetector(signals=[abort_signal])
        runner = _make_runner(tree, detector=detector)

        with pytest.raises(HumanReviewRequired) as exc_info:
            runner.run()

        assert exc_info.value.node is node
        assert node.status == NodeStatus.FAILED

    def test_block_signal_triggers_retry(self):
        tree, node = _make_tree_with_leaf()

        # Detector blocks on first call, clean on second
        call_count = [0]
        block_signal = _make_drift_signal(severity=Severity.BLOCK)

        class OnceBlockDetector:
            def check_all(self, node, step, output, gate_results=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [block_signal]
                return []

        llm = MockLLM(default="clean_output")
        runner = _make_runner(tree, llm=llm, detector=OnceBlockDetector())

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        # Should have made extra LLM calls for the retry
        assert len(llm.calls) > len(node.steps)
        assert node.status == NodeStatus.COMPLETE

    def test_double_block_escalates(self):
        """Two consecutive blocks on same step should escalate to human review."""
        tree, node = _make_tree_with_leaf()
        block_signal = _make_drift_signal(severity=Severity.BLOCK)
        # Detector always blocks
        detector = MockDetector(signals=[block_signal])
        runner = _make_runner(tree, detector=detector)

        with pytest.raises(HumanReviewRequired):
            runner.run()

        assert node.status == NodeStatus.FAILED

    def test_warn_signal_continues(self):
        tree, node = _make_tree_with_leaf()
        warn_signal = _make_drift_signal(severity=Severity.WARN)
        detector = MockDetector(signals=[warn_signal])
        runner = _make_runner(tree, detector=detector)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        # Warn signals don't block — node should complete
        assert node.status == NodeStatus.COMPLETE

    def test_no_detector_skips_drift_checks(self):
        tree, node = _make_tree_with_leaf()
        llm = MockLLM()
        runner = _make_runner(tree, llm=llm, detector=None)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()
        assert node.status == NodeStatus.COMPLETE


# ============================================================================
# Uncertainty routing
# ============================================================================


class TestRunnerUncertaintyRouting:
    def _make_interrupt_signal(self) -> UncertaintySignal:
        return UncertaintySignal(
            id="unc12345",
            uncertainty_type=UncertaintyType.SCHEMA_NEAR_MISS,
            node_id="n1234",
            step_name="implement",
            confidence=0.6,
            evidence="Type mismatch",
            output_excerpt="excerpt",
            question="Is this OK?",
            option_a="yes",
            option_b="no",
            default_resolution=Resolution.RETRY,
        )

    def _make_batch_signal(self) -> UncertaintySignal:
        return UncertaintySignal(
            id="unc67890",
            uncertainty_type=UncertaintyType.AMBIGUOUS_SCOPE,
            node_id="n1234",
            step_name="implement",
            confidence=0.4,
            evidence="New symbol",
            output_excerpt="excerpt",
            question="Is this fine?",
            option_a="yes",
            option_b="no",
            default_resolution=Resolution.PROCEED,
        )

    def test_interrupt_signals_notified_immediately(self):
        tree, node = _make_tree_with_leaf()
        signal = self._make_interrupt_signal()
        u_detector = MockUncertaintyDetector(signals=[signal])
        notifier = MockNotifier(resolution=Resolution.PROCEED)
        runner = _make_runner(
            tree,
            uncertainty_detector=u_detector,
            notifier=notifier,
        )

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        assert len(notifier.interrupted) > 0
        assert node.status == NodeStatus.COMPLETE

    def test_batch_signals_buffered(self):
        tree, node = _make_tree_with_leaf()
        signal = self._make_batch_signal()
        u_detector = MockUncertaintyDetector(signals=[signal])
        notifier = MockNotifier()
        runner = _make_runner(
            tree,
            uncertainty_detector=u_detector,
            notifier=notifier,
        )

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        # Signals should have been buffered (not interrupted)
        assert len(notifier.interrupted) == 0
        assert node.status == NodeStatus.COMPLETE

    def test_no_uncertainty_detector_no_crash(self):
        tree, node = _make_tree_with_leaf()
        runner = _make_runner(tree, uncertainty_detector=None, notifier=None)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()
        assert node.status == NodeStatus.COMPLETE


# ============================================================================
# Gate failures
# ============================================================================


class TestRunnerGateFailures:
    def test_gate_abort_raises_human_review(self):
        tree, node = _make_tree_with_leaf(PrimitiveType.ORCHESTRATION)
        # Add a child so the orchestration isn't empty
        child = TaskNode(
            name="child",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        child.status = NodeStatus.COMPLETE
        node.sub_nodes = [child]

        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        # Orchestration has all_children_typed gate with on_failure="abort".
        # With only one child that has a valid type, that passes.
        # But let's force the abort gate to fail by clearing sub_nodes after steps run.
        # Instead, let's directly test _handle_gate_failures logic.
        # Set up: mark all steps complete
        for step in node.steps:
            step.status = StepStatus.COMPLETE
            step.output = "output"

        # Manually set a gate to failed + abort
        abort_gate = None
        for gr in node.gate_results:
            if gr.gate.on_failure == "abort":
                gr.passed = False
                gr.evidence = "Abort test"
                abort_gate = gr
                break

        if abort_gate:
            # Mark other gates as passed
            for gr in node.gate_results:
                if gr is not abort_gate:
                    gr.passed = True
                    gr.checked_at = datetime.now(UTC)

            with pytest.raises(HumanReviewRequired):
                runner._handle_gate_failures(node)
            assert node.status == NodeStatus.FAILED

    def test_gate_block_retries_then_completes(self):
        """Gate block should re-execute the responsible step."""
        tree, node = _make_tree_with_leaf()
        llm = MockLLM(default="def f(x: int) -> int:\n    return x\n")
        runner = _make_runner(tree, llm=llm)

        # Run all steps first
        for step in node.steps:
            step.status = StepStatus.COMPLETE
            step.output = "def f(x: int) -> int:\n    return x\n"

        # Pre-pass all gates
        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        # Force one gate to fail on first call, then pass on retry
        call_count = [0]

        def patched_run_all(n):
            call_count[0] += 1
            if call_count[0] == 1:
                # Force first gate to fail
                n.gate_results[0].passed = False
                n.gate_results[0].evidence = "Forced failure"
                n.gate_results[0].checked_at = datetime.now(UTC)
            else:
                # All pass on retry
                for gr in n.gate_results:
                    gr.passed = True
                    gr.checked_at = datetime.now(UTC)
            return n.gate_results

        runner.gate_runner.run_all = patched_run_all

        runner._execute_leaf(node)

        # Should have retried and eventually completed
        assert node.status == NodeStatus.COMPLETE

    def test_gate_block_max_retries_escalates(self):
        """Exceeding max retries on gate failure should escalate."""
        tree, node = _make_tree_with_leaf()
        node.max_retries = 0  # No retries allowed

        for step in node.steps:
            step.status = StepStatus.COMPLETE
            step.output = "output"

        # Force a gate failure
        node.gate_results[0].passed = False
        node.gate_results[0].evidence = "Always fails"

        # Mark rest as passed
        for gr in node.gate_results[1:]:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        with pytest.raises(HumanReviewRequired):
            runner._handle_gate_failures(node)


# ============================================================================
# Parent completion propagation
# ============================================================================


class TestRunnerParentCompletion:
    def test_leaf_completion_propagates_to_parent(self):
        tree = TaskTree(session_id="parent-test")
        parent = TaskNode(
            name="flow",
            description="test",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )
        child = TaskNode(
            name="step1",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
            parent_id=parent.id,
        )
        parent.sub_nodes = [child]
        parent.status = NodeStatus.DECOMPOSING
        tree.root = parent
        tree.register(parent)
        tree.register(child)

        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        # Pre-pass all gates on both nodes
        for gr in child.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)
        for gr in parent.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        # Execute just the child
        runner._execute_node(child)

        assert child.status == NodeStatus.COMPLETE
        assert parent.status == NodeStatus.COMPLETE
        assert tree.is_complete()

    def test_parent_waits_for_all_children(self):
        tree = TaskTree(session_id="multi-child")
        parent = TaskNode(
            name="flow",
            description="test",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )
        child1 = TaskNode(
            name="c1",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
            parent_id=parent.id,
        )
        child2 = TaskNode(
            name="c2",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
            parent_id=parent.id,
        )
        parent.sub_nodes = [child1, child2]
        parent.status = NodeStatus.DECOMPOSING
        tree.root = parent
        tree.register(parent)
        tree.register(child1)
        tree.register(child2)

        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        # Pre-pass all gates
        for node in [child1, child2, parent]:
            for gr in node.gate_results:
                gr.passed = True
                gr.checked_at = datetime.now(UTC)

        # Execute child1 only
        runner._execute_node(child1)
        assert child1.status == NodeStatus.COMPLETE
        # Parent should NOT be complete yet
        assert parent.status != NodeStatus.COMPLETE

        # Now execute child2
        runner._execute_node(child2)
        assert child2.status == NodeStatus.COMPLETE
        # NOW parent should complete
        assert parent.status == NodeStatus.COMPLETE

    def test_grandparent_propagation(self):
        tree = TaskTree(session_id="grandparent")
        grandparent = TaskNode(
            name="gp",
            description="test",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )
        parent = TaskNode(
            name="p",
            description="test",
            primitive_type=PrimitiveType.PIPELINE,
            parent_id=grandparent.id,
        )
        child = TaskNode(
            name="c",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
            parent_id=parent.id,
        )
        parent.sub_nodes = [child]
        grandparent.sub_nodes = [parent]
        grandparent.status = NodeStatus.DECOMPOSING
        parent.status = NodeStatus.DECOMPOSING
        tree.root = grandparent
        tree.register(grandparent)
        tree.register(parent)
        tree.register(child)

        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        for node in [child, parent, grandparent]:
            for gr in node.gate_results:
                gr.passed = True
                gr.checked_at = datetime.now(UTC)

        runner._execute_node(child)

        assert child.status == NodeStatus.COMPLETE
        assert parent.status == NodeStatus.COMPLETE
        assert grandparent.status == NodeStatus.COMPLETE
        assert tree.is_complete()


# ============================================================================
# Composition / decomposition
# ============================================================================


class TestRunnerDecomposition:
    def test_composition_node_runs_its_steps(self):
        tree, node = _make_tree_with_leaf(PrimitiveType.ORCHESTRATION)
        step_count = len(node.steps)
        llm = MockLLM(default="planning output")
        runner = _make_runner(tree, llm=llm)

        runner._decompose(node)

        assert node.status == NodeStatus.DECOMPOSING
        assert len(llm.calls) == step_count
        for step in node.steps:
            assert step.status == StepStatus.COMPLETE

    def test_composition_skips_completed_steps(self):
        tree, node = _make_tree_with_leaf(PrimitiveType.ORCHESTRATION)
        # Mark first step as done
        node.steps[0].status = StepStatus.COMPLETE
        node.steps[0].output = "already done"

        remaining = len(node.steps) - 1
        llm = MockLLM(default="new output")
        runner = _make_runner(tree, llm=llm)

        runner._decompose(node)

        assert len(llm.calls) == remaining
        assert node.steps[0].output == "already done"


# ============================================================================
# Full run with dependency ordering
# ============================================================================


class TestRunnerDependencyOrdering:
    def test_dependencies_respected(self):
        tree = TaskTree(session_id="deps-test")
        root = TaskNode(
            name="root",
            description="test",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )
        child_a = TaskNode(
            name="a",
            description="test",
            primitive_type=PrimitiveType.DATA_MODEL,
            parent_id=root.id,
        )
        child_b = TaskNode(
            name="b",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
            parent_id=root.id,
        )
        child_b.dependency_ids = [child_a.id]

        root.sub_nodes = [child_a, child_b]
        root.status = NodeStatus.DECOMPOSING
        tree.root = root
        tree.register(root)
        tree.register(child_a)
        tree.register(child_b)

        # next_executable should return child_a first
        nxt = tree.next_executable()
        assert nxt is child_a

        # After completing child_a, child_b becomes executable
        child_a.status = NodeStatus.COMPLETE
        nxt = tree.next_executable()
        assert nxt is child_b


# ============================================================================
# HumanReviewRequired exception
# ============================================================================


class TestHumanReviewRequired:
    def test_contains_node(self):
        node = TaskNode(
            name="failing",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        exc = HumanReviewRequired(node)
        assert exc.node is node
        assert "failing" in str(exc)

    def test_contains_drift_signals(self):
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.MUTATION)
        signal = _make_drift_signal(severity=Severity.ABORT, drift_type=DriftType.SCOPE)
        exc = HumanReviewRequired(node, [signal])
        assert len(exc.signals) == 1
        assert "scope" in str(exc)

    def test_contains_gate_results(self):
        node = TaskNode(name="n", description="t", primitive_type=PrimitiveType.MUTATION)
        gate_result = GateResult(
            gate=GateTemplate(name="test_gate", check_type="ast_no_any"),
            passed=False,
            evidence="Found Any",
        )
        exc = HumanReviewRequired(node, [gate_result])
        assert "test_gate" in str(exc)
        assert "Found Any" in str(exc)


# ============================================================================
# Context builder integration
# ============================================================================


class TestRunnerContextIntegration:
    def test_context_includes_node_name(self):
        tree, node = _make_tree_with_leaf(name="my_special_node")
        llm = MockLLM()
        runner = _make_runner(tree, llm=llm)

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        # The first LLM call should contain the node name
        assert any("my_special_node" in call for call in llm.calls)

    def test_correction_context_in_retry_prompt(self):
        tree, node = _make_tree_with_leaf()
        block_signal = _make_drift_signal(severity=Severity.BLOCK)

        call_count = [0]

        class OnceBlockDetector:
            def check_all(self, node, step, output, gate_results=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [block_signal]
                return []

        llm = MockLLM(default="output")
        runner = _make_runner(tree, llm=llm, detector=OnceBlockDetector())

        for gr in node.gate_results:
            gr.passed = True
            gr.checked_at = datetime.now(UTC)

        runner.run()

        # The retry call should contain correction context
        assert any("CORRECTION" in call for call in llm.calls)
