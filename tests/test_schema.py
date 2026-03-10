"""Tests for the schema layer.

Verifies that the schema layer is self-consistent: all types have
categories, step templates exist, gate templates exist, nodes initialize
correctly, and tree operations work.
"""

from __future__ import annotations

import pytest

from superpowers_runner.schema.primitives import (
    CATEGORY_MAP,
    COMPOSITION_TYPES,
    GateTemplate,
    NodeCategory,
    PrimitiveType,
    STEP_TEMPLATES,
    StepTemplate,
    get_steps,
)
from superpowers_runner.schema.gates import GATE_TEMPLATES, get_gates
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
from superpowers_runner.schema.signals import (
    BATCH_AND_NOTIFY,
    DriftSignal,
    DriftType,
    INTERRUPT_IMMEDIATELY,
    Resolution,
    Severity,
    UncertaintySignal,
    UncertaintyType,
)


class TestPrimitiveTypes:
    def test_all_types_exist(self):
        # 21 types enumerated in primitives.md across 6 categories
        assert len(PrimitiveType) == 21

    def test_all_types_have_category(self):
        for ptype in PrimitiveType:
            assert ptype in CATEGORY_MAP, f"{ptype} missing from CATEGORY_MAP"

    def test_composition_types(self):
        assert PrimitiveType.PIPELINE in COMPOSITION_TYPES
        assert PrimitiveType.ROUTER in COMPOSITION_TYPES
        assert PrimitiveType.ORCHESTRATION in COMPOSITION_TYPES
        assert len(COMPOSITION_TYPES) == 3

    def test_all_types_have_steps(self):
        for ptype in PrimitiveType:
            steps = get_steps(ptype)
            assert len(steps) > 0, f"{ptype} has no steps"

    def test_step_templates_are_frozen(self):
        steps = get_steps(PrimitiveType.TRANSFORMATION)
        with pytest.raises(AttributeError):
            steps[0].name = "changed"


class TestGateTemplates:
    def test_all_typed_gates_exist(self):
        for ptype in GATE_TEMPLATES:
            gates = get_gates(ptype)
            assert len(gates) > 0

    def test_generic_fallback(self):
        # Types not in GATE_TEMPLATES get the generic
        gates = get_gates(PrimitiveType.CONFIG)
        assert len(gates) >= 2
        check_types = [g.check_type for g in gates]
        assert "run_tests" in check_types
        assert "ast_no_any" in check_types

    def test_orchestration_has_abort_gate(self):
        gates = get_gates(PrimitiveType.ORCHESTRATION)
        abort_gates = [g for g in gates if g.on_failure == "abort"]
        assert len(abort_gates) >= 1
        assert any(g.check_type == "children_have_types" for g in abort_gates)


class TestNodeSchema:
    def test_has_any_types_detects_any(self):
        schema = NodeSchema(fields=[
            SchemaField(name="data", type_annotation="Any"),
        ])
        assert schema.has_any_types()

    def test_has_any_types_clean(self):
        schema = NodeSchema(fields=[
            SchemaField(name="name", type_annotation="str"),
            SchemaField(name="age", type_annotation="int"),
        ])
        assert not schema.has_any_types()

    def test_has_any_types_bare_dict(self):
        schema = NodeSchema(fields=[
            SchemaField(name="config", type_annotation="dict"),
        ])
        assert schema.has_any_types()


