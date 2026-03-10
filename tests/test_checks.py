"""Tests for detector/checks.py — gate check implementations.

Each check function gets tested for both passing and failing cases,
plus edge cases specific to that check type.
"""

from __future__ import annotations

import textwrap

import pytest

from superpowers_runner.detector.checks import (
    CheckResult,
    ast_has_exception_handling,
    ast_no_any,
    ast_no_io,
    ast_no_mutations,
    ast_no_shared_mutable_state,
    children_have_types,
    file_contains_tests,
    has_docstring,
    has_documented_exceptions,
    has_rollback_documentation,
    llm_judge,
    run_check,
    run_tests,
    CHECKS,
)
from superpowers_runner.detector import checks as _checks_mod

# Import test_* functions with non-test_ names to avoid pytest collection
check_test_count_minimum = _checks_mod.test_count_minimum
check_test_covers_exceptions = _checks_mod.test_covers_exceptions
check_test_covers_partial_failure = _checks_mod.test_covers_partial_failure
from superpowers_runner.schema.nodes import TaskNode
from superpowers_runner.schema.primitives import PrimitiveType


# ============================================================================
# ast_no_any
# ============================================================================


class TestAstNoAny:
    def test_clean_code_passes(self):
        source = textwrap.dedent("""\
            def greet(name: str) -> str:
                return f"Hello, {name}"
        """)
        result = ast_no_any(source)
        assert result.passed
        assert "No Any annotations" in result.evidence

    def test_any_annotation_fails(self):
        source = textwrap.dedent("""\
            from typing import Any

            def process(data: Any) -> str:
                return str(data)
        """)
        result = ast_no_any(source)
        assert not result.passed
        assert "Any" in result.evidence

    def test_bare_dict_annotation_fails(self):
        source = textwrap.dedent("""\
            def process(data: dict) -> str:
                return str(data)
        """)
        result = ast_no_any(source)
        assert not result.passed
        assert "dict" in result.evidence

    def test_bare_object_annotation_fails(self):
        source = textwrap.dedent("""\
            def process(data: object) -> str:
                return str(data)
        """)
        result = ast_no_any(source)
        assert not result.passed
        assert "object" in result.evidence

    def test_typed_dict_passes(self):
        source = textwrap.dedent("""\
            def process(data: dict[str, int]) -> str:
                return str(data)
        """)
        result = ast_no_any(source)
        assert result.passed

    def test_typing_any_fails(self):
        source = textwrap.dedent("""\
            import typing

            x: typing.Any = 42
        """)
        result = ast_no_any(source)
        assert not result.passed
        assert "typing.Any" in result.evidence

    def test_return_type_any_fails(self):
        source = textwrap.dedent("""\
            from typing import Any

            def get_data() -> Any:
                return {}
        """)
        result = ast_no_any(source)
        assert not result.passed

    def test_class_attribute_any_fails(self):
        source = textwrap.dedent("""\
            from typing import Any

            class Config:
                value: Any = None
        """)
        result = ast_no_any(source)
        assert not result.passed

    def test_syntax_error_fails(self):
        source = "def broken("
        result = ast_no_any(source)
        assert not result.passed
        assert "SyntaxError" in result.evidence

    def test_no_annotations_passes(self):
        source = textwrap.dedent("""\
            def add(a, b):
                return a + b
        """)
        result = ast_no_any(source)
        assert result.passed

    def test_multiple_violations_reported(self):
        source = textwrap.dedent("""\
            from typing import Any

            def process(a: Any, b: dict) -> object:
                return None
        """)
        result = ast_no_any(source)
        assert not result.passed
        # Should flag all three
        assert "Any" in result.evidence
        assert "dict" in result.evidence
        assert "object" in result.evidence

    def test_line_numbers_in_evidence(self):
        source = textwrap.dedent("""\
            x: int = 1
            y: str = "hello"
            from typing import Any
            z: Any = None
        """)
        result = ast_no_any(source)
        assert not result.passed
        assert "line 4" in result.evidence


