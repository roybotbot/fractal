"""GateRunner — executes gate checks against completed nodes.

Dispatches to checks.py implementations by check_type string, collects
results, determines pass/fail per gate. Gates that already passed on a
prior run are skipped (session resume).

Depends on: schema layer, detector/checks.py
"""

from __future__ import annotations

from datetime import datetime, UTC

from superpowers_runner.detector.checks import (
    CheckResult,
    LLMClient,
    run_check,
)
from superpowers_runner.schema.nodes import GateResult, TaskNode, StepStatus


class GateRunner:
    """Executes gate checks for a completed node.

    The gate runner is called after all steps in a leaf node are done.
    It runs each gate template's check against the node's output,
    collects results, and returns the list of GateResult objects.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        test_file_resolver: callable | None = None,
    ) -> None:
        """
        Args:
            llm_client: Required for llm_judge gate checks. If None,
                llm_judge checks will fail with "no client" evidence.
            test_file_resolver: Callable that takes a TaskNode and returns
                the path to its test file, or None. Used by run_tests checks.
        """
        self._llm_client = llm_client
        self._test_file_resolver = test_file_resolver or (lambda node: None)

    def run_all(self, node: TaskNode) -> list[GateResult]:
        """Run all gate checks for this node.

        Gates that already passed (checked_at is not None and passed is True)
        are skipped — this supports session resume without re-running
        expensive checks.

        Returns the updated list of GateResult objects (same objects as on
        the node, mutated in place).
        """
        for gate_result in node.gate_results:
            if gate_result.passed and gate_result.checked_at is not None:
                # Already passed on a prior run — skip
                continue

            check_result = self._run_single(node, gate_result)

            gate_result.passed = check_result.passed
            gate_result.evidence = check_result.evidence
            gate_result.checked_at = datetime.now(UTC)

        return node.gate_results

    def run_single(self, node: TaskNode, gate_result: GateResult) -> GateResult:
        """Run a single gate check and update the GateResult in place."""
        check_result = self._run_single(node, gate_result)
        gate_result.passed = check_result.passed
        gate_result.evidence = check_result.evidence
        gate_result.checked_at = datetime.now(UTC)
        return gate_result

    def _run_single(self, node: TaskNode, gate_result: GateResult) -> CheckResult:
        """Dispatch a single gate check to the checks module."""
        gate = gate_result.gate
        source = self._collect_source(node)

        # Build kwargs from gate parameters + extras
        kwargs: dict = dict(gate.parameters) if gate.parameters else {}

        # Inject dependencies that certain check types need
        if gate.check_type == "llm_judge":
            kwargs["llm_client"] = self._llm_client

        if gate.check_type == "run_tests":
            kwargs["test_file_path"] = self._test_file_resolver(node)

        return run_check(
            check_type=gate.check_type,
            source=source,
            node=node,
            **kwargs,
        )

    def _collect_source(self, node: TaskNode) -> str:
        """Collect the source code to check from the node's completed steps.

        Concatenates all completed step outputs that contain code.
        The last implementation step's output is the primary source.
        """
        outputs: list[str] = []
        for step in node.steps:
            if step.status == StepStatus.COMPLETE and step.output:
                outputs.append(step.output)
        return "\n\n".join(outputs)

    @property
    def llm_client(self) -> LLMClient | None:
        return self._llm_client

    @llm_client.setter
    def llm_client(self, client: LLMClient | None) -> None:
        self._llm_client = client