class TestTaskNode:
    def test_auto_generates_id(self):
        node = TaskNode(
            name="test_node",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        assert len(node.id) == 8

    def test_steps_derived_from_type(self):
        node = TaskNode(
            name="transform",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        step_names = [s.template.name for s in node.steps]
        assert "define_input_schema" in step_names
        assert "implement_minimal" in step_names

    def test_gates_derived_from_type(self):
        node = TaskNode(
            name="transform",
            description="test",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        gate_names = [g.gate.name for g in node.gate_results]
        assert "no_any_types" in gate_names
        assert "no_io_calls" in gate_names

    def test_is_composition(self):
        comp = TaskNode(name="flow", description="test", primitive_type=PrimitiveType.ORCHESTRATION)
        leaf = TaskNode(name="func", description="test", primitive_type=PrimitiveType.TRANSFORMATION)
        assert comp.is_composition
        assert not leaf.is_composition

    def test_context_summary(self):
        node = TaskNode(
            name="generate_token",
            description="Generates a reset token",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        summary = node.to_context_summary()
        assert "generate_token" in summary
        assert "transformation" in summary

    def test_all_gates_passed(self):
        node = TaskNode(name="t", description="t", primitive_type=PrimitiveType.TRANSFORMATION)
        assert not node.all_gates_passed
        for gr in node.gate_results:
            gr.passed = True
        assert node.all_gates_passed


class TestTaskTree:
    def test_register_and_get(self):
        tree = TaskTree(session_id="test-001")
        node = TaskNode(name="root", description="root", primitive_type=PrimitiveType.ORCHESTRATION)
        tree.root = node
        tree.register(node)
        assert tree.get(node.id) is node

    def test_all_nodes_traversal(self):
        tree = TaskTree(session_id="test-002")
        root = TaskNode(name="root", description="r", primitive_type=PrimitiveType.ORCHESTRATION)
        child1 = TaskNode(name="c1", description="c", primitive_type=PrimitiveType.TRANSFORMATION)
        child2 = TaskNode(name="c2", description="c", primitive_type=PrimitiveType.MUTATION)
        root.sub_nodes = [child1, child2]
        tree.root = root
        tree.register(root)
        tree.register(child1)
        tree.register(child2)

        all_nodes = tree.all_nodes()
        assert len(all_nodes) == 3

    def test_next_executable_respects_deps(self):
        tree = TaskTree(session_id="test-003")
        root = TaskNode(name="root", description="r", primitive_type=PrimitiveType.ORCHESTRATION)
        child1 = TaskNode(name="c1", description="c", primitive_type=PrimitiveType.DATA_MODEL)
        child2 = TaskNode(name="c2", description="c", primitive_type=PrimitiveType.TRANSFORMATION)
        child2.dependency_ids = [child1.id]
        root.sub_nodes = [child1, child2]
        root.status = NodeStatus.DECOMPOSING
        tree.root = root
        tree.register(root)
        tree.register(child1)
        tree.register(child2)

        # child1 has no deps, should be first
        nxt = tree.next_executable()
        assert nxt is child1

        # After child1 completes, child2 becomes executable
        child1.status = NodeStatus.COMPLETE
        nxt = tree.next_executable()
        assert nxt is child2

    def test_is_complete(self):
        tree = TaskTree(session_id="test-004")
        root = TaskNode(name="root", description="r", primitive_type=PrimitiveType.TRANSFORMATION)
        tree.root = root
        assert not tree.is_complete()
        root.status = NodeStatus.COMPLETE
        assert tree.is_complete()

    def test_summary(self):
        tree = TaskTree(session_id="test-005")
        root = TaskNode(name="root", description="r", primitive_type=PrimitiveType.ORCHESTRATION)
        tree.root = root
        summary = tree.summary()
        assert "pending" in summary


class TestSignals:
    def test_uncertainty_types_partition(self):
        """Every uncertainty type should be in exactly one routing set."""
        all_types = set(UncertaintyType)
        interrupt_set = set(INTERRUPT_IMMEDIATELY)
        batch_set = set(BATCH_AND_NOTIFY)
        assert interrupt_set | batch_set == all_types
        assert interrupt_set & batch_set == set()

    def test_drift_signal_correction_context(self):
        signal = DriftSignal(
            id="test1234",
            drift_type=DriftType.PHASE,
            severity=Severity.BLOCK,
            node_id="node1234",
            step_name="enumerate_edge_cases",
            evidence="Implementation code found",
            output_excerpt="def solve(): ...",
            correction_template="Remove implementation code",
        )
        ctx = signal.correction_context()
        assert "DRIFT DETECTED: phase" in ctx
        assert "Implementation code found" in ctx
        assert "Remove implementation code" in ctx