# ============================================================================
# ast_no_io
# ============================================================================


class TestAstNoIo:
    def test_clean_code_passes(self):
        source = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b
        """)
        result = ast_no_io(source)
        assert result.passed

    def test_import_os_fails(self):
        source = textwrap.dedent("""\
            import os
            def get_cwd():
                return os.getcwd()
        """)
        result = ast_no_io(source)
        assert not result.passed
        assert "import os" in result.evidence

    def test_from_os_import_fails(self):
        source = textwrap.dedent("""\
            from os.path import join
            def make_path(a, b):
                return join(a, b)
        """)
        result = ast_no_io(source)
        assert not result.passed
        assert "os.path" in result.evidence

    def test_import_requests_fails(self):
        source = textwrap.dedent("""\
            import requests
            def fetch(url):
                return requests.get(url)
        """)
        result = ast_no_io(source)
        assert not result.passed
        assert "requests" in result.evidence

    def test_import_httpx_fails(self):
        source = textwrap.dedent("""\
            import httpx
        """)
        result = ast_no_io(source)
        assert not result.passed

    def test_import_aiohttp_fails(self):
        source = textwrap.dedent("""\
            import aiohttp
        """)
        result = ast_no_io(source)
        assert not result.passed

    def test_import_sys_fails(self):
        source = textwrap.dedent("""\
            import sys
        """)
        result = ast_no_io(source)
        assert not result.passed

    def test_open_call_fails(self):
        source = textwrap.dedent("""\
            def read_file(path):
                with open(path) as f:
                    return f.read()
        """)
        result = ast_no_io(source)
        assert not result.passed
        assert "open()" in result.evidence

    def test_os_qualified_call_fails(self):
        source = textwrap.dedent("""\
            import os
            x = os.path.join("a", "b")
        """)
        result = ast_no_io(source)
        assert not result.passed

    def test_custom_forbidden_modules(self):
        source = textwrap.dedent("""\
            import boto3
        """)
        # Default modules don't include boto3
        result = ast_no_io(source)
        assert result.passed

        # Custom list does
        result = ast_no_io(source, forbidden_modules=["boto3"])
        assert not result.passed

    def test_custom_forbidden_builtins(self):
        source = textwrap.dedent("""\
            x = print("hello")
        """)
        result = ast_no_io(source, forbidden_builtins=["print"])
        assert not result.passed

    def test_safe_stdlib_passes(self):
        source = textwrap.dedent("""\
            import json
            import re
            from collections import defaultdict
            from dataclasses import dataclass

            def parse(data: str) -> dict:
                return json.loads(data)
        """)
        result = ast_no_io(source)
        assert result.passed

    def test_syntax_error_fails(self):
        source = "import ("
        result = ast_no_io(source)
        assert not result.passed
        assert "SyntaxError" in result.evidence


# ============================================================================
# ast_has_exception_handling
# ============================================================================


class TestAstHasExceptionHandling:
    def test_try_except_passes(self):
        source = textwrap.dedent("""\
            def save(data):
                try:
                    db.save(data)
                except DatabaseError as e:
                    raise SaveFailed(str(e))
        """)
        result = ast_has_exception_handling(source)
        assert result.passed

    def test_no_try_except_fails(self):
        source = textwrap.dedent("""\
            def save(data):
                db.save(data)
        """)
        result = ast_has_exception_handling(source)
        assert not result.passed
        assert "No try/except" in result.evidence

    def test_try_finally_without_except_fails(self):
        source = textwrap.dedent("""\
            def save(data):
                try:
                    db.save(data)
                finally:
                    db.close()
        """)
        result = ast_has_exception_handling(source)
        assert not result.passed

    def test_nested_try_except_passes(self):
        source = textwrap.dedent("""\
            def process():
                def inner():
                    try:
                        risky()
                    except Exception:
                        pass
                inner()
        """)
        result = ast_has_exception_handling(source)
        assert result.passed

    def test_syntax_error_fails(self):
        source = "try:"
        result = ast_has_exception_handling(source)
        assert not result.passed


# ============================================================================
# ast_no_mutations
# ============================================================================


class TestAstNoMutations:
    def test_read_only_passes(self):
        source = textwrap.dedent("""\
            def find_user(user_id: str):
                result = db.find_one({"id": user_id})
                return result
        """)
        result = ast_no_mutations(source)
        assert result.passed

    def test_attribute_assignment_fails(self):
        source = textwrap.dedent("""\
            def update_user(user, name):
                user.name = name
        """)
        result = ast_no_mutations(source)
        assert not result.passed
        assert "attribute assignment" in result.evidence

    def test_augmented_attribute_assignment_fails(self):
        source = textwrap.dedent("""\
            def increment(counter):
                counter.value += 1
        """)
        result = ast_no_mutations(source)
        assert not result.passed
        assert "augmented assignment" in result.evidence

    def test_write_method_calls_fail(self):
        source = textwrap.dedent("""\
            def create_user(data):
                db.save(data)
        """)
        result = ast_no_mutations(source)
        assert not result.passed
        assert ".save()" in result.evidence

    def test_insert_method_fails(self):
        source = textwrap.dedent("""\
            def add_record(record):
                collection.insert(record)
        """)
        result = ast_no_mutations(source)
        assert not result.passed

    def test_delete_method_fails(self):
        source = textwrap.dedent("""\
            def remove_user(user_id):
                db.delete(user_id)
        """)
        result = ast_no_mutations(source)
        assert not result.passed

    def test_local_variable_assignment_passes(self):
        """Local variable assignment (not attribute) should pass."""
        source = textwrap.dedent("""\
            def compute(data):
                result = data * 2
                total = result + 1
                return total
        """)
        result = ast_no_mutations(source)
        assert result.passed

    def test_commit_call_fails(self):
        source = textwrap.dedent("""\
            def save_transaction(session):
                session.commit()
        """)
        result = ast_no_mutations(source)
        assert not result.passed

    def test_syntax_error_fails(self):
        source = "def broken(:"
        result = ast_no_mutations(source)
        assert not result.passed


# ============================================================================
# ast_no_shared_mutable_state
# ============================================================================


class TestAstNoSharedMutableState:
    def test_no_module_state_passes(self):
        source = textwrap.dedent("""\
            def test_add():
                assert 1 + 1 == 2

            def test_sub():
                assert 3 - 1 == 2
        """)
        result = ast_no_shared_mutable_state(source)
        assert result.passed

    def test_shared_list_fails(self):
        source = textwrap.dedent("""\
            shared_data = []

            def test_one():
                shared_data.append(1)
                assert len(shared_data) == 1

            def test_two():
                shared_data.append(2)
                assert len(shared_data) == 1
        """)
        result = ast_no_shared_mutable_state(source)
        assert not result.passed
        assert "shared_data" in result.evidence

    def test_shared_dict_fails(self):
        source = textwrap.dedent("""\
            cache = {}

            def test_first():
                cache["key"] = "value"

            def test_second():
                assert cache.get("key") is None
        """)
        result = ast_no_shared_mutable_state(source)
        assert not result.passed
        assert "cache" in result.evidence

    def test_module_level_constant_passes(self):
        """Immutable module-level values (strings, ints, tuples) should pass."""
        source = textwrap.dedent("""\
            MAX_RETRIES = 3
            NAME = "test"
            TUPLE = (1, 2, 3)

            def test_one():
                assert MAX_RETRIES == 3

            def test_two():
                assert NAME == "test"
        """)
        result = ast_no_shared_mutable_state(source)
        assert result.passed

    def test_used_in_one_test_passes(self):
        """Mutable state used in only one test is fine."""
        source = textwrap.dedent("""\
            data = []

            def test_one():
                data.append(1)
                assert len(data) == 1

            def test_unrelated():
                assert True
        """)
        result = ast_no_shared_mutable_state(source)
        assert result.passed

    def test_class_based_tests(self):
        source = textwrap.dedent("""\
            items = []

            class TestSuite:
                def test_a(self):
                    items.append("a")

                def test_b(self):
                    items.append("b")
        """)
        result = ast_no_shared_mutable_state(source)
        assert not result.passed
        assert "items" in result.evidence

    def test_set_constructor_detected(self):
        source = textwrap.dedent("""\
            seen = set()

            def test_alpha():
                seen.add("a")

            def test_beta():
                seen.add("b")
        """)
        result = ast_no_shared_mutable_state(source)
        assert not result.passed
        assert "seen" in result.evidence

    def test_syntax_error_fails(self):
        source = "def test_(:"
        result = ast_no_shared_mutable_state(source)
        assert not result.passed


# ============================================================================
# test_count_minimum
# ============================================================================


class TestTestCountMinimum:
    def test_sufficient_count_passes(self):
        source = textwrap.dedent("""\
            def test_one():
                assert True

            def test_two():
                assert True

            def test_three():
                assert True
        """)
        result = check_test_count_minimum(source, minimum=3)
        assert result.passed
        assert "3 test functions" in result.evidence

    def test_insufficient_count_fails(self):
        source = textwrap.dedent("""\
            def test_one():
                assert True
        """)
        result = check_test_count_minimum(source, minimum=3)
        assert not result.passed
        assert "1 test functions" in result.evidence
        assert "minimum: 3" in result.evidence

    def test_zero_tests_fails(self):
        source = textwrap.dedent("""\
            def helper():
                return 42
        """)
        result = check_test_count_minimum(source, minimum=1)
        assert not result.passed

    def test_class_methods_counted(self):
        source = textwrap.dedent("""\
            class TestMath:
                def test_add(self):
                    assert 1 + 1 == 2

                def test_sub(self):
                    assert 3 - 1 == 2

                def test_mul(self):
                    assert 2 * 3 == 6
        """)
        result = check_test_count_minimum(source, minimum=3)
        assert result.passed

    def test_default_minimum_is_three(self):
        source = textwrap.dedent("""\
            def test_one(): pass
            def test_two(): pass
        """)
        result = check_test_count_minimum(source)
        assert not result.passed


# ============================================================================
# test_covers_exceptions
# ============================================================================


class TestTestCoversExceptions:
    def test_pytest_raises_passes(self):
        source = textwrap.dedent("""\
            import pytest

            def test_invalid_input():
                with pytest.raises(ValueError):
                    process(None)
        """)
        result = check_test_covers_exceptions(source)
        assert result.passed
        assert "test_invalid_input" in result.evidence

    def test_assert_raises_passes(self):
        source = textwrap.dedent("""\
            import unittest

            class TestProcess(unittest.TestCase):
                def test_error(self):
                    self.assertRaises(TypeError, process, None)
        """)
        result = check_test_covers_exceptions(source)
        assert result.passed

    def test_try_except_in_test_passes(self):
        source = textwrap.dedent("""\
            def test_handles_error():
                try:
                    risky_operation()
                    assert False, "Should have raised"
                except RuntimeError:
                    pass
        """)
        result = check_test_covers_exceptions(source)
        assert result.passed

    def test_no_exception_testing_fails(self):
        source = textwrap.dedent("""\
            def test_happy_path():
                result = process("valid")
                assert result == "ok"
        """)
        result = check_test_covers_exceptions(source)
        assert not result.passed

    def test_no_tests_at_all_fails(self):
        source = textwrap.dedent("""\
            def helper():
                return 42
        """)
        result = check_test_covers_exceptions(source)
        assert not result.passed
        assert "No test functions" in result.evidence


# ============================================================================
# test_covers_partial_failure
# ============================================================================


class TestTestCoversPartialFailure:
    def test_failure_named_test_passes(self):
        source = textwrap.dedent("""\
            def test_happy_path():
                assert True

            def test_partial_failure_scenario():
                assert True
        """)
        result = check_test_covers_partial_failure(source)
        assert result.passed
        assert "test_partial_failure_scenario" in result.evidence

    def test_error_named_test_passes(self):
        source = textwrap.dedent("""\
            def test_success():
                assert True

            def test_error_handling():
                assert True
        """)
        result = check_test_covers_partial_failure(source)
        assert result.passed

    def test_rollback_named_test_passes(self):
        source = textwrap.dedent("""\
            def test_rollback_on_failure():
                assert True
        """)
        result = check_test_covers_partial_failure(source)
        assert result.passed

    def test_only_happy_path_fails(self):
        source = textwrap.dedent("""\
            def test_happy_path():
                assert True

            def test_another_success():
                assert True
        """)
        result = check_test_covers_partial_failure(source)
        assert not result.passed

    def test_exception_testing_also_passes(self):
        source = textwrap.dedent("""\
            import pytest

            def test_success():
                assert True

            def test_handling():
                with pytest.raises(RuntimeError):
                    process()
        """)
        result = check_test_covers_partial_failure(source)
        assert result.passed


# ============================================================================
# file_contains_tests
# ============================================================================


class TestFileContainsTests:
    def test_has_test_functions_passes(self):
        source = textwrap.dedent("""\
            def test_something():
                assert True
        """)
        result = file_contains_tests(source)
        assert result.passed

    def test_empty_source_fails(self):
        result = file_contains_tests("")
        assert not result.passed
        assert "empty" in result.evidence

    def test_no_test_functions_fails(self):
        source = textwrap.dedent("""\
            def helper():
                return 42

            class Config:
                pass
        """)
        result = file_contains_tests(source)
        assert not result.passed

    def test_whitespace_only_fails(self):
        result = file_contains_tests("   \n  \n  ")
        assert not result.passed

    def test_test_class_methods_pass(self):
        source = textwrap.dedent("""\
            class TestSuite:
                def test_one(self):
                    assert True
        """)
        result = file_contains_tests(source)
        assert result.passed


# ============================================================================
# has_docstring
# ============================================================================


class TestHasDocstring:
    def test_class_with_docstring_passes(self):
        source = textwrap.dedent('''\
            class User:
                """Represents a user in the system."""
                name: str
                email: str
        ''')
        result = has_docstring(source)
        assert result.passed

    def test_function_with_docstring_passes(self):
        source = textwrap.dedent('''\
            def process(data: str) -> str:
                """Process the input data and return result."""
                return data.upper()
        ''')
        result = has_docstring(source)
        assert result.passed

    def test_class_without_docstring_fails(self):
        source = textwrap.dedent("""\
            class User:
                name: str
                email: str
        """)
        result = has_docstring(source)
        assert not result.passed
        assert "User" in result.evidence

    def test_empty_docstring_fails(self):
        source = textwrap.dedent('''\
            class User:
                """"""
                name: str
        ''')
        result = has_docstring(source)
        assert not result.passed

    def test_no_definitions_fails(self):
        source = textwrap.dedent("""\
            x = 42
            y = "hello"
        """)
        result = has_docstring(source)
        assert not result.passed
        assert "No class or function" in result.evidence

    def test_first_definition_checked(self):
        """Only the primary (first) class/function needs a docstring."""
        source = textwrap.dedent('''\
            class Primary:
                """This is the main class."""
                pass

            class Secondary:
                pass
        ''')
        result = has_docstring(source)
        assert result.passed


# ============================================================================
# has_documented_exceptions
# ============================================================================


class TestHasDocumentedExceptions:
    def test_documented_raises_passes(self):
        source = textwrap.dedent('''\
            def process(data):
                """Process input data.

                Raises:
                    ValueError: If data is invalid.
                """
                if not data:
                    raise ValueError("data is required")
        ''')
        result = has_documented_exceptions(source)
        assert result.passed
        assert "ValueError" in result.evidence

    def test_undocumented_raises_fails(self):
        source = textwrap.dedent('''\
            def process(data):
                """Process input data."""
                if not data:
                    raise ValueError("data is required")
                if data == "bad":
                    raise TypeError("wrong type")
        ''')
        result = has_documented_exceptions(source)
        assert not result.passed
        # At least one should be undocumented
        evidence = result.evidence
        assert "Undocumented" in evidence

    def test_no_raises_passes(self):
        source = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b
        """)
        result = has_documented_exceptions(source)
        assert result.passed
        assert "No exceptions raised" in result.evidence

    def test_raises_in_docstring_counted(self):
        source = textwrap.dedent('''\
            class Service:
                """Service class.

                Raises:
                    ConnectionError: when connection fails
                    TimeoutError: when request times out
                """
                def connect(self):
                    raise ConnectionError("failed")

                def fetch(self):
                    raise TimeoutError("timed out")
        ''')
        result = has_documented_exceptions(source)
        assert result.passed


