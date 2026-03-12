from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GateResult:
    """Minimal gate result structure for the first v2 slice."""

    passed: bool
    evidence: str


def check_no_io(source: str) -> GateResult:
    """Reject obviously impure transformation code.

    This first pass is intentionally narrow: it looks for a few common imports
    and the built-in `open()` call. That is enough to prove the retry loop.
    """
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"requests", "httpx", "os", "sys"}:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in {"requests", "httpx", "os", "sys"}:
                violations.append(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            violations.append("open")

    if violations:
        return GateResult(False, ", ".join(violations))
    return GateResult(True, "No I/O detected")


def run_pytest(test_file: str | Path) -> GateResult:
    """Execute pytest against a single generated test file."""
    result = subprocess.run(
        ["python", "-m", "pytest", str(test_file), "-q"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = (result.stdout + result.stderr).strip()
    return GateResult(result.returncode == 0, output)
