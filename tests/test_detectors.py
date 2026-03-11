"""Tests for detector/drift.py and detector/uncertainty.py."""

from __future__ import annotations

import textwrap

import pytest

from superpowers_runner.detector.drift import DriftDetector
from superpowers_runner.detector.uncertainty import UncertaintyDetector
from superpowers_runner.schema.nodes import (
    GateResult,
    NodeSchema,
    SchemaField,
    StepRecord,
    StepStatus,
    TaskNode,
)
from superpowers_runner.schema.primitives import GateTemplate, PrimitiveType, StepTemplate
from superpowers_runner.schema.signals import (
    DriftType,
    Resolution,
    Severity,
    UncertaintyType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    name: str = "generate_token",
    input_fields: list[tuple[str, str]] | None = None,
    output_fields: list[tuple[str, str]] | None = None,
) -> TaskNode:
    node = TaskNode(
        name=name,
        description="Test node",
        primitive_type=PrimitiveType.TRANSFORMATION,
    )
    if input_fields:
        node.input_schema = NodeSchema(
            fields=[SchemaField(name=n, type_annotation=t) for n, t in input_fields]
        )
    if output_fields:
        node.output_schema = NodeSchema(
            fields=[SchemaField(name=n, type_annotation=t) for n, t in output_fields]
        )
    return node


def _make_step(
    name: str = "implement_minimal",
    expected: list[str] | None = None,
    forbidden: list[str] | None = None,
) -> StepRecord:
    return StepRecord(
        template=StepTemplate(
            name=name,
            prompt_template=f"Do {name}",
            expected_artifacts=expected or [],
            forbidden_artifacts=forbidden or [],
        ),
        status=StepStatus.ACTIVE,
    )


# ============================================================================
# DriftDetector — scope drift
# ============================================================================


class TestScopeDrift:
    def test_no_drift_on_clean_output(self):
        node = _make_node(output_fields=[("token", "str")])
        detector = DriftDetector()
        signals = detector.check_scope(node, "token = generate()\n")
        assert len(signals) == 0

    def test_new_class_triggers_scope_drift(self):
        node = _make_node(output_fields=[("token", "ResetToken")])
        output = textwrap.dedent("""\
            class TokenStore:
                def save(self, token):
                    pass
        """)
        detector = DriftDetector()
        signals = detector.check_scope(node, output)
        assert len(signals) == 1
        assert signals[0].drift_type == DriftType.SCOPE
        assert "TokenStore" in signals[0].evidence

    def test_syntax_error_returns_empty(self):
        node = _make_node()
        detector = DriftDetector()
        signals = detector.check_scope(node, "def broken(")
        assert len(signals) == 0


# ============================================================================
# DriftDetector — phase drift
# ============================================================================


class TestPhaseDrift:
    def test_no_drift_when_no_forbidden(self):
        step = _make_step(forbidden=[])
        detector = DriftDetector()
        signals = detector.check_phase(step, "anything goes")
        assert len(signals) == 0

    def test_implementation_code_in_planning_step(self):
        step = _make_step(
            name="enumerate_edge_cases",
            forbidden=["implementation_code"],
        )
        output = textwrap.dedent("""\
            def compute_discount(price: float, rate: float) -> float:
                return price * (1 - rate)
        """)
        detector = DriftDetector()
        signals = detector.check_phase(step, output)
        assert len(signals) == 1
        assert signals[0].drift_type == DriftType.PHASE
        assert "implementation_code" in signals[0].evidence

    def test_test_code_in_planning_step(self):
        step = _make_step(
            name="enumerate_edge_cases",
            forbidden=["test_code"],
        )
        output = textwrap.dedent("""\
            def test_edge_case_null():
                assert compute(None) is None
        """)
        detector = DriftDetector()
        signals = detector.check_phase(step, output)
        assert len(signals) == 1
        assert "test_code" in signals[0].evidence

    def test_plain_text_no_drift(self):
        step = _make_step(
            name="enumerate_edge_cases",
            forbidden=["implementation_code", "test_code"],
        )
        output = "Edge cases:\n1. Null input\n2. Empty string\n3. Negative number"
        detector = DriftDetector()
        signals = detector.check_phase(step, output)
        assert len(signals) == 0


# ============================================================================
# DriftDetector — instruction adherence (fallback mode)
# ============================================================================


