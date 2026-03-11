"""Tests for step and gate template coverage — all 21 primitive types."""

from __future__ import annotations

import pytest

from superpowers_runner.schema.primitives import (
    COMPOSITION_TYPES,
    PrimitiveType,
    StepTemplate,
    get_steps,
    STEP_TEMPLATES,
)
from superpowers_runner.schema.gates import (
    GateTemplate,
    get_gates,
    GATE_TEMPLATES,
)


class TestStepTemplatesCoverage:
    """Every PrimitiveType must have its own step template (no generic fallback)."""

    def test_all_types_have_explicit_templates(self):
        """No type should fall through to _GENERIC_STEPS."""
        for pt in PrimitiveType:
            assert pt in STEP_TEMPLATES, (
                f"{pt.value} not in STEP_TEMPLATES — falls through to generic"
            )

    def test_all_templates_have_steps(self):
        for pt in PrimitiveType:
            steps = get_steps(pt)
            assert len(steps) >= 2, (
                f"{pt.value} has only {len(steps)} steps — minimum is 2"
            )

    def test_all_step_templates_are_frozen(self):
        for pt in PrimitiveType:
            for step in get_steps(pt):
                assert isinstance(step, StepTemplate)
                assert step.name
                assert step.prompt_template

    def test_no_duplicate_step_names_within_type(self):
        for pt in PrimitiveType:
            names = [s.name for s in get_steps(pt)]
            assert len(names) == len(set(names)), (
                f"{pt.value} has duplicate step names: {names}"
            )


class TestGateTemplatesCoverage:
    """Every PrimitiveType must have its own gate template."""

    def test_all_types_have_explicit_gates(self):
        for pt in PrimitiveType:
            assert pt in GATE_TEMPLATES, (
                f"{pt.value} not in GATE_TEMPLATES — falls through to generic"
            )

    def test_all_types_have_gates(self):
        for pt in PrimitiveType:
            gates = get_gates(pt)
            assert len(gates) >= 2, (
                f"{pt.value} has only {len(gates)} gates — minimum is 2"
            )

    def test_all_gate_templates_valid(self):
        for pt in PrimitiveType:
            for gate in get_gates(pt):
                assert isinstance(gate, GateTemplate)
                assert gate.name
                assert gate.check_type
                assert gate.on_failure in ("block", "abort")

    def test_no_duplicate_gate_names_within_type(self):
        for pt in PrimitiveType:
            names = [g.name for g in get_gates(pt)]
            assert len(names) == len(set(names)), (
                f"{pt.value} has duplicate gate names: {names}"
            )


class TestCompositionTypeTemplates:
    """Composition types have specific requirements."""

    def test_composition_types_have_children_gate(self):
        for pt in COMPOSITION_TYPES:
            gates = get_gates(pt)
            has_children_gate = any(
                g.check_type == "children_have_types" for g in gates
            )
            assert has_children_gate, (
                f"Composition type {pt.value} missing children_have_types gate"
            )

    def test_composition_children_gate_is_abort(self):
        for pt in COMPOSITION_TYPES:
            gates = get_gates(pt)
            for g in gates:
                if g.check_type == "children_have_types":
                    assert g.on_failure == "abort", (
                        f"{pt.value}: children_have_types should abort, not block"
                    )


class TestStepTemplateContent:
    """Spot-check step template content for specific types."""

    def test_config_has_startup_validation(self):
        steps = get_steps(PrimitiveType.CONFIG)
        names = [s.name for s in steps]
        assert "define_startup_validation" in names

    def test_event_handler_has_idempotency(self):
        steps = get_steps(PrimitiveType.EVENT_HANDLER)
        names = [s.name for s in steps]
        assert "define_idempotency_strategy" in names

    def test_pipeline_has_type_chain(self):
        steps = get_steps(PrimitiveType.PIPELINE)
        names = [s.name for s in steps]
        assert "define_type_chain" in names

    def test_router_has_default_route(self):
        steps = get_steps(PrimitiveType.ROUTER)
        names = [s.name for s in steps]
        assert "define_default_route" in names

    def test_cache_has_eviction_policy(self):
        steps = get_steps(PrimitiveType.CACHE)
        names = [s.name for s in steps]
        assert "define_eviction_policy" in names

    def test_auth_guard_has_rejection_behavior(self):
        steps = get_steps(PrimitiveType.AUTH_GUARD)
        names = [s.name for s in steps]
        assert "define_rejection_behavior" in names

    def test_retry_policy_has_exhaustion(self):
        steps = get_steps(PrimitiveType.RETRY_POLICY)
        names = [s.name for s in steps]
        assert "define_exhaustion_behavior" in names

    def test_observer_no_behavior_modification_gate(self):
        gates = get_gates(PrimitiveType.OBSERVER)
        names = [g.name for g in gates]
        assert "no_behavior_modification" in names

    def test_fixture_no_magic_values_gate(self):
        gates = get_gates(PrimitiveType.FIXTURE)
        names = [g.name for g in gates]
        assert "no_magic_values" in names

    def test_integration_test_has_failure_scenario_gate(self):
        gates = get_gates(PrimitiveType.INTEGRATION_TEST)
        names = [g.name for g in gates]
        assert "has_failure_scenario" in names

    def test_contract_test_implementation_agnostic_gate(self):
        gates = get_gates(PrimitiveType.CONTRACT_TEST)
        names = [g.name for g in gates]
        assert "implementation_agnostic" in names

    def test_aggregation_same_gates_as_transformation(self):
        """Aggregation is a specialization of transformation — similar gates."""
        agg_check_types = {g.check_type for g in get_gates(PrimitiveType.AGGREGATION)}
        trans_check_types = {g.check_type for g in get_gates(PrimitiveType.TRANSFORMATION)}
        # Aggregation should share ast_no_any, ast_no_io, run_tests
        shared = agg_check_types & trans_check_types
        assert len(shared) >= 3


class TestForbiddenArtifacts:
    """Planning steps should forbid code; implementation steps should not."""

    def test_planning_steps_forbid_code(self):
        """First step of most types should forbid some form of code."""
        planning_types = [
            PrimitiveType.DATA_MODEL,
            PrimitiveType.TRANSFORMATION,
            PrimitiveType.CONFIG,
            PrimitiveType.AGGREGATION,
        ]
        for pt in planning_types:
            first = get_steps(pt)[0]
            assert first.forbidden_artifacts, (
                f"{pt.value} first step '{first.name}' has no forbidden artifacts"
            )

    def test_implementation_steps_allow_code(self):
        """Steps with 'implement' in name should not forbid implementation_code."""
        for pt in PrimitiveType:
            for step in get_steps(pt):
                if "implement" in step.name:
                    assert "implementation_code" not in step.forbidden_artifacts, (
                        f"{pt.value}/{step.name} forbids implementation_code"
                    )
