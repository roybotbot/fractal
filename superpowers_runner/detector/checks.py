"""Gate check implementations keyed by check_type string.

Each function receives source code (str), a TaskNode, and optional parameters.
Returns a CheckResult with passed/failed and evidence string.

These are called by the GateRunner to evaluate gate templates. They depend only
on the schema layer — no runner imports, no LLM calls (except llm_judge).
"""

from __future__ import annotations

import ast
import re
import subprocess
import textwrap
from dataclasses import dataclass
from typing import Protocol

from superpowers_runner.schema.nodes import TaskNode
from superpowers_runner.schema.primitives import PrimitiveType


@dataclass
class CheckResult:
    passed: bool
    evidence: str


class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# AST-based checks
# ---------------------------------------------------------------------------


def ast_no_any(source: str, node: TaskNode | None = None, **kwargs) -> CheckResult:
    """Walk AST for Any annotations, unannotated dict, or bare object.

    Flags line numbers where violations occur.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    violations: list[str] = []

    for ast_node in ast.walk(tree):
        annotation = _extract_annotation(ast_node)
        if annotation is None:
            continue

        annotation_str = _annotation_to_string(annotation)
        line = getattr(annotation, "lineno", getattr(ast_node, "lineno", "?"))

        if _is_any_type(annotation_str):
            violations.append(f"line {line}: {annotation_str}")

    if violations:
        detail = "; ".join(violations)
        return CheckResult(passed=False, evidence=f"Any/dict/object annotations found: {detail}")

    return CheckResult(passed=True, evidence="No Any annotations found")


def _extract_annotation(node: ast.AST) -> ast.AST | None:
    """Extract annotation AST node from various annotation-bearing nodes."""
    if isinstance(node, ast.AnnAssign) and node.annotation is not None:
        return node.annotation
    if isinstance(node, ast.arg) and node.annotation is not None:
        return node.annotation
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
        return node.returns
    return None


def _annotation_to_string(node: ast.AST) -> str:
    """Convert an annotation AST node to its string representation."""
    return ast.unparse(node)


def _is_any_type(annotation_str: str) -> bool:
    """Check if an annotation string represents Any, bare dict, or bare object."""
    stripped = annotation_str.strip()
    # Exact matches for bare types
    if stripped in ("Any", "any", "dict", "object"):
        return True
    # typing.Any
    if stripped in ("typing.Any",):
        return True
    return False


def ast_no_io(
    source: str,
    node: TaskNode | None = None,
    forbidden_modules: list[str] | None = None,
    forbidden_builtins: list[str] | None = None,
    **kwargs,
) -> CheckResult:
    """Walk AST for I/O module imports and I/O function calls.

    Forbidden by default: os, sys, requests, httpx, aiohttp, and built-in open.
    """
    if forbidden_modules is None:
        forbidden_modules = ["os", "sys", "requests", "httpx", "aiohttp"]
    if forbidden_builtins is None:
        forbidden_builtins = ["open"]

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    violations: list[str] = []

    for ast_node in ast.walk(tree):
        # Check imports
        if isinstance(ast_node, ast.Import):
            for alias in ast_node.names:
                root_module = alias.name.split(".")[0]
                if root_module in forbidden_modules:
                    violations.append(
                        f"line {ast_node.lineno}: import {alias.name}"
                    )

        elif isinstance(ast_node, ast.ImportFrom):
            if ast_node.module:
                root_module = ast_node.module.split(".")[0]
                if root_module in forbidden_modules:
                    violations.append(
                        f"line {ast_node.lineno}: from {ast_node.module} import ..."
                    )

        # Check calls to forbidden builtins
        elif isinstance(ast_node, ast.Call):
            func_name = _call_name(ast_node)
            if func_name in forbidden_builtins:
                violations.append(
                    f"line {ast_node.lineno}: {func_name}() call"
                )
            # Also catch module-qualified calls like os.path.join
            if func_name and any(
                func_name.startswith(m + ".") for m in forbidden_modules
            ):
                violations.append(
                    f"line {ast_node.lineno}: {func_name}() call"
                )

    if violations:
        detail = "; ".join(violations)
        return CheckResult(passed=False, evidence=f"I/O violations found: {detail}")

    return CheckResult(passed=True, evidence="No I/O calls found")


def _call_name(call_node: ast.Call) -> str | None:
    """Extract the name string from a Call node's func."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        current = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    return None