class TestInstructionDrift:
    def test_artifact_present_no_drift(self):
        step = _make_step(expected=["edge_case_list"])
        output = "Edge case list:\n1. null input\n2. overflow"
        detector = DriftDetector()
        signals = detector.check_instruction_adherence(step, output)
        assert len(signals) == 0

    def test_artifact_missing_triggers_drift(self):
        step = _make_step(expected=["edge_case_list"])
        output = "The transformation handles various scenarios."
        detector = DriftDetector()
        signals = detector.check_instruction_adherence(step, output)
        assert len(signals) == 1
        assert signals[0].drift_type == DriftType.INSTRUCTION

    def test_no_expected_artifacts_no_check(self):
        step = _make_step(expected=[])
        detector = DriftDetector()
        signals = detector.check_instruction_adherence(step, "anything")
        assert len(signals) == 0


# ============================================================================
# DriftDetector — schema consistency
# ============================================================================


class TestSchemaDrift:
    def test_no_registry_no_drift(self):
        detector = DriftDetector()
        signals = detector.check_schema_consistency({}, "user_id: str = 'abc'")
        assert len(signals) == 0

    def test_matching_types_no_drift(self):
        registry = {"User": "id: str, name: str"}
        output = "id: str = 'abc'\nname: str = 'Alice'"
        detector = DriftDetector()
        signals = detector.check_schema_consistency(registry, output)
        assert len(signals) == 0

    def test_type_mismatch_triggers_drift(self):
        registry = {"User": "user_id: str"}
        output = "user_id: int = 42"
        detector = DriftDetector()
        signals = detector.check_schema_consistency(registry, output)
        assert len(signals) == 1
        assert signals[0].drift_type == DriftType.SCHEMA
        assert "user_id" in signals[0].evidence
        assert "int" in signals[0].evidence


# ============================================================================
# DriftDetector — completion honesty
# ============================================================================


class TestCompletionDrift:
    def test_no_completion_claim_no_drift(self):
        node = _make_node()
        failing_gate = GateResult(
            gate=GateTemplate(name="test_gate", check_type="run_tests"),
            passed=False,
            evidence="Tests failed",
        )
        detector = DriftDetector()
        signals = detector.check_completion_honesty(
            node, "Here is the code:\ndef f(): pass", [failing_gate]
        )
        assert len(signals) == 0

    def test_completion_claim_with_failing_gate(self):
        node = _make_node()
        failing_gate = GateResult(
            gate=GateTemplate(name="test_gate", check_type="run_tests"),
            passed=False,
            evidence="Tests failed",
        )
        output = "The implementation is complete and all tests pass."
        detector = DriftDetector()
        signals = detector.check_completion_honesty(node, output, [failing_gate])
        assert len(signals) == 1
        assert signals[0].drift_type == DriftType.COMPLETION
        assert signals[0].severity == Severity.BLOCK

    def test_completion_claim_all_gates_passing(self):
        node = _make_node()
        passing_gate = GateResult(
            gate=GateTemplate(name="test_gate", check_type="run_tests"),
            passed=True,
            evidence="Tests passed",
        )
        output = "The implementation is complete."
        detector = DriftDetector()
        signals = detector.check_completion_honesty(node, output, [passing_gate])
        assert len(signals) == 0

    def test_no_gate_results_no_drift(self):
        node = _make_node()
        detector = DriftDetector()
        signals = detector.check_completion_honesty(node, "implementation is complete", [])
        assert len(signals) == 0


# ============================================================================
# DriftDetector — check_all
# ============================================================================


class TestDriftCheckAll:
    def test_check_all_aggregates(self):
        node = _make_node()
        step = _make_step(
            name="enumerate_edge_cases",
            expected=["edge_case_list"],
            forbidden=["implementation_code"],
        )
        output = textwrap.dedent("""\
            def compute(x: int) -> int:
                return x * 2
        """)
        detector = DriftDetector()
        signals = detector.check_all(node, step, output)
        # Should catch phase drift at minimum
        assert len(signals) >= 1
        assert all(s.node_id == node.id for s in signals)


# ============================================================================
# UncertaintyDetector — ambiguous scope
# ============================================================================


