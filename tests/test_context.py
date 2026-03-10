"""Tests for runner/context.py — ContextBuilder.

Testable without live LLM calls, as specified in the build order docs.
"""

from __future__ import annotations

import textwrap

import pytest

from superpowers_runner.runner.context import (
    SYSTEM_BLOCK,
    ContextBuilder,
    SchemaRegistry,
)
from superpowers_runner.schema.nodes import (
    NodeSchema,
    SchemaField,
    StepRecord,
    StepStatus,
    TaskNode,
)
from superpowers_runner.schema.primitives import PrimitiveType, get_steps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    primitive_type: PrimitiveType = PrimitiveType.TRANSFORMATION,
    name: str = "generate_token",
    description: str = "Generates a reset token",
) -> TaskNode:
    return TaskNode(
        name=name,
        description=description,
        primitive_type=primitive_type,
    )


def _active_step(node: TaskNode, step_index: int = 0) -> StepRecord:
    """Mark a specific step as active and return it."""
    step = node.steps[step_index]
    step.status = StepStatus.ACTIVE
    return step


def _complete_steps(node: TaskNode, up_to: int, outputs: list[str] | None = None) -> None:
    """Mark steps 0..up_to-1 as COMPLETE with optional outputs."""
    for i in range(up_to):
        node.steps[i].status = StepStatus.COMPLETE
        if outputs and i < len(outputs):
            node.steps[i].output = outputs[i]
        else:
            node.steps[i].output = f"Output for {node.steps[i].template.name}"


# ============================================================================
# SchemaRegistry
# ============================================================================


class TestSchemaRegistry:
    def test_empty_registry(self):
        reg = SchemaRegistry()
        assert reg.to_string() == "(no established schemas yet)"

    def test_register_and_render(self):
        reg = SchemaRegistry()
        reg.register("User", "id: str, name: str, email: str")
        reg.register("Order", "id: str, user_id: str, total: float")
        output = reg.to_string()
        assert "User:" in output
        assert "id: str, name: str, email: str" in output
        assert "Order:" in output

    def test_token_estimate(self):
        reg = SchemaRegistry()
        reg.register("User", "id: str")
        estimate = reg.token_estimate()
        assert estimate > 0
        assert isinstance(estimate, int)


# ============================================================================
# ContextBuilder — basic structure
# ============================================================================


