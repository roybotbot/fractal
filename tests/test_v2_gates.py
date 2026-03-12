from __future__ import annotations

from pathlib import Path

from superpowers_runner_v2.gates import check_no_io, run_pytest


def test_check_no_io_flags_requests_and_open_calls() -> None:
    source = """
import requests

def bad() -> str:
    with open('x.txt') as handle:
        return handle.read()
""".strip()

    result = check_no_io(source)

    assert result.passed is False
    assert "requests" in result.evidence
    assert "open" in result.evidence


def test_check_no_io_returns_failed_gate_on_syntax_error() -> None:
    result = check_no_io("def broken(:\n    pass")

    assert result.passed is False
    assert "SyntaxError" in result.evidence


def test_run_pytest_executes_real_test_file(tmp_path: Path) -> None:
    test_file = tmp_path / "test_example.py"
    test_file.write_text(
        """
def test_truth() -> None:
    assert 2 + 2 == 4
""".strip()
    )

    result = run_pytest(test_file)

    assert result.passed is True
    assert "1 passed" in result.evidence