class TestAmbiguousScope:
    def test_new_function_triggers_uncertainty(self):
        node = _make_node(output_fields=[("token", "str")])
        output = textwrap.dedent("""\
            def generate_token() -> str:
                return "abc"

            def format_output(token: str) -> str:
                return f"Token: {token}"
        """)
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_scope(node, output)
        # format_output and generate_token are not in spec
        assert len(signals) >= 1
        assert all(s.uncertainty_type == UncertaintyType.AMBIGUOUS_SCOPE for s in signals)

    def test_private_functions_ignored(self):
        node = _make_node()
        output = textwrap.dedent("""\
            def _helper():
                pass
        """)
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_scope(node, output)
        assert len(signals) == 0

    def test_no_new_symbols_clean(self):
        node = _make_node()
        output = "x = 42\ny = x + 1"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_scope(node, output)
        assert len(signals) == 0


# ============================================================================
# UncertaintyDetector — ambiguous phase
# ============================================================================


class TestAmbiguousPhase:
    def test_code_block_in_planning_step(self):
        step = _make_step(
            name="enumerate_fields",
            forbidden=["code"],
        )
        output = "Fields:\n1. name\n\n```python\nclass User:\n    name: str\n```\n"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 1
        assert signals[0].uncertainty_type == UncertaintyType.AMBIGUOUS_PHASE

    def test_no_code_block_no_signal(self):
        step = _make_step(name="enumerate_fields", forbidden=["code"])
        output = "Fields:\n1. name: str\n2. email: str"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 0

    def test_non_planning_step_no_signal(self):
        step = _make_step(name="implement", forbidden=[])
        output = "```python\ndef f(): pass\n```"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 0

    def test_plain_text_in_fence_no_signal(self):
        """Fenced block with plain English (no code syntax) should not trigger."""
        step = _make_step(name="define_input_schema", forbidden=["code"])
        output = "Schema:\n\n```\nInput: celsius (numeric)\n```\n"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 0

    def test_short_label_no_signal(self):
        """A 1-2 line fenced block without code indicators should not trigger."""
        step = _make_step(name="enumerate_fields", forbidden=["code"])
        output = "```\nname: string\nemail: string\n```"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 0

    def test_real_function_in_fence_triggers(self):
        """A fenced block with def/class keywords should trigger."""
        step = _make_step(name="enumerate_fields", forbidden=["code"])
        output = "```python\ndef convert(c):\n    return c * 9/5 + 32\n```"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 1
        assert signals[0].uncertainty_type == UncertaintyType.AMBIGUOUS_PHASE

    def test_schema_step_allows_type_definitions(self):
        """Steps that expect schema output should not flag type defs as phase violations."""
        step = _make_step(
            name="define_input_schema",
            forbidden=["implementation_code"],
            expected=["input_schema"],
        )
        output = "```typescript\ntype CelsiusInput = {\n  celsius: number;\n};\n```"
        detector = UncertaintyDetector()
        signals = detector.check_ambiguous_phase(step, output)
        assert len(signals) == 0


# ============================================================================
# UncertaintyDetector — partial adherence
# ============================================================================


class TestPartialAdherence:
    def test_partial_match_triggers_signal(self):
        step = _make_step(expected=["edge_case_list"])
        output = "Here are some edge considerations."  # "edge" matches but not "case" and "list"
        detector = UncertaintyDetector()
        signals = detector.check_partial_adherence(step, output)
        assert len(signals) == 1
        assert signals[0].uncertainty_type == UncertaintyType.PARTIAL_ADHERENCE
        assert signals[0].default_resolution == Resolution.RETRY

    def test_full_match_no_signal(self):
        step = _make_step(expected=["input_schema"])
        output = "Input schema definition: user_id: str"
        detector = UncertaintyDetector()
        signals = detector.check_partial_adherence(step, output)
        assert len(signals) == 0

    def test_no_match_no_signal(self):
        """Complete miss is instruction drift, not partial adherence."""
        step = _make_step(expected=["validation_rules"])
        output = "The weather is nice today."
        detector = UncertaintyDetector()
        signals = detector.check_partial_adherence(step, output)
        assert len(signals) == 0


# ============================================================================
# UncertaintyDetector — schema near miss
# ============================================================================