# ============================================================================
# has_rollback_documentation
# ============================================================================


class TestHasRollbackDocumentation:
    def test_rollback_keyword_passes(self):
        source = textwrap.dedent('''\
            class OrderFlow:
                """Orchestration for order processing.

                Rollback behavior:
                    If payment fails, the order is cancelled.
                """
                pass
        ''')
        result = has_rollback_documentation(source)
        assert result.passed
        assert "rollback" in result.evidence

    def test_compensate_keyword_passes(self):
        source = textwrap.dedent("""\
            # compensating transaction: undo the reservation
            def handle_failure():
                pass
        """)
        result = has_rollback_documentation(source)
        assert result.passed
        assert "compensat" in result.evidence

    def test_undo_keyword_passes(self):
        source = textwrap.dedent("""\
            def undo_changes():
                # undo the previous operations
                pass
        """)
        result = has_rollback_documentation(source)
        assert result.passed

    def test_revert_keyword_passes(self):
        source = textwrap.dedent("""\
            # revert to previous state on failure
            pass
        """)
        result = has_rollback_documentation(source)
        assert result.passed

    def test_no_rollback_docs_fails(self):
        source = textwrap.dedent("""\
            class OrderFlow:
                def process(self):
                    step_one()
                    step_two()
                    step_three()
        """)
        result = has_rollback_documentation(source)
        assert not result.passed
        assert "No rollback documentation" in result.evidence


