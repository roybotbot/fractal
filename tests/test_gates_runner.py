"""Tests for runner/gates_runner.py — GateRunner.

Tests are built against hand-constructed TaskNodes using the schema layer,
as specified in the build order docs.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, UTC

import pytest

from superpowers_runner.runner.gates_runner import GateRunner
from superpowers_runner.schema.nodes import (
    GateResult,
    NodeStatus,
    StepRecord,
    StepStatus,
    TaskNode,
)
from superpowers_runner.schema.primitives import GateTemplate, PrimitiveType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    primitive_type: PrimitiveType,
    step_outputs: dict[str, str] | None = None,
) -> TaskNode:
    """Create a TaskNode with completed steps populated with output."""
    node = TaskNode(
        name="test_node",
        description="A test node",
        primitive_type=primitive_type,
    )
    if step_outputs:
        for step in node.steps:
            if step.template.name in step_outputs:
                step.status = StepStatus.COMPLETE
                step.output = step_outputs[step.template.name]
            else:
                step.status = StepStatus.COMPLETE
                step.output = ""
    else:
        # Mark all steps complete with empty output
        for step in node.steps:
            step.status = StepStatus.COMPLETE
    return node


class MockLLM:
    def __init__(self, response: str = "yes"):
        self.response = response
        self.calls: list[dict] = []

    def call(self, prompt: str, max_tokens: int = 4096, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system})
        return self.response


# ---------------------------------------------------------------------------
# Basic run_all behavior
# ---------------------------------------------------------------------------


class TestGateRunnerBasic:
    def test_run_all_returns_gate_results(self):
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": textwrap.dedent("""\
                def add(a: int, b: int) -> int:
                    return a + b
            """),
        })
        runner = GateRunner()
        results = runner.run_all(node)
        assert isinstance(results, list)
        assert len(results) == len(node.gate_results)
        for r in results:
            assert isinstance(r, GateResult)
            assert r.checked_at is not None

    def test_already_passed_gates_skipped(self):
        node = _make_node(PrimitiveType.TRANSFORMATION)
        # Pre-mark first gate as passed
        node.gate_results[0].passed = True
        node.gate_results[0].checked_at = datetime.now(UTC)
        node.gate_results[0].evidence = "Already passed"

        runner = GateRunner()
        runner.run_all(node)

        # First gate should still have original evidence (not re-run)
        assert node.gate_results[0].evidence == "Already passed"

    def test_failed_gates_rerun(self):
        """Gates that failed previously should be re-run."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": "def add(a: int, b: int) -> int:\n    return a + b\n",
        })
        # Pre-mark first gate as failed
        node.gate_results[0].passed = False
        node.gate_results[0].checked_at = datetime.now(UTC)
        node.gate_results[0].evidence = "Old failure"

        runner = GateRunner()
        runner.run_all(node)

        # Should have been re-run with fresh evidence
        assert node.gate_results[0].evidence != "Old failure"


# ---------------------------------------------------------------------------
# AST-based gate checks via GateRunner
# ---------------------------------------------------------------------------


