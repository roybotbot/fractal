"""Tests for planner layer — classifier, decomposer, planner.

Tested with mock LLM clients that return canned responses.
"""

from __future__ import annotations

import json

import pytest

from superpowers_runner.planner.classifier import (
    CLASSIFICATION_PROMPT,
    MAX_CLASSIFICATION_RETRIES,
    ClassificationFailure,
    classify,
)
from superpowers_runner.planner.decomposer import (
    CircularDependency,
    DecompositionFailure,
    InvalidChildType,
    decompose,
)
from superpowers_runner.planner.planner import Planner
from superpowers_runner.schema.nodes import TaskNode, TaskTree
from superpowers_runner.schema.primitives import COMPOSITION_TYPES, PrimitiveType


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLM:
    def __init__(self, responses: list[str] | None = None, default: str = ""):
        self._responses = list(responses) if responses else []
        self._default = default
        self.calls: list[dict] = []

    def call(self, prompt: str, max_tokens: int = 4096, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if self._responses:
            return self._responses.pop(0)
        return self._default


# ============================================================================
# Classifier
# ============================================================================


class TestClassifier:
    def test_clean_classification(self):
        llm = MockLLM(default="transformation")
        result = classify(llm, "format a currency string")
        assert result == PrimitiveType.TRANSFORMATION

    def test_strips_whitespace(self):
        llm = MockLLM(default="  mutation  \n")
        result = classify(llm, "save user to database")
        assert result == PrimitiveType.MUTATION

    def test_case_insensitive(self):
        llm = MockLLM(default="ORCHESTRATION")
        result = classify(llm, "checkout flow")
        assert result == PrimitiveType.ORCHESTRATION

    def test_strips_trailing_period(self):
        llm = MockLLM(default="query.")
        result = classify(llm, "find user by email")
        assert result == PrimitiveType.QUERY

    def test_extracts_type_from_verbose_response(self):
        """If the model returns extra text, try to extract the type."""
        llm = MockLLM(default="I think this is a data_model type")
        result = classify(llm, "define User schema")
        assert result == PrimitiveType.DATA_MODEL

    def test_retries_on_invalid_response(self):
        llm = MockLLM(responses=["invalid_type", "still_wrong", "validation"])
        result = classify(llm, "check password strength")
        assert result == PrimitiveType.VALIDATION
        assert len(llm.calls) == 3

    def test_raises_after_max_retries(self):
        llm = MockLLM(default="completely_wrong_type")
        with pytest.raises(ClassificationFailure) as exc_info:
            classify(llm, "do something")
        assert len(exc_info.value.attempts) == MAX_CLASSIFICATION_RETRIES
        assert "completely_wrong_type" in exc_info.value.attempts[0]

    def test_all_types_classifiable(self):
        """Every PrimitiveType value should be accepted."""
        for ptype in PrimitiveType:
            llm = MockLLM(default=ptype.value)
            result = classify(llm, "test task")
            assert result == ptype

    def test_prompt_contains_all_types(self):
        """The classification prompt should list all type values."""
        for ptype in PrimitiveType:
            assert ptype.value in CLASSIFICATION_PROMPT

    def test_max_tokens_is_small(self):
        """Classification should use a small token budget."""
        llm = MockLLM(default="transformation")
        classify(llm, "test")
        assert llm.calls[0]["max_tokens"] == 50


# ============================================================================
# Decomposer
# ============================================================================


class TestDecomposer:
    def _make_parent(self) -> TaskNode:
        return TaskNode(
            name="checkout_flow",
            description="Full checkout process with payment and shipping",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )

    def _make_valid_response(self) -> str:
        return json.dumps({
            "children": [
                {
                    "name": "CartTotal",
                    "type": "aggregation",
                    "description": "Sum cart item totals",
                    "dependencies": [],
                },
                {
                    "name": "process_payment",
                    "type": "mutation",
                    "description": "Charge the payment method",
                    "dependencies": ["CartTotal"],
                },
                {
                    "name": "send_confirmation",
                    "type": "mutation",
                    "description": "Send order confirmation email",
                    "dependencies": ["process_payment"],
                },
            ]
        })

    def test_valid_decomposition(self):
        parent = self._make_parent()
        llm = MockLLM(default=self._make_valid_response())
        children = decompose(llm, parent)

        assert len(children) == 3
        assert children[0].name == "CartTotal"
        assert children[0].primitive_type == PrimitiveType.AGGREGATION
        assert children[1].name == "process_payment"
        assert children[1].primitive_type == PrimitiveType.MUTATION
        assert children[2].name == "send_confirmation"

    def test_children_have_parent_id(self):
        parent = self._make_parent()
        llm = MockLLM(default=self._make_valid_response())
        children = decompose(llm, parent)

        for child in children:
            assert child.parent_id == parent.id

    def test_dependencies_resolved_to_ids(self):
        parent = self._make_parent()
        llm = MockLLM(default=self._make_valid_response())
        children = decompose(llm, parent)

        # process_payment depends on CartTotal
        cart_total = children[0]
        process_payment = children[1]
        assert cart_total.id in process_payment.dependency_ids

    def test_handles_markdown_code_block(self):
        parent = self._make_parent()
        response = "```json\n" + self._make_valid_response() + "\n```"
        llm = MockLLM(default=response)
        children = decompose(llm, parent)
        assert len(children) == 3

    def test_invalid_type_retries(self):
        parent = self._make_parent()
        bad_response = json.dumps({
            "children": [
                {"name": "x", "type": "INVALID", "description": "bad"}
            ]
        })
        good_response = self._make_valid_response()
        llm = MockLLM(responses=[bad_response, bad_response, good_response])
        children = decompose(llm, parent)
        assert len(children) == 3
        assert len(llm.calls) == 3

    def test_missing_name_retries(self):
        parent = self._make_parent()
        bad_response = json.dumps({
            "children": [
                {"type": "mutation", "description": "no name"}
            ]
        })
        good_response = self._make_valid_response()
        llm = MockLLM(responses=[bad_response, good_response])
        children = decompose(llm, parent)
        assert len(children) == 3

    def test_self_referential_dependency_retries(self):
        parent = self._make_parent()
        bad_response = json.dumps({
            "children": [
                {
                    "name": "loopy",
                    "type": "mutation",
                    "description": "depends on self",
                    "dependencies": ["loopy"],
                }
            ]
        })
        good_response = self._make_valid_response()
        llm = MockLLM(responses=[bad_response, good_response])
        children = decompose(llm, parent)
        assert len(children) == 3

    def test_circular_dependency_retries(self):
        parent = self._make_parent()
        bad_response = json.dumps({
            "children": [
                {"name": "a", "type": "mutation", "description": "d", "dependencies": ["b"]},
                {"name": "b", "type": "mutation", "description": "d", "dependencies": ["a"]},
            ]
        })
        good_response = self._make_valid_response()
        llm = MockLLM(responses=[bad_response, good_response])
        children = decompose(llm, parent)
        assert len(children) == 3

    def test_all_retries_fail_raises(self):
        parent = self._make_parent()
        llm = MockLLM(default="not json at all {{{")
        with pytest.raises(DecompositionFailure) as exc_info:
            decompose(llm, parent)
        assert "checkout_flow" in str(exc_info.value)

    def test_empty_children_list_retries(self):
        parent = self._make_parent()
        bad_response = json.dumps({"children": []})
        good_response = self._make_valid_response()
        llm = MockLLM(responses=[bad_response, good_response])
        children = decompose(llm, parent)
        assert len(children) == 3

    def test_missing_children_key_retries(self):
        parent = self._make_parent()
        bad_response = json.dumps({"nodes": []})
        good_response = self._make_valid_response()
        llm = MockLLM(responses=[bad_response, good_response])
        children = decompose(llm, parent)
        assert len(children) == 3

    def test_children_get_steps_from_type(self):
        """Each child node should have steps derived from its type."""
        parent = self._make_parent()
        llm = MockLLM(default=self._make_valid_response())
        children = decompose(llm, parent)

        for child in children:
            assert len(child.steps) > 0


# ============================================================================
# Planner (integration of classifier + decomposer)
# ============================================================================


class TestPlanner:
    def test_leaf_task_produces_single_node_tree(self):
        """A leaf-type task should produce a tree with just the root."""
        root_json = json.dumps({
            "name": "normalize_email",
            "description": "Normalize email to lowercase",
            "notes": "Use .lower().strip()",
        })
        llm = MockLLM(responses=["transformation", root_json])
        planner = Planner(llm)
        tree = planner.plan("normalize email addresses", session_id="test-001")

        assert tree.session_id == "test-001"
        assert tree.root is not None
        assert tree.root.primitive_type == PrimitiveType.TRANSFORMATION
        assert tree.root.name == "normalize_email"
        assert len(tree.root.sub_nodes) == 0

    def test_composition_task_decomposes(self):
        """An orchestration task should produce root + children."""
        decomp = json.dumps({
            "children": [
                {"name": "Model", "type": "data_model", "description": "d", "dependencies": []},
                {"name": "Save", "type": "mutation", "description": "d", "dependencies": ["Model"]},
            ]
        })
        root_json = json.dumps({
            "name": "user_flow",
            "description": "User creation flow",
        })
        llm = MockLLM(responses=["orchestration", root_json, decomp])
        planner = Planner(llm)
        tree = planner.plan("user registration", session_id="test-002")

        assert tree.root.primitive_type == PrimitiveType.ORCHESTRATION
        assert len(tree.root.sub_nodes) == 2
        # Children should be registered in tree index
        for child in tree.root.sub_nodes:
            assert tree.get(child.id) is child

    def test_root_registered_in_tree(self):
        root_json = json.dumps({"name": "x", "description": "d"})
        llm = MockLLM(responses=["query", root_json])
        planner = Planner(llm)
        tree = planner.plan("find user", session_id="test-003")

        assert tree.get(tree.root.id) is tree.root

    def test_fallback_on_bad_root_json(self):
        """If root JSON is invalid, fallback to task-derived name."""
        llm = MockLLM(responses=["mutation", "not valid json {{{"])
        planner = Planner(llm)
        tree = planner.plan("save order to db")

        assert tree.root is not None
        assert tree.root.primitive_type == PrimitiveType.MUTATION
        # Name should be derived from task
        assert tree.root.name != ""

    def test_classification_failure_propagates(self):
        llm = MockLLM(default="totally_invalid_type_xyz")
        planner = Planner(llm)
        with pytest.raises(ClassificationFailure):
            planner.plan("do something weird")

    def test_decomposition_failure_propagates(self):
        root_json = json.dumps({"name": "flow", "description": "d"})
        llm = MockLLM(responses=["orchestration", root_json, "bad", "bad", "bad"])
        planner = Planner(llm)
        with pytest.raises(DecompositionFailure):
            planner.plan("complex flow")