# ============================================================================
# children_have_types
# ============================================================================


class TestChildrenHaveTypes:
    def test_all_children_typed_passes(self):
        parent = TaskNode(
            name="password_reset_flow",
            description="Password reset orchestration",
            primitive_type=PrimitiveType.ORCHESTRATION,
        )
        child1 = TaskNode(
            name="ResetToken",
            description="Token model",
            primitive_type=PrimitiveType.DATA_MODEL,
            parent_id=parent.id,
        )
        child2 = TaskNode(
            name="generate_token",
            description="Token generation",
            primitive_type=PrimitiveType.TRANSFORMATION,
            parent_id=parent.id,
        )
        parent.sub_nodes = [child1, child2]

        result = children_have_types("", node=parent)
        assert result.passed
        assert "ResetToken" in result.evidence

    def test_no_node_fails(self):
        result = children_have_types("", node=None)
        assert not result.passed

    def test_composition_no_children_fails(self):
        parent = TaskNode(
            name="empty_pipeline",
            description="Empty pipeline",
            primitive_type=PrimitiveType.PIPELINE,
        )
        result = children_have_types("", node=parent)
        assert not result.passed
        assert "no children" in result.evidence

    def test_leaf_node_no_children_passes(self):
        leaf = TaskNode(
            name="add",
            description="Addition",
            primitive_type=PrimitiveType.TRANSFORMATION,
        )
        result = children_have_types("", node=leaf)
        assert result.passed