class TestContextBuilderStructure:
    def test_contains_system_block(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder(session_id="test-001")
        result = builder.build(node, step)

        assert "=== SYSTEM CONTEXT ===" in result
        assert "Complete ONLY the current step" in result
        assert "Do not proceed to subsequent steps" in result
        assert "Do not implement work that belongs to a different node" in result

    def test_contains_session_and_node_identity(self):
        node = _make_node(name="my_transform")
        step = _active_step(node)
        builder = ContextBuilder(session_id="auth-flow-123")
        result = builder.build(node, step)

        assert "Session: auth-flow-123" in result
        assert "my_transform" in result
        assert node.id in result
        assert "transformation" in result

    def test_contains_node_spec(self):
        node = _make_node()
        node.input_schema = NodeSchema(
            fields=[SchemaField(name="user_id", type_annotation="str")],
            description="User identifier",
        )
        node.output_schema = NodeSchema(
            fields=[SchemaField(name="token", type_annotation="ResetToken")],
            description="Reset token",
        )
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "=== NODE SPEC ===" in result
        assert "user_id: str" in result
        assert "token: ResetToken" in result

    def test_contains_progress(self):
        node = _make_node()
        _complete_steps(node, 2)
        step = _active_step(node, 2)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "=== PROGRESS ===" in result
        assert "Completed steps:" in result
        assert "Current step:" in result
        assert "Remaining steps:" in result

    def test_contains_step_prompt_last(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "=== STEP PROMPT ===" in result
        # Step prompt should be the last section
        step_prompt_pos = result.rfind("=== STEP PROMPT ===")
        # No other section marker after it
        after_prompt = result[step_prompt_pos + len("=== STEP PROMPT ==="):]
        assert "=== " not in after_prompt

    def test_step_prompt_has_node_name_injected(self):
        node = _make_node(name="normalize_email")
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "normalize_email" in result


# ============================================================================
# ContextBuilder — correction context
# ============================================================================


class TestContextBuilderCorrection:
    def test_no_correction_by_default(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "=== CORRECTION CONTEXT ===" not in result

    def test_correction_included_when_provided(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        correction = (
            "DRIFT DETECTED: phase\n"
            "EVIDENCE: Implementation code found during enumerate_edge_cases.\n"
            "CORRECTION REQUIRED: Remove all implementation code."
        )
        result = builder.build(node, step, correction_context=correction)

        assert "=== CORRECTION CONTEXT ===" in result
        assert "DRIFT DETECTED: phase" in result
        assert "Remove all implementation code" in result

    def test_correction_appears_before_node_context(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step, correction_context="Fix this")

        correction_pos = result.find("=== CORRECTION CONTEXT ===")
        node_spec_pos = result.find("=== NODE SPEC ===")
        assert correction_pos < node_spec_pos

    def test_correction_appears_after_system_block(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step, correction_context="Fix this")

        system_pos = result.find("=== SYSTEM CONTEXT ===")
        correction_pos = result.find("=== CORRECTION CONTEXT ===")
        assert system_pos < correction_pos


# ============================================================================
# ContextBuilder — completed step outputs
# ============================================================================


class TestContextBuilderCompletedSteps:
    def test_no_completed_steps(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "COMPLETED STEPS" not in result

    def test_completed_steps_included(self):
        node = _make_node()
        _complete_steps(node, 2, ["Input: user_id: str", "Output: token: str"])
        step = _active_step(node, 2)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "=== COMPLETED STEPS" in result
        assert "do not repeat this work" in result
        assert "Input: user_id: str" in result
        assert "Output: token: str" in result

    def test_completed_step_names_in_headers(self):
        node = _make_node()
        _complete_steps(node, 2, ["schema def", "output def"])
        step = _active_step(node, 2)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "define_input_schema" in result
        assert "define_output_schema" in result

    def test_current_step_output_not_in_completed(self):
        """The current step's output should not appear in completed section."""
        node = _make_node()
        _complete_steps(node, 1, ["First output"])
        step = node.steps[1]
        step.status = StepStatus.ACTIVE
        step.output = "Should not appear in completed"
        builder = ContextBuilder()
        result = builder.build(node, step)

        # Check the completed section doesn't include the active step
        completed_section_start = result.find("=== COMPLETED STEPS")
        if completed_section_start >= 0:
            completed_section = result[completed_section_start:]
            assert "Should not appear in completed" not in completed_section


# ============================================================================
# ContextBuilder — schema registry
# ============================================================================


class TestContextBuilderSchemaRegistry:
    def test_no_registry(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step, global_schema=None)

        assert "=== GLOBAL SCHEMA REGISTRY ===" not in result

    def test_empty_registry_omitted(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step, global_schema=SchemaRegistry())

        assert "=== GLOBAL SCHEMA REGISTRY ===" not in result

    def test_populated_registry_included(self):
        node = _make_node()
        step = _active_step(node)
        reg = SchemaRegistry()
        reg.register("User", "id: str, name: str")
        reg.register("ResetToken", "value: str, expires_at: datetime")
        builder = ContextBuilder()
        result = builder.build(node, step, global_schema=reg)

        assert "=== GLOBAL SCHEMA REGISTRY ===" in result
        assert "User:" in result
        assert "ResetToken:" in result

    def test_registry_before_step_prompt(self):
        node = _make_node()
        step = _active_step(node)
        reg = SchemaRegistry()
        reg.register("User", "id: str")
        builder = ContextBuilder()
        result = builder.build(node, step, global_schema=reg)

        registry_pos = result.find("=== GLOBAL SCHEMA REGISTRY ===")
        step_pos = result.find("=== STEP PROMPT ===")
        assert registry_pos < step_pos


# ============================================================================
# ContextBuilder — section ordering
# ============================================================================


class TestContextBuilderOrdering:
    def test_full_ordering(self):
        """All sections should appear in the documented order."""
        node = _make_node()
        node.input_schema = NodeSchema(
            fields=[SchemaField(name="x", type_annotation="int")],
        )
        _complete_steps(node, 2, ["output1", "output2"])
        step = _active_step(node, 2)

        reg = SchemaRegistry()
        reg.register("SomeType", "field: str")

        builder = ContextBuilder(session_id="ord-test")
        result = builder.build(
            node, step,
            global_schema=reg,
            correction_context="Fix the thing",
        )

        positions = [
            result.find("=== SYSTEM CONTEXT ==="),
            result.find("=== CORRECTION CONTEXT ==="),
            result.find("=== NODE SPEC ==="),
            result.find("=== COMPLETED STEPS"),
            result.find("=== GLOBAL SCHEMA REGISTRY ==="),
            result.find("=== STEP PROMPT ==="),
        ]
        # All should be present
        assert all(p >= 0 for p in positions), f"Missing sections: {positions}"
        # And in ascending order
        assert positions == sorted(positions), f"Wrong order: {positions}"


# ============================================================================
# ContextBuilder — progress tracking
# ============================================================================


class TestContextBuilderProgress:
    def test_first_step_shows_no_completed(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "Completed steps: none" in result

    def test_middle_step_shows_progress(self):
        node = _make_node()
        _complete_steps(node, 3)
        step = _active_step(node, 3)
        builder = ContextBuilder()
        result = builder.build(node, step)

        # Should show 3 completed step names
        assert "define_input_schema" in result
        assert "define_output_schema" in result
        assert "enumerate_edge_cases" in result
        # Current step
        assert f"Current step: {step.template.name}" in result

    def test_implementation_notes_included(self):
        node = _make_node()
        node.implementation_notes = "Use hashlib for token generation"
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "Notes: Use hashlib for token generation" in result


# ============================================================================
# ContextBuilder — budget management
# ============================================================================


class TestContextBuilderBudget:
    def test_under_budget_no_summarization(self):
        node = _make_node()
        _complete_steps(node, 2, ["short output", "short output 2"])
        step = _active_step(node, 2)
        builder = ContextBuilder(max_tokens=100_000)
        result = builder.build(node, step)

        # Full outputs should be present
        assert "short output" in result
        assert "summarized" not in result.lower()

    def test_over_budget_triggers_summarization(self):
        node = _make_node()
        # Create very long step outputs to bust the budget
        long_output = "x" * 10_000
        _complete_steps(node, 3, [long_output, long_output, long_output])
        step = _active_step(node, 3)

        # Set a very tight budget
        builder = ContextBuilder(max_tokens=500)
        result = builder.build(node, step)

        # Should contain the summarization marker
        assert "summarized" in result.lower() or "truncated" in result.lower()

    def test_budget_preserves_all_required_sections(self):
        """Even under budget pressure, system block and step prompt survive."""
        node = _make_node()
        long_output = "y" * 10_000
        _complete_steps(node, 2, [long_output, long_output])
        step = _active_step(node, 2)

        builder = ContextBuilder(max_tokens=500)
        result = builder.build(node, step)

        assert "=== SYSTEM CONTEXT ===" in result
        assert "=== STEP PROMPT ===" in result
        assert "=== NODE SPEC ===" in result


# ============================================================================
# ContextBuilder — build_system_prompt
# ============================================================================


class TestBuildSystemPrompt:
    def test_returns_system_block(self):
        builder = ContextBuilder()
        prompt = builder.build_system_prompt()
        assert prompt == SYSTEM_BLOCK
        assert "Complete ONLY the current step" in prompt


# ============================================================================
# ContextBuilder — different node types
# ============================================================================


class TestContextBuilderNodeTypes:
    def test_orchestration_node(self):
        node = _make_node(
            PrimitiveType.ORCHESTRATION,
            name="checkout_flow",
            description="Full checkout process",
        )
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "orchestration" in result
        assert "checkout_flow" in result

    def test_data_model_node(self):
        node = _make_node(
            PrimitiveType.DATA_MODEL,
            name="User",
            description="Core user model",
        )
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "data_model" in result
        assert "User" in result

    def test_mutation_node(self):
        node = _make_node(
            PrimitiveType.MUTATION,
            name="save_order",
            description="Persist order to database",
        )
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "mutation" in result
        assert "save_order" in result


# ============================================================================
# ContextBuilder — empty/edge cases
# ============================================================================


class TestContextBuilderEdgeCases:
    def test_empty_schema_fields(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "Input: none" in result
        assert "Output: none" in result

    def test_no_session_id(self):
        node = _make_node()
        step = _active_step(node)
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "Session:" in result  # Still present, just empty

    def test_step_with_no_prompt_template_vars(self):
        """Steps whose templates don't use {node.name} should still work."""
        from superpowers_runner.schema.primitives import StepTemplate

        node = _make_node()
        step = StepRecord(
            template=StepTemplate(
                name="custom_step",
                prompt_template="Just do the thing.",
            ),
            status=StepStatus.ACTIVE,
        )
        builder = ContextBuilder()
        result = builder.build(node, step)

        assert "Just do the thing." in result