class TestGateRunnerAstChecks:
    def test_transformation_no_any_passes(self):
        """Clean transformation code should pass no_any_types gate."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": textwrap.dedent("""\
                def normalize_email(email: str) -> str:
                    return email.strip().lower()
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        no_any = next(g for g in node.gate_results if g.gate.name == "no_any_types")
        assert no_any.passed

    def test_transformation_any_type_fails(self):
        """Any annotation should fail the no_any_types gate."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": textwrap.dedent("""\
                from typing import Any
                def process(data: Any) -> str:
                    return str(data)
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        no_any = next(g for g in node.gate_results if g.gate.name == "no_any_types")
        assert not no_any.passed
        assert "Any" in no_any.evidence

    def test_transformation_no_io_passes(self):
        """Pure transformation should pass no_io_calls gate."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": textwrap.dedent("""\
                def double(x: int) -> int:
                    return x * 2
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        no_io = next(g for g in node.gate_results if g.gate.name == "no_io_calls")
        assert no_io.passed

    def test_transformation_io_import_fails(self):
        """I/O import in transformation should fail no_io_calls gate."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": textwrap.dedent("""\
                import requests
                def fetch(url: str) -> str:
                    return requests.get(url).text
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        no_io = next(g for g in node.gate_results if g.gate.name == "no_io_calls")
        assert not no_io.passed
        assert "requests" in no_io.evidence

    def test_mutation_exception_handling_passes(self):
        """Mutation with try/except should pass failure_modes_handled gate."""
        node = _make_node(PrimitiveType.MUTATION, {
            "implement_with_error_handling": textwrap.dedent("""\
                def save_user(user_id: str, name: str) -> None:
                    try:
                        db.save({"id": user_id, "name": name})
                    except DatabaseError as e:
                        raise SaveFailed(str(e))
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        exc_gate = next(g for g in node.gate_results if g.gate.name == "failure_modes_handled")
        assert exc_gate.passed

    def test_mutation_no_exception_handling_fails(self):
        """Mutation without try/except should fail failure_modes_handled gate."""
        node = _make_node(PrimitiveType.MUTATION, {
            "implement_with_error_handling": textwrap.dedent("""\
                def save_user(user_id: str, name: str) -> None:
                    db.save({"id": user_id, "name": name})
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        exc_gate = next(g for g in node.gate_results if g.gate.name == "failure_modes_handled")
        assert not exc_gate.passed

    def test_query_no_mutations_passes(self):
        """Read-only query should pass no_side_effects gate."""
        node = _make_node(PrimitiveType.QUERY, {
            "implement": textwrap.dedent("""\
                def find_user(user_id: str) -> dict[str, str]:
                    result = db.find_one({"id": user_id})
                    return result
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        no_mut = next(g for g in node.gate_results if g.gate.name == "no_side_effects")
        assert no_mut.passed

    def test_query_with_write_fails(self):
        """Query with write operations should fail no_side_effects gate."""
        node = _make_node(PrimitiveType.QUERY, {
            "implement": textwrap.dedent("""\
                def find_and_update(user_id: str) -> dict[str, str]:
                    result = db.find_one({"id": user_id})
                    result.last_accessed = "now"
                    db.save(result)
                    return result
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        no_mut = next(g for g in node.gate_results if g.gate.name == "no_side_effects")
        assert not no_mut.passed

    def test_unit_test_no_shared_state_passes(self):
        """Independent tests should pass tests_are_independent gate."""
        node = _make_node(PrimitiveType.UNIT_TEST, {
            "implement_cases": textwrap.dedent("""\
                def test_add():
                    assert 1 + 1 == 2

                def test_sub():
                    assert 3 - 1 == 2
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        indep = next(g for g in node.gate_results if g.gate.name == "tests_are_independent")
        assert indep.passed

    def test_unit_test_shared_state_fails(self):
        """Tests sharing mutable state should fail tests_are_independent gate."""
        node = _make_node(PrimitiveType.UNIT_TEST, {
            "implement_cases": textwrap.dedent("""\
                state = []

                def test_one():
                    state.append(1)
                    assert len(state) == 1

                def test_two():
                    state.append(2)
                    assert len(state) == 2
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        indep = next(g for g in node.gate_results if g.gate.name == "tests_are_independent")
        assert not indep.passed


# ---------------------------------------------------------------------------
# Test runner gates
# ---------------------------------------------------------------------------


class TestGateRunnerTestChecks:
    def test_transformation_test_count_passes(self):
        """Transformation with 3+ test functions passes edge_cases_covered."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "write_failing_tests": textwrap.dedent("""\
                def test_normal():
                    assert double(2) == 4

                def test_zero():
                    assert double(0) == 0

                def test_negative():
                    assert double(-1) == -2
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        edge = next(g for g in node.gate_results if g.gate.name == "edge_cases_covered")
        assert edge.passed

    def test_transformation_test_count_fails(self):
        """Transformation with <3 test functions fails edge_cases_covered."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "write_failing_tests": textwrap.dedent("""\
                def test_normal():
                    assert double(2) == 4
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        edge = next(g for g in node.gate_results if g.gate.name == "edge_cases_covered")
        assert not edge.passed

    def test_mutation_failure_path_tested_passes(self):
        """Mutation test with exception testing passes failure_path_tested."""
        node = _make_node(PrimitiveType.MUTATION, {
            "write_failing_tests": textwrap.dedent("""\
                import pytest

                def test_save_success():
                    assert save_user("1", "Alice") is None

                def test_save_failure():
                    with pytest.raises(SaveFailed):
                        save_user("bad", "")
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        fp = next(g for g in node.gate_results if g.gate.name == "failure_path_tested")
        assert fp.passed

    def test_mutation_failure_path_not_tested_fails(self):
        """Mutation test without exception testing fails failure_path_tested."""
        node = _make_node(PrimitiveType.MUTATION, {
            "write_failing_tests": textwrap.dedent("""\
                def test_save_success():
                    assert save_user("1", "Alice") is None

                def test_save_another():
                    assert save_user("2", "Bob") is None
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        fp = next(g for g in node.gate_results if g.gate.name == "failure_path_tested")
        assert not fp.passed


# ---------------------------------------------------------------------------
# Structural gates
# ---------------------------------------------------------------------------


class TestGateRunnerStructuralChecks:
    def test_data_model_docstring_passes(self):
        """Data model with docstring passes invariants_documented gate."""
        node = _make_node(PrimitiveType.DATA_MODEL, {
            "implement_model": textwrap.dedent('''\
                class User:
                    """Represents a user. Invariants: email must be non-empty."""
                    name: str
                    email: str
            '''),
        })
        runner = GateRunner()
        runner.run_all(node)

        doc = next(g for g in node.gate_results if g.gate.name == "invariants_documented")
        assert doc.passed

    def test_data_model_no_docstring_fails(self):
        """Data model without docstring fails invariants_documented gate."""
        node = _make_node(PrimitiveType.DATA_MODEL, {
            "implement_model": textwrap.dedent("""\
                class User:
                    name: str
                    email: str
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        doc = next(g for g in node.gate_results if g.gate.name == "invariants_documented")
        assert not doc.passed

    def test_data_model_test_file_exists(self):
        """Data model with test content passes validation_tests_exist gate."""
        node = _make_node(PrimitiveType.DATA_MODEL, {
            "write_validation_tests": textwrap.dedent("""\
                def test_user_valid():
                    user = User(name="Alice", email="a@b.com")
                    assert user.name == "Alice"
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        tests_exist = next(g for g in node.gate_results if g.gate.name == "validation_tests_exist")
        assert tests_exist.passed

    def test_orchestration_children_typed_passes(self):
        """Orchestration with typed children passes all_children_typed gate."""
        node = _make_node(PrimitiveType.ORCHESTRATION)
        child = TaskNode(
            name="step1",
            description="step",
            primitive_type=PrimitiveType.MUTATION,
            parent_id=node.id,
        )
        node.sub_nodes = [child]

        runner = GateRunner()
        runner.run_all(node)

        children_gate = next(g for g in node.gate_results if g.gate.name == "all_children_typed")
        assert children_gate.passed

    def test_orchestration_no_children_fails(self):
        """Orchestration with no children fails all_children_typed gate."""
        node = _make_node(PrimitiveType.ORCHESTRATION)
        # No sub_nodes — composition node with no children

        runner = GateRunner()
        runner.run_all(node)

        children_gate = next(g for g in node.gate_results if g.gate.name == "all_children_typed")
        assert not children_gate.passed
        assert children_gate.gate.on_failure == "abort"

    def test_orchestration_rollback_documented_passes(self):
        """Orchestration with rollback docs passes rollback_defined gate."""
        node = _make_node(PrimitiveType.ORCHESTRATION, {
            "define_rollback": textwrap.dedent("""\
                Rollback plan:
                - If step 2 fails, rollback step 1 by deleting the token.
                - If step 3 fails, revert the email send (not possible, log instead).
            """),
        })
        runner = GateRunner()
        runner.run_all(node)

        rollback = next(g for g in node.gate_results if g.gate.name == "rollback_defined")
        assert rollback.passed

    def test_orchestration_no_rollback_fails(self):
        """Orchestration without rollback docs fails rollback_defined gate."""
        node = _make_node(PrimitiveType.ORCHESTRATION, {
            "define_rollback": "The steps are all independent.",
        })
        runner = GateRunner()
        runner.run_all(node)

        rollback = next(g for g in node.gate_results if g.gate.name == "rollback_defined")
        assert not rollback.passed


# ---------------------------------------------------------------------------
# LLM judge gates
# ---------------------------------------------------------------------------


class TestGateRunnerLlmJudge:
    def test_mutation_no_business_logic_passes(self):
        """LLM judge answering 'pure_io' should pass no_business_logic gate."""
        node = _make_node(PrimitiveType.MUTATION, {
            "implement_with_error_handling": textwrap.dedent("""\
                def save_user(user_id: str, name: str) -> None:
                    try:
                        db.save({"id": user_id, "name": name})
                    except Exception as e:
                        raise SaveFailed(str(e))
            """),
        })
        mock_llm = MockLLM("pure_io")
        runner = GateRunner(llm_client=mock_llm)
        runner.run_all(node)

        biz = next(g for g in node.gate_results if g.gate.name == "no_business_logic")
        assert biz.passed
        assert len(mock_llm.calls) >= 1

    def test_mutation_has_business_logic_fails(self):
        """LLM judge answering 'contains_logic' should fail no_business_logic gate."""
        node = _make_node(PrimitiveType.MUTATION, {
            "implement_with_error_handling": "def save(x): db.save(transform(x))",
        })
        mock_llm = MockLLM("contains_logic")
        runner = GateRunner(llm_client=mock_llm)
        runner.run_all(node)

        biz = next(g for g in node.gate_results if g.gate.name == "no_business_logic")
        assert not biz.passed

    def test_llm_judge_no_client_fails(self):
        """LLM judge gate without client should fail gracefully."""
        node = _make_node(PrimitiveType.MUTATION)
        runner = GateRunner(llm_client=None)
        runner.run_all(node)

        biz = next(g for g in node.gate_results if g.gate.name == "no_business_logic")
        assert not biz.passed
        assert "No LLM client" in biz.evidence


# ---------------------------------------------------------------------------
# Test file resolver
# ---------------------------------------------------------------------------


class TestGateRunnerTestFileResolver:
    def test_run_tests_with_resolver(self, tmp_path):
        """run_tests gate should use the test_file_resolver to find the test file."""
        test_file = tmp_path / "test_impl.py"
        test_file.write_text(textwrap.dedent("""\
            def test_one():
                assert True

            def test_two():
                assert True

            def test_three():
                assert True
        """))

        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": "def double(x: int) -> int:\n    return x * 2\n",
        })
        runner = GateRunner(test_file_resolver=lambda n: str(test_file))
        runner.run_all(node)

        tests_gate = next(g for g in node.gate_results if g.gate.name == "tests_exist_and_pass")
        assert tests_gate.passed

    def test_run_tests_no_resolver_fails(self):
        """run_tests gate with no resolver returns failure."""
        node = _make_node(PrimitiveType.TRANSFORMATION)
        runner = GateRunner()
        runner.run_all(node)

        tests_gate = next(g for g in node.gate_results if g.gate.name == "tests_exist_and_pass")
        assert not tests_gate.passed
        assert "No test file path" in tests_gate.evidence


# ---------------------------------------------------------------------------
# Source collection
# ---------------------------------------------------------------------------


class TestSourceCollection:
    def test_collects_from_all_completed_steps(self):
        """_collect_source concatenates all completed step outputs."""
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "define_input_schema": "input: str",
            "define_output_schema": "output: int",
            "implement_minimal": "def convert(x: str) -> int:\n    return int(x)\n",
        })
        runner = GateRunner()
        source = runner._collect_source(node)
        assert "input: str" in source
        assert "output: int" in source
        assert "def convert" in source

    def test_empty_steps_produce_empty_source(self):
        node = _make_node(PrimitiveType.TRANSFORMATION)
        runner = GateRunner()
        source = runner._collect_source(node)
        # All steps complete but empty output
        assert source == ""


# ---------------------------------------------------------------------------
# run_single
# ---------------------------------------------------------------------------


class TestRunSingle:
    def test_run_single_updates_gate_result(self):
        node = _make_node(PrimitiveType.TRANSFORMATION, {
            "implement_minimal": "def f(x: int) -> int:\n    return x\n",
        })
        runner = GateRunner()

        gate_result = node.gate_results[0]  # no_any_types
        assert gate_result.checked_at is None

        runner.run_single(node, gate_result)

        assert gate_result.checked_at is not None
        assert gate_result.passed
        assert gate_result.evidence != ""