# ============================================================================
# llm_judge
# ============================================================================


class MockLLMClient:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    def call(self, prompt: str, max_tokens: int = 4096, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system})
        return self.response


class TestLlmJudge:
    def test_expected_answer_passes(self):
        client = MockLLMClient("pure_io")
        result = llm_judge(
            "def save(x): db.save(x)",
            llm_client=client,
            question="Is this pure I/O?",
            expected_answer="pure_io",
        )
        assert result.passed

    def test_unexpected_answer_fails(self):
        client = MockLLMClient("contains_logic")
        result = llm_judge(
            "def save(x): db.save(x)",
            llm_client=client,
            question="Is this pure I/O?",
            expected_answer="pure_io",
        )
        assert not result.passed

    def test_no_client_fails(self):
        result = llm_judge(
            "code",
            llm_client=None,
            question="Q?",
            expected_answer="yes",
        )
        assert not result.passed
        assert "No LLM client" in result.evidence

    def test_no_question_fails(self):
        client = MockLLMClient("yes")
        result = llm_judge("code", llm_client=client, question="", expected_answer="yes")
        assert not result.passed

    def test_case_insensitive_match(self):
        client = MockLLMClient("Yes")
        result = llm_judge(
            "code",
            llm_client=client,
            question="Is it correct?",
            expected_answer="yes",
        )
        assert result.passed

    def test_retries_on_empty_response(self):
        """Empty responses should trigger retries up to 3 times."""
        call_count = 0
        responses = ["", "", "yes"]

        class RetryClient:
            def call(self, prompt, max_tokens=4096, system=None):
                nonlocal call_count
                resp = responses[call_count] if call_count < len(responses) else ""
                call_count += 1
                return resp

        result = llm_judge(
            "code",
            llm_client=RetryClient(),
            question="Is it correct?",
            expected_answer="yes",
        )
        assert result.passed
        assert call_count == 3