class TestSchemaNearMiss:
    def test_similar_class_triggers_signal(self):
        output = textwrap.dedent("""\
            class UserModel:
                name: str
                email: str
                age: int
        """)
        registry = {"User": "name: str, email: str, age: int"}
        detector = UncertaintyDetector()
        signals = detector.check_schema_near_miss(output, registry)
        assert len(signals) == 1
        assert signals[0].uncertainty_type == UncertaintyType.SCHEMA_NEAR_MISS
        assert "UserModel" in signals[0].evidence
        assert "User" in signals[0].evidence

    def test_exact_match_no_signal(self):
        output = textwrap.dedent("""\
            class User:
                name: str
                email: str
        """)
        registry = {"User": "name: str, email: str"}
        detector = UncertaintyDetector()
        signals = detector.check_schema_near_miss(output, registry)
        assert len(signals) == 0

    def test_no_registry_no_signal(self):
        detector = UncertaintyDetector()
        signals = detector.check_schema_near_miss("class Foo: pass", None)
        assert len(signals) == 0


# ============================================================================
# UncertaintyDetector — token velocity
# ============================================================================


class TestTokenVelocity:
    def test_no_signal_with_insufficient_data(self):
        step = _make_step(name="implement")
        detector = UncertaintyDetector()
        signals = detector.check_token_velocity(step, "x" * 1000)
        assert len(signals) == 0  # Need 3+ data points

    def test_suspicious_speed_triggers_signal(self):
        detector = UncertaintyDetector()
        step = _make_step(name="implement_step")

        # Build up history with long outputs
        for _ in range(3):
            detector.check_token_velocity(step, "x" * 4000)

        # Now a very short output
        signals = detector.check_token_velocity(step, "x" * 100)
        assert len(signals) == 1
        assert signals[0].uncertainty_type == UncertaintyType.SUSPICIOUSLY_FAST

    def test_normal_speed_no_signal(self):
        detector = UncertaintyDetector()
        step = _make_step(name="impl_step")

        for _ in range(3):
            detector.check_token_velocity(step, "x" * 1000)

        # Similar length — no signal
        signals = detector.check_token_velocity(step, "x" * 900)
        assert len(signals) == 0


# ============================================================================
# UncertaintyDetector — self contradiction
# ============================================================================


class TestSelfContradiction:
    def test_claim_without_code_triggers_signal(self):
        output = (
            "Error handling is implemented for all failure modes.\n\n"
            "```python\n"
            "def save(data):\n"
            "    db.save(data)\n"
            "```"
        )
        detector = UncertaintyDetector()
        signals = detector.check_self_contradiction(output)
        assert len(signals) == 1
        assert signals[0].uncertainty_type == UncertaintyType.SELF_CONTRADICTION
        assert signals[0].default_resolution == Resolution.ESCALATE

    def test_claim_with_matching_code_no_signal(self):
        output = (
            "Error handling is implemented.\n\n"
            "```python\n"
            "def save(data):\n"
            "    try:\n"
            "        db.save(data)\n"
            "    except Exception:\n"
            "        pass\n"
            "```"
        )
        detector = UncertaintyDetector()
        signals = detector.check_self_contradiction(output)
        # try/except is present, matches the claim
        assert len(signals) == 0

    def test_no_claims_no_signal(self):
        output = "def add(a, b): return a + b"
        detector = UncertaintyDetector()
        signals = detector.check_self_contradiction(output)
        assert len(signals) == 0

    def test_typescript_tests_with_describe_no_signal(self):
        """Tests written with describe/it blocks should not trigger."""
        output = (
            "Tests have been written for all edge cases.\n\n"
            "```typescript\n"
            "describe('celsiusToFahrenheit', () => {\n"
            "  it('converts freezing point', () => {\n"
            "    expect(celsiusToFahrenheit(0)).toBe(32);\n"
            "  });\n"
            "});\n"
            "```"
        )
        detector = UncertaintyDetector()
        signals = detector.check_self_contradiction(output)
        assert len(signals) == 0

    def test_js_error_handling_with_catch_no_signal(self):
        """JS catch blocks should satisfy error handling claims."""
        output = (
            "Error handling is implemented.\n\n"
            "```javascript\n"
            "try {\n"
            "  doThing();\n"
            "} catch (e) {\n"
            "  handleError(e);\n"
            "}\n"
            "```"
        )
        detector = UncertaintyDetector()
        signals = detector.check_self_contradiction(output)
        assert len(signals) == 0


# ============================================================================
# UncertaintyDetector — check_all
# ============================================================================


class TestUncertaintyCheckAll:
    def test_check_all_fills_node_id(self):
        node = _make_node()
        step = _make_step()
        detector = UncertaintyDetector()
        signals = detector.check_all(node, step, "output text")
        for s in signals:
            assert s.node_id == node.id