def ast_has_exception_handling(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify at least one try/except block is present."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    for ast_node in ast.walk(tree):
        if isinstance(ast_node, ast.Try):
            if ast_node.handlers:
                return CheckResult(
                    passed=True,
                    evidence="Exception handling found",
                )

    return CheckResult(passed=False, evidence="No try/except blocks found")


def ast_no_mutations(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify no write operations: attribute assignment on external objects,
    database write calls, file writes.

    Used on query nodes to enforce read-only behavior.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    violations: list[str] = []

    # Known write method names (database, file, collection mutation)
    write_methods = frozenset({
        "save", "create", "update", "delete", "remove", "insert",
        "put", "write", "append", "extend", "pop", "clear",
        "add", "discard", "commit", "execute", "executemany",
        "bulk_create", "bulk_update", "set", "setdefault",
    })

    for ast_node in ast.walk(tree):
        # Attribute assignment: obj.attr = value
        if isinstance(ast_node, ast.Assign):
            for target in ast_node.targets:
                if isinstance(target, ast.Attribute):
                    line = target.lineno
                    attr_str = ast.unparse(target)
                    violations.append(
                        f"line {line}: attribute assignment {attr_str}"
                    )

        # Augmented assignment: obj.attr += value
        if isinstance(ast_node, ast.AugAssign):
            if isinstance(ast_node.target, ast.Attribute):
                line = ast_node.target.lineno
                attr_str = ast.unparse(ast_node.target)
                violations.append(
                    f"line {line}: augmented assignment {attr_str}"
                )

        # Calls to write methods
        if isinstance(ast_node, ast.Call):
            func = ast_node.func
            if isinstance(func, ast.Attribute) and func.attr in write_methods:
                line = ast_node.lineno
                violations.append(
                    f"line {line}: write method call .{func.attr}()"
                )

    if violations:
        detail = "; ".join(violations)
        return CheckResult(passed=False, evidence=f"Write operations found: {detail}")

    return CheckResult(passed=True, evidence="No write operations found")


def ast_no_shared_mutable_state(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Look for module-level mutable variables used across test functions.

    Used on unit_test nodes to enforce test isolation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    # Find module-level assignments to mutable types
    module_level_mutables: dict[str, int] = {}  # name -> line
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if _is_mutable_value(stmt.value):
                        module_level_mutables[target.id] = stmt.lineno
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.target and isinstance(stmt.target, ast.Name) and stmt.value:
                if _is_mutable_value(stmt.value):
                    module_level_mutables[stmt.target.id] = stmt.lineno

    if not module_level_mutables:
        return CheckResult(
            passed=True,
            evidence="No module-level mutable state found",
        )

    # Find test functions that reference these module-level mutables
    test_functions = _find_test_functions(tree)
    usage_map: dict[str, list[str]] = {}  # mutable_name -> [test_func_names]

    for func_node in test_functions:
        for name_node in ast.walk(func_node):
            if isinstance(name_node, ast.Name) and name_node.id in module_level_mutables:
                usage_map.setdefault(name_node.id, []).append(func_node.name)

    # Flag mutables used in 2+ test functions
    violations: list[str] = []
    for var_name, test_funcs in usage_map.items():
        unique_funcs = list(dict.fromkeys(test_funcs))
        if len(unique_funcs) >= 2:
            line = module_level_mutables[var_name]
            violations.append(
                f"line {line}: '{var_name}' used in {len(unique_funcs)} test functions: "
                f"{', '.join(unique_funcs)}"
            )

    if violations:
        detail = "; ".join(violations)
        return CheckResult(
            passed=False,
            evidence=f"Shared mutable state found: {detail}",
        )

    return CheckResult(passed=True, evidence="No shared mutable state across tests")


def _is_mutable_value(node: ast.AST) -> bool:
    """Check if an AST value node represents a mutable type (list, dict, set)."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in ("list", "dict", "set", "defaultdict", "OrderedDict", "deque"):
            return True
    return False


def _find_test_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Find all test functions (top-level or in test classes)."""
    results: list[ast.FunctionDef] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name.startswith("test_") or stmt.name.startswith("test"):
                results.append(stmt)
        elif isinstance(stmt, ast.ClassDef):
            for class_stmt in stmt.body:
                if isinstance(class_stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if class_stmt.name.startswith("test_") or class_stmt.name.startswith("test"):
                        results.append(class_stmt)
    return results


# ---------------------------------------------------------------------------
# Test runner checks
# ---------------------------------------------------------------------------


def run_tests(
    source: str,
    node: TaskNode | None = None,
    test_file_path: str | None = None,
    **kwargs,
) -> CheckResult:
    """Execute the test suite for this node. Returns pass/fail + output.

    If test_file_path is provided, runs pytest on that file.
    Otherwise returns a failure indicating no test file was specified.
    """
    if test_file_path is None:
        return CheckResult(passed=False, evidence="No test file path provided")

    try:
        result = subprocess.run(
            ["python", "-m", "pytest", test_file_path, "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        # Truncate very long output
        if len(output) > 4000:
            output = output[:4000] + "\n... (truncated)"

        if result.returncode == 0:
            return CheckResult(passed=True, evidence=f"Tests passed:\n{output}")
        else:
            return CheckResult(passed=False, evidence=f"Tests failed:\n{output}")
    except subprocess.TimeoutExpired:
        return CheckResult(passed=False, evidence="Test execution timed out (60s)")
    except FileNotFoundError:
        return CheckResult(passed=False, evidence="pytest not found — cannot run tests")


def test_count_minimum(
    source: str,
    node: TaskNode | None = None,
    minimum: int = 3,
    **kwargs,
) -> CheckResult:
    """Count test functions/methods in source. Compare to minimum."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    test_funcs = _find_test_functions(tree)
    count = len(test_funcs)

    if count >= minimum:
        return CheckResult(
            passed=True,
            evidence=f"Found {count} test functions (minimum: {minimum})",
        )
    else:
        names = [f.name for f in test_funcs]
        return CheckResult(
            passed=False,
            evidence=f"Found {count} test functions (minimum: {minimum}): {', '.join(names) or 'none'}",
        )


def test_covers_exceptions(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify at least one test explicitly tests an exception path.

    Looks for: pytest.raises, assertRaises, try/except in test body.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    test_funcs = _find_test_functions(tree)
    if not test_funcs:
        return CheckResult(passed=False, evidence="No test functions found")

    for func in test_funcs:
        for child in ast.walk(func):
            # pytest.raises(...) or self.assertRaises(...)
            if isinstance(child, ast.Call):
                name = _call_name(child)
                if name and ("raises" in name.lower() or "assertraises" in name.lower()):
                    return CheckResult(
                        passed=True,
                        evidence=f"Exception test found in {func.name}",
                    )

            # with pytest.raises(...)
            if isinstance(child, ast.With):
                for item in child.items:
                    if isinstance(item.context_expr, ast.Call):
                        name = _call_name(item.context_expr)
                        if name and "raises" in name.lower():
                            return CheckResult(
                                passed=True,
                                evidence=f"Exception test found in {func.name} (with block)",
                            )

            # try/except in test body
            if isinstance(child, ast.Try) and child.handlers:
                return CheckResult(
                    passed=True,
                    evidence=f"Exception handling test found in {func.name} (try/except)",
                )

    return CheckResult(
        passed=False,
        evidence="No test function explicitly tests an exception path",
    )


def test_covers_partial_failure(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify integration tests include a failure scenario.

    Looks for: test function names containing 'fail', 'error', 'partial',
    'rollback'; or pytest.raises / assertRaises usage.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    test_funcs = _find_test_functions(tree)
    if not test_funcs:
        return CheckResult(passed=False, evidence="No test functions found")

    failure_keywords = {"fail", "error", "partial", "rollback", "exception", "invalid"}

    for func in test_funcs:
        name_lower = func.name.lower()
        if any(kw in name_lower for kw in failure_keywords):
            return CheckResult(
                passed=True,
                evidence=f"Failure scenario test found: {func.name}",
            )

    # Also check for exception testing patterns (same as test_covers_exceptions)
    result = test_covers_exceptions(source, node)
    if result.passed:
        return CheckResult(
            passed=True,
            evidence=f"Partial failure coverage found via exception testing: {result.evidence}",
        )

    return CheckResult(
        passed=False,
        evidence="No failure scenario test found in integration tests",
    )


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def file_contains_tests(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Check that test content exists and is non-empty.

    Lower bar than run_tests — just checks structure.
    """
    if not source or not source.strip():
        return CheckResult(passed=False, evidence="Test source is empty")

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    test_funcs = _find_test_functions(tree)
    if test_funcs:
        names = [f.name for f in test_funcs]
        return CheckResult(
            passed=True,
            evidence=f"Found {len(test_funcs)} test functions: {', '.join(names)}",
        )

    return CheckResult(
        passed=False,
        evidence="No test functions found (expected functions starting with 'test_')",
    )


def has_docstring(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify the primary class or function has a non-empty docstring."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    # Find the first class or function definition
    for stmt in tree.body:
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(stmt)
            if docstring and docstring.strip():
                return CheckResult(
                    passed=True,
                    evidence=f"Docstring found on {stmt.name}: {docstring[:80]}...",
                )
            else:
                return CheckResult(
                    passed=False,
                    evidence=f"No docstring on {stmt.name}",
                )

    return CheckResult(
        passed=False,
        evidence="No class or function definition found in source",
    )


def has_documented_exceptions(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify raised exceptions are documented in docstring or via annotation.

    Looks for: Raises section in docstring matching raise statements in code.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return CheckResult(passed=False, evidence=f"SyntaxError: {e}")

    # Find all raise statements
    raised_exceptions: set[str] = set()
    for ast_node in ast.walk(tree):
        if isinstance(ast_node, ast.Raise) and ast_node.exc is not None:
            if isinstance(ast_node.exc, ast.Call):
                name = _call_name(ast_node.exc)
                if name:
                    raised_exceptions.add(name)
            elif isinstance(ast_node.exc, ast.Name):
                raised_exceptions.add(ast_node.exc.id)

    if not raised_exceptions:
        return CheckResult(
            passed=True,
            evidence="No exceptions raised — nothing to document",
        )

    # Check for documentation of raised exceptions
    # Look in docstrings of classes and functions only
    documented: set[str] = set()

    # Collect all docstrings
    docstrings: list[str] = []
    for stmt in ast.walk(tree):
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(stmt) or ""
            if docstring:
                docstrings.append(docstring)

    # Collect all comments (lines starting with #)
    comments: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped)

    # Check docstrings for exception names
    for docstring in docstrings:
        for exc_name in raised_exceptions:
            if exc_name in docstring:
                documented.add(exc_name)

    # Check comments for exception names with "raises" context
    comment_text = "\n".join(comments)
    for exc_name in raised_exceptions:
        if exc_name in comment_text:
            documented.add(exc_name)

    undocumented = raised_exceptions - documented
    if undocumented:
        return CheckResult(
            passed=False,
            evidence=f"Undocumented exceptions: {', '.join(sorted(undocumented))}",
        )

    return CheckResult(
        passed=True,
        evidence=f"All raised exceptions documented: {', '.join(sorted(raised_exceptions))}",
    )


def has_rollback_documentation(
    source: str, node: TaskNode | None = None, **kwargs
) -> CheckResult:
    """Verify rollback behavior is described in comment, docstring, or implementation.

    Used on orchestration nodes.
    """
    rollback_indicators = [
        "rollback", "compensat", "undo", "revert", "cleanup",
        "compensating transaction", "saga",
    ]

    source_lower = source.lower()
    found: list[str] = []
    for indicator in rollback_indicators:
        if indicator in source_lower:
            found.append(indicator)

    if found:
        return CheckResult(
            passed=True,
            evidence=f"Rollback documentation found (keywords: {', '.join(found)})",
        )

    return CheckResult(
        passed=False,
        evidence="No rollback documentation found (expected: rollback, compensate, undo, revert, or cleanup)",
    )


def children_have_types(
    source: str,
    node: TaskNode | None = None,
    **kwargs,
) -> CheckResult:
    """Verify all children of a composition node have valid primitive_type.

    This check examines the TaskNode, not source code.
    If any child is untyped, this is an abort — the tree was malformed.
    """
    if node is None:
        return CheckResult(passed=False, evidence="No node provided for children type check")

    if not node.sub_nodes:
        # Composition node with no children is a problem
        if node.is_composition:
            return CheckResult(
                passed=False,
                evidence="Composition node has no children",
            )
        return CheckResult(passed=True, evidence="Not a composition node — check not applicable")

    valid_types = set(PrimitiveType)
    invalid_children: list[str] = []

    for child in node.sub_nodes:
        if child.primitive_type not in valid_types:
            invalid_children.append(f"{child.name}: invalid type '{child.primitive_type}'")

    if invalid_children:
        detail = "; ".join(invalid_children)
        return CheckResult(
            passed=False,
            evidence=f"Children with invalid types: {detail}",
        )

    child_summary = ", ".join(
        f"{c.name} ({c.primitive_type.value})" for c in node.sub_nodes
    )
    return CheckResult(
        passed=True,
        evidence=f"All children have valid types: {child_summary}",
    )


# ---------------------------------------------------------------------------
# LLM judge check
# ---------------------------------------------------------------------------


def llm_judge(
    source: str,
    node: TaskNode | None = None,
    llm_client: LLMClient | None = None,
    question: str = "",
    expected_answer: str = "",
    **kwargs,
) -> CheckResult:
    """Constrained LLM call for semantic checks.

    The judge call is constrained: system prompt specifies the exact question
    and valid answers. The response is parsed for the answer token. Any deviation
    from expected answer format causes a retry (up to 3), not a gate failure.
    """
    if llm_client is None:
        return CheckResult(
            passed=False,
            evidence="No LLM client provided — cannot run judge check",
        )

    if not question or not expected_answer:
        return CheckResult(
            passed=False,
            evidence="No question or expected_answer provided for judge check",
        )

    system_prompt = (
        "You are a code review judge. Answer the following question about the code "
        "with ONLY one of the valid answer tokens. No explanation."
    )

    prompt = (
        f"Code:\n```\n{source}\n```\n\n"
        f"Question: {question}\n\n"
        f"Valid answers: {expected_answer}, or the opposite.\n"
        f"Answer:"
    )

    max_retries = 3
    for attempt in range(max_retries):
        response = llm_client.call(prompt, max_tokens=50, system=system_prompt)
        answer = response.strip().lower()

        if expected_answer.lower() in answer:
            return CheckResult(
                passed=True,
                evidence=f"LLM judge answered: {response.strip()} (expected: {expected_answer})",
            )
        elif answer and answer not in ("", " "):
            # Got a real answer but it doesn't match expected
            return CheckResult(
                passed=False,
                evidence=f"LLM judge answered: {response.strip()} (expected: {expected_answer})",
            )
        # Empty or whitespace — retry

    return CheckResult(
        passed=False,
        evidence=f"LLM judge failed to produce valid answer after {max_retries} attempts",
    )


# ---------------------------------------------------------------------------
# Check registry — maps check_type strings to implementations
# ---------------------------------------------------------------------------


CHECKS: dict[str, callable] = {
    "ast_no_any": ast_no_any,
    "ast_no_io": ast_no_io,
    "ast_has_exception_handling": ast_has_exception_handling,
    "ast_no_mutations": ast_no_mutations,
    "ast_no_shared_mutable_state": ast_no_shared_mutable_state,
    "run_tests": run_tests,
    "test_count_minimum": test_count_minimum,
    "test_covers_exceptions": test_covers_exceptions,
    "test_covers_partial_failure": test_covers_partial_failure,
    "file_contains_tests": file_contains_tests,
    "has_docstring": has_docstring,
    "has_documented_exceptions": has_documented_exceptions,
    "has_rollback_documentation": has_rollback_documentation,
    "children_have_types": children_have_types,
    "llm_judge": llm_judge,
}


def run_check(
    check_type: str,
    source: str,
    node: TaskNode | None = None,
    **kwargs,
) -> CheckResult:
    """Dispatch to the appropriate check implementation by check_type string."""
    if check_type not in CHECKS:
        return CheckResult(
            passed=False,
            evidence=f"Unknown check_type: {check_type}",
        )
    return CHECKS[check_type](source, node=node, **kwargs)