# ============================================================================
# run_tests (limited testing — subprocess)
# ============================================================================


class TestRunTests:
    def test_no_file_path_fails(self):
        result = run_tests("", test_file_path=None)
        assert not result.passed
        assert "No test file path" in result.evidence

    def test_nonexistent_file_fails(self):
        result = run_tests("", test_file_path="/nonexistent/test_file.py")
        assert not result.passed

    def test_passing_test_file(self, tmp_path):
        test_file = tmp_path / "test_passing.py"
        test_file.write_text("def test_ok():\n    assert True\n")
        result = run_tests("", test_file_path=str(test_file))
        assert result.passed
        assert "passed" in result.evidence.lower()

    def test_failing_test_file(self, tmp_path):
        test_file = tmp_path / "test_failing.py"
        test_file.write_text("def test_fail():\n    assert False\n")
        result = run_tests("", test_file_path=str(test_file))
        assert not result.passed
        assert "failed" in result.evidence.lower()


# ============================================================================
# run_check dispatch
# ============================================================================


class TestRunCheck:
    def test_dispatch_to_known_check(self):
        source = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b
        """)
        result = run_check("ast_no_any", source)
        assert result.passed

    def test_unknown_check_type_fails(self):
        result = run_check("nonexistent_check", "code")
        assert not result.passed
        assert "Unknown check_type" in result.evidence

    def test_all_gate_check_types_registered(self):
        """Every check_type referenced in GATE_TEMPLATES must exist in CHECKS."""
        from superpowers_runner.schema.gates import GATE_TEMPLATES

        referenced_types = set()
        for gates in GATE_TEMPLATES.values():
            for gate in gates:
                referenced_types.add(gate.check_type)

        for check_type in referenced_types:
            assert check_type in CHECKS, f"check_type '{check_type}' not in CHECKS registry"

    def test_parameters_passed_through(self):
        """Gate parameters like minimum should flow through run_check."""
        source = textwrap.dedent("""\
            def test_one(): pass
            def test_two(): pass
        """)
        result = run_check("test_count_minimum", source, minimum=2)
        assert result.passed

        result = run_check("test_count_minimum", source, minimum=5)
        assert not result.passed
