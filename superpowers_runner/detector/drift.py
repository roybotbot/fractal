"""DriftDetector — five check methods, one per drift type.

Receives LLM output and node context. Returns lists of DriftSignal.
Never modifies node state, never takes action.

Depends on: schema layer only.
"""

from __future__ import annotations

import ast
import re
from typing import Protocol
from uuid import uuid4

from superpowers_runner.schema.nodes import GateResult, StepRecord, StepStatus, TaskNode
from superpowers_runner.schema.signals import DriftSignal, DriftType, Severity


class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


def _sig_id() -> str:
    return uuid4().hex[:8]


# Completion language patterns
_COMPLETION_PATTERNS = [
    re.compile(r"\b(implementation is complete|all tests pass|fully implemented)\b", re.I),
    re.compile(r"\b(task is done|everything works|implementation complete)\b", re.I),
    re.compile(r"\b(all requirements met|all gates pass|tests are passing)\b", re.I),
]


class DriftDetector:
    """Detects the five drift types in LLM step output."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client

    def check_scope(
        self,
        node: TaskNode,
        output: str,
    ) -> list[DriftSignal]:
        """Type 1: scope drift — symbols not in the node spec.

        Extracts class/function definitions from output and checks
        against node input/output schema field names.
        """
        signals: list[DriftSignal] = []

        try:
            tree = ast.parse(output)
        except SyntaxError:
            return signals

        # Collect names from node spec
        spec_names: set[str] = set()
        for field in node.input_schema.fields:
            spec_names.add(field.name)
            spec_names.add(field.type_annotation)
        for field in node.output_schema.fields:
            spec_names.add(field.name)
            spec_names.add(field.type_annotation)
        spec_names.add(node.name)
        # Add common Python builtins/types to avoid false positives
        spec_names.update({
            "str", "int", "float", "bool", "None", "list", "dict", "set",
            "tuple", "Optional", "Any", "self", "cls", "__init__",
        })

        # Find class definitions in output
        for ast_node in ast.walk(tree):
            if isinstance(ast_node, ast.ClassDef):
                if ast_node.name not in spec_names:
                    signals.append(DriftSignal(
                        id=_sig_id(),
                        drift_type=DriftType.SCOPE,
                        severity=Severity.BLOCK,
                        node_id=node.id,
                        step_name="",
                        evidence=(
                            f"Output introduced '{ast_node.name}' class "
                            f"which is not in the node spec."
                        ),
                        output_excerpt=f"class {ast_node.name}:",
                        correction_template=(
                            f"Remove '{ast_node.name}'. Implement only "
                            f"what the node spec defines."
                        ),
                    ))

        return signals

    def check_phase(
        self,
        step: StepRecord,
        output: str,
        subsequent_steps: list[StepRecord] | None = None,
    ) -> list[DriftSignal]:
        """Type 2: phase drift — forbidden artifacts in step output.

        Checks output against the step's forbidden_artifacts list.
        """
        signals: list[DriftSignal] = []
        forbidden = step.template.forbidden_artifacts

        if not forbidden:
            return signals

        for artifact in forbidden:
            if self._contains_artifact(output, artifact):
                signals.append(DriftSignal(
                    id=_sig_id(),
                    drift_type=DriftType.PHASE,
                    severity=Severity.BLOCK,
                    node_id="",
                    step_name=step.template.name,
                    evidence=(
                        f"{artifact} found during '{step.template.name}' step. "
                        f"This artifact belongs in a later step."
                    ),
                    output_excerpt=output[:200],
                    correction_template=(
                        f"Remove all {artifact}. This step produces only "
                        f"{', '.join(step.template.expected_artifacts)}."
                    ),
                ))

        return signals

    def _contains_artifact(self, output: str, artifact: str) -> bool:
        """Check if output contains a forbidden artifact type."""
        if artifact == "implementation_code":
            return self._looks_like_implementation(output)
        if artifact == "test_code":
            return self._looks_like_test_code(output)
        if artifact in ("code", "class_definition"):
            return self._looks_like_code(output)
        return False

    def _looks_like_implementation(self, output: str) -> bool:
        """Heuristic: does this output contain actual implementation code?"""
        try:
            tree = ast.parse(output)
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A function with a body beyond just 'pass' or a docstring
                body = [
                    s for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, (ast.Constant, ast.Str)))
                    and not isinstance(s, ast.Pass)
                ]
                if body and not node.name.startswith("test_"):
                    return True
        return False

    def _looks_like_test_code(self, output: str) -> bool:
        """Heuristic: does this output contain test code?"""
        try:
            tree = ast.parse(output)
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    return True
        return False

    def _looks_like_code(self, output: str) -> bool:
        """Heuristic: does this contain Python code definitions?"""
        try:
            tree = ast.parse(output)
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                return True
        return False

    def check_instruction_adherence(
        self,
        step: StepRecord,
        output: str,
    ) -> list[DriftSignal]:
        """Type 3: instruction drift — expected artifacts missing.

        If an LLM client is available, uses an LLM judge call.
        Otherwise falls back to keyword matching.
        """
        signals: list[DriftSignal] = []
        expected = step.template.expected_artifacts

        if not expected:
            return signals

        if self._llm_client:
            return self._judge_instruction_adherence(step, output)

        # Fallback: simple keyword check
        for artifact in expected:
            # Convert artifact name to likely keywords
            keywords = artifact.lower().replace("_", " ").split()
            if not any(kw in output.lower() for kw in keywords):
                signals.append(DriftSignal(
                    id=_sig_id(),
                    drift_type=DriftType.INSTRUCTION,
                    severity=Severity.BLOCK,
                    node_id="",
                    step_name=step.template.name,
                    evidence=(
                        f"Step '{step.template.name}' required '{artifact}' "
                        f"but the output does not appear to address it."
                    ),
                    output_excerpt=output[:200],
                    correction_template=(
                        f"Address the following artifact explicitly: {artifact}"
                    ),
                ))

        return signals

    def _judge_instruction_adherence(
        self, step: StepRecord, output: str
    ) -> list[DriftSignal]:
        """Use LLM judge to check instruction adherence."""
        signals: list[DriftSignal] = []
        expected = step.template.expected_artifacts

        artifact_list = ", ".join(expected)
        prompt = (
            f"The required step was: {step.template.prompt_template}\n\n"
            f"The output was:\n{output[:2000]}\n\n"
            f"Did the output explicitly address each required artifact?\n"
            f"Answer per artifact:\n"
        )
        for artifact in expected:
            prompt += f"  {artifact}: addressed / not addressed / partially addressed\n"

        response = self._llm_client.call(prompt, max_tokens=200)

        for artifact in expected:
            if "not addressed" in response.lower() and artifact in response.lower():
                signals.append(DriftSignal(
                    id=_sig_id(),
                    drift_type=DriftType.INSTRUCTION,
                    severity=Severity.BLOCK,
                    node_id="",
                    step_name=step.template.name,
                    evidence=(
                        f"Step '{step.template.name}' required '{artifact}' "
                        f"which was not addressed in the output."
                    ),
                    output_excerpt=output[:200],
                    correction_template=(
                        f"Address the following artifact explicitly: {artifact}"
                    ),
                ))

        return signals

    def check_schema_consistency(
        self,
        global_registry: dict[str, str],
        output: str,
    ) -> list[DriftSignal]:
        """Type 4: schema drift — type mismatches against global registry.

        Extracts type annotations from output and compares against
        established schema entries.
        """
        signals: list[DriftSignal] = []

        if not global_registry:
            return signals

        try:
            tree = ast.parse(output)
        except SyntaxError:
            return signals

        # Extract all type annotations from output
        annotations: dict[str, str] = {}  # field_name -> type_string
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and node.annotation:
                if isinstance(node.target, ast.Name):
                    annotations[node.target.id] = ast.unparse(node.annotation)

        # Compare against registry
        for entry_name, entry_schema in global_registry.items():
            # Parse entry_schema for field definitions (simple format: "field: type")
            for field_def in entry_schema.split(","):
                field_def = field_def.strip()
                if ":" in field_def:
                    field_name, field_type = field_def.split(":", 1)
                    field_name = field_name.strip()
                    field_type = field_type.strip()

                    if field_name in annotations:
                        output_type = annotations[field_name]
                        if output_type != field_type:
                            signals.append(DriftSignal(
                                id=_sig_id(),
                                drift_type=DriftType.SCHEMA,
                                severity=Severity.BLOCK,
                                node_id="",
                                step_name="",
                                evidence=(
                                    f"Field '{field_name}' typed as '{output_type}' "
                                    f"but global registry defines {entry_name}.{field_name}: {field_type}"
                                ),
                                output_excerpt=f"{field_name}: {output_type}",
                                correction_template=(
                                    f"Use '{field_type}' for {field_name}, "
                                    f"consistent with the global schema."
                                ),
                            ))

        return signals

    def check_completion_honesty(
        self,
        node: TaskNode,
        output: str,
        gate_results: list[GateResult],
    ) -> list[DriftSignal]:
        """Type 5: completion drift — false completion claims.

        Binary check: if output claims completion AND any gate is failing,
        this is a completion drift signal.
        """
        signals: list[DriftSignal] = []

        if not gate_results:
            return signals

        failing_gates = [g for g in gate_results if not g.passed]
        if not failing_gates:
            return signals

        # Check for completion language
        has_completion_claim = any(
            pattern.search(output) for pattern in _COMPLETION_PATTERNS
        )

        if has_completion_claim:
            gate_list = "\n  ".join(
                f"- {g.gate.name}: {g.evidence}" for g in failing_gates
            )
            signals.append(DriftSignal(
                id=_sig_id(),
                drift_type=DriftType.COMPLETION,
                severity=Severity.BLOCK,
                node_id=node.id,
                step_name="",
                evidence=(
                    f"Output claims completion but gates are failing:\n  {gate_list}"
                ),
                output_excerpt=output[:200],
                correction_template=(
                    f"Do not declare completion. Address each failing gate:\n  {gate_list}"
                ),
            ))

        return signals

    def check_all(
        self,
        node: TaskNode,
        step: StepRecord,
        output: str,
        gate_results: list[GateResult] | None = None,
    ) -> list[DriftSignal]:
        """Run all applicable checks for the given step."""
        signals: list[DriftSignal] = []

        signals.extend(self.check_scope(node, output))
        signals.extend(self.check_phase(step, output))
        # instruction adherence is expensive (LLM judge) — skip if no expected artifacts
        if step.template.expected_artifacts:
            signals.extend(self.check_instruction_adherence(step, output))
        # schema consistency needs a registry — caller passes it if available
        # (handled at runner level)
        if gate_results is not None:
            signals.extend(self.check_completion_honesty(node, output, gate_results))

        # Fill in node_id for signals that don't have it
        for s in signals:
            if not s.node_id:
                s.node_id = node.id

        return signals
