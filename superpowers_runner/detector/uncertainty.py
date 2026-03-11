"""UncertaintyDetector — six check methods with confidence scoring.

Each method returns a list of UncertaintySignal. The detector identifies
situations where something looks off but confidence isn't high enough to
block. These route to human review.

Depends on: schema layer only.
"""

from __future__ import annotations

import ast
import re
from uuid import uuid4

from superpowers_runner.schema.nodes import StepRecord, StepStatus, TaskNode
from superpowers_runner.schema.signals import (
    DEFAULT_TIMEOUT_RESOLUTION,
    Resolution,
    UncertaintySignal,
    UncertaintyType,
)


def _sig_id() -> str:
    return uuid4().hex[:8]


class UncertaintyDetector:
    """Detects the six uncertainty types in LLM step output."""

    def __init__(self) -> None:
        self._token_counts: dict[str, list[int]] = {}
        # step_name -> list of token counts for rolling average

    def check_ambiguous_scope(
        self,
        node: TaskNode,
        output: str,
    ) -> list[UncertaintySignal]:
        """New symbol appeared that might be a legitimate local helper."""
        signals: list[UncertaintySignal] = []

        try:
            tree = ast.parse(output)
        except SyntaxError:
            return signals

        spec_names: set[str] = set()
        for field in node.input_schema.fields:
            spec_names.add(field.name)
            spec_names.add(field.type_annotation)
        for field in node.output_schema.fields:
            spec_names.add(field.name)
            spec_names.add(field.type_annotation)
        spec_names.add(node.name)
        # Builtins and common names
        spec_names.update({
            "str", "int", "float", "bool", "None", "list", "dict", "set",
            "tuple", "Optional", "Any", "self", "cls", "__init__",
            "Exception", "ValueError", "TypeError", "RuntimeError",
        })

        for ast_node in ast.walk(tree):
            if isinstance(ast_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast_node.name not in spec_names and not ast_node.name.startswith("_"):
                    # Could be a helper or could be scope drift
                    signals.append(UncertaintySignal(
                        id=_sig_id(),
                        uncertainty_type=UncertaintyType.AMBIGUOUS_SCOPE,
                        node_id=node.id,
                        step_name="",
                        confidence=0.4,
                        evidence=f"Output introduced '{ast_node.name}' which is not in the node spec.",
                        output_excerpt=f"def {ast_node.name}(...):",
                        question=(
                            f"Output introduced `{ast_node.name}` which is not in the node spec. "
                            f"Is this a legitimate local helper (A) or scope drift that should be removed (B)?"
                        ),
                        option_a="Legitimate local helper",
                        option_b="Scope drift — remove",
                        default_resolution=Resolution.PROCEED,
                    ))

        return signals

    def check_ambiguous_phase(
        self,
        step: StepRecord,
        output: str,
    ) -> list[UncertaintySignal]:
        """Code-like content in a planning step — might be pseudocode."""
        signals: list[UncertaintySignal] = []

        # Only relevant for steps that forbid code
        forbidden = step.template.forbidden_artifacts
        if not forbidden:
            return signals
        if "code" not in " ".join(forbidden).lower() and "implementation" not in " ".join(forbidden).lower():
            return signals

        # If the step is supposed to produce schemas/types, type definitions
        # in fences are the expected output — not a phase violation
        expected = " ".join(step.template.expected_artifacts).lower()
        if "schema" in expected or "type" in expected or "interface" in expected:
            return signals

        # Check for code block markers that contain actual code syntax
        code_block_pattern = re.compile(r"```(\w*)\n(.*?)\n```", re.DOTALL)
        matches = code_block_pattern.findall(output)

        # Patterns that indicate real code vs plain-text/pseudocode
        _CODE_INDICATORS = re.compile(
            r"(?:"
            r"(?:def|class|function|const|let|var|import|from|return)\s"  # keywords
            r"|[{};]"                                  # braces/semicolons
            r"|\b\w+\(.*\)"                            # function calls
            r"|=>"                                      # arrow functions
            r"|\bif\s*\(.*\)\s*[:{]"                   # if-conditions
            r")"
        )

        for lang, content in matches:
            # Skip if the language tag is explicitly non-code
            if lang.lower() in ("text", "txt", "markdown", "md", ""):
                # No language tag or plain text — only flag if content looks like code
                if not _CODE_INDICATORS.search(content):
                    continue

            # Skip very short blocks (single-line labels, schemas, etc.)
            content_lines = [l for l in content.strip().splitlines() if l.strip()]
            if len(content_lines) < 3 and not _CODE_INDICATORS.search(content):
                continue

            block_preview = content[:200].strip()
            signals.append(UncertaintySignal(
                id=_sig_id(),
                uncertainty_type=UncertaintyType.AMBIGUOUS_PHASE,
                node_id="",
                step_name=step.template.name,
                confidence=0.50,
                evidence=f"Code block with apparent implementation found during '{step.template.name}' planning step.",
                output_excerpt=block_preview,
                question=(
                    f"This looks like real code in a planning step (`{step.template.name}`). "
                    f"Is it pseudocode for illustration (A) or premature implementation (B)?"
                ),
                option_a="Pseudocode / illustration — proceed",
                option_b="Premature implementation — retry step",
                default_resolution=Resolution.PROCEED,
            ))

        return signals

    def check_partial_adherence(
        self,
        step: StepRecord,
        output: str,
    ) -> list[UncertaintySignal]:
        """Output partially addresses the step's requirements."""
        signals: list[UncertaintySignal] = []
        expected = step.template.expected_artifacts

        if not expected:
            return signals

        for artifact in expected:
            keywords = artifact.lower().replace("_", " ").split()
            matched = sum(1 for kw in keywords if kw in output.lower())
            total = len(keywords)

            if total > 0 and 0 < matched < total:
                confidence = 1.0 - (matched / total)
                signals.append(UncertaintySignal(
                    id=_sig_id(),
                    uncertainty_type=UncertaintyType.PARTIAL_ADHERENCE,
                    node_id="",
                    step_name=step.template.name,
                    confidence=min(confidence, 0.7),
                    evidence=(
                        f"Step '{step.template.name}' required '{artifact}'. "
                        f"Output partially addressed it ({matched}/{total} keywords)."
                    ),
                    output_excerpt=output[:200],
                    question=(
                        f"Step `{step.template.name}` required `{artifact}`. "
                        f"The output partially addressed this. Is the coverage sufficient (A) "
                        f"or should the step be retried (B)?"
                    ),
                    option_a="Coverage sufficient — proceed",
                    option_b="Retry with more explicit requirements",
                    default_resolution=Resolution.RETRY,
                ))

        return signals

    def check_schema_near_miss(
        self,
        output: str,
        global_registry: dict[str, str] | None = None,
    ) -> list[UncertaintySignal]:
        """Type in output structurally similar to established type but different name."""
        signals: list[UncertaintySignal] = []

        if not global_registry:
            return signals

        try:
            tree = ast.parse(output)
        except SyntaxError:
            return signals

        # Find class definitions in output
        output_classes: dict[str, set[str]] = {}
        for ast_node in ast.walk(tree):
            if isinstance(ast_node, ast.ClassDef):
                fields = set()
                for child in ast.walk(ast_node):
                    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                        fields.add(child.target.id)
                if fields:
                    output_classes[ast_node.name] = fields

        # Compare against registry entries
        for class_name, class_fields in output_classes.items():
            for reg_name, reg_schema in global_registry.items():
                if class_name == reg_name:
                    continue  # Exact match — no near miss

                reg_fields = set()
                for field_def in reg_schema.split(","):
                    if ":" in field_def:
                        field_name = field_def.split(":")[0].strip()
                        reg_fields.add(field_name)

                if not reg_fields:
                    continue

                # Check structural similarity
                overlap = class_fields & reg_fields
                if len(overlap) >= len(reg_fields) * 0.7:
                    signals.append(UncertaintySignal(
                        id=_sig_id(),
                        uncertainty_type=UncertaintyType.SCHEMA_NEAR_MISS,
                        node_id="",
                        step_name="",
                        confidence=0.6,
                        evidence=(
                            f"Output uses '{class_name}' which is structurally "
                            f"similar to established type '{reg_name}'."
                        ),
                        output_excerpt=f"class {class_name}",
                        question=(
                            f"Output uses `{class_name}` which is structurally identical "
                            f"to established type `{reg_name}`. Is this an intentional "
                            f"rename (A) or should the established name be used (B)?"
                        ),
                        option_a="Intentional rename",
                        option_b="Use established name",
                        default_resolution=Resolution.RETRY,
                    ))

        return signals

    def check_token_velocity(
        self,
        step: StepRecord,
        output: str,
    ) -> list[UncertaintySignal]:
        """Step completed suspiciously fast compared to average."""
        signals: list[UncertaintySignal] = []
        step_name = step.template.name
        token_count = len(output) // 4  # rough estimate

        # Record this count
        if step_name not in self._token_counts:
            self._token_counts[step_name] = []
        self._token_counts[step_name].append(token_count)

        counts = self._token_counts[step_name]
        if len(counts) < 3:
            # Not enough data for comparison
            return signals

        # Rolling average of prior counts (exclude current)
        prior = counts[:-1]
        avg = sum(prior) / len(prior)

        if avg > 0 and token_count < avg * 0.4:
            signals.append(UncertaintySignal(
                id=_sig_id(),
                uncertainty_type=UncertaintyType.SUSPICIOUSLY_FAST,
                node_id="",
                step_name=step_name,
                confidence=0.38,
                evidence=(
                    f"Step '{step_name}' completed in ~{token_count} tokens "
                    f"(avg: ~{int(avg)} tokens)."
                ),
                output_excerpt=output[:200],
                question=(
                    f"Step `{step_name}` completed in {token_count} tokens "
                    f"(avg: {int(avg)}). Is this node genuinely simple (A) "
                    f"or did it skip required work (B)?"
                ),
                option_a="Genuinely simple",
                option_b="Skipped work — retry",
                default_resolution=Resolution.RETRY,
            ))

        return signals

    def check_self_contradiction(
        self,
        output: str,
    ) -> list[UncertaintySignal]:
        """Prose claims something is handled but code doesn't show it."""
        signals: list[UncertaintySignal] = []

        # Pattern: "error handling is implemented" or similar in prose
        # Each tuple: (claim regex, label, list of code indicators — ANY match = ok)
        claim_patterns = [
            (r"error handling is (?:implemented|included|present)", "error handling",
             ["try:", "except", "catch", ".catch(", "try {", "rescue"]),
            (r"validation is (?:implemented|included|handled)", "validation",
             ["if ", "throw ", "raise ", "assert"]),
            (r"(?:all )?edge cases (?:are |have been )?(?:handled|covered)", "edge case handling",
             ["if ", "throw ", "raise ", "assert", "expect("]),
            (r"tests? (?:are |have been )?(?:written|implemented|added)", "test implementation",
             ["def test_", "it(", "it (", "test(", "test (", "describe(", "describe (",
              "@Test", "#[test]", "func Test"]),
        ]

        for pattern, claim_name, code_indicators in claim_patterns:
            if re.search(pattern, output, re.IGNORECASE):
                # Check if the code part actually demonstrates it
                # Split output into prose and code sections
                code_sections = re.findall(r"```[\w]*\n(.*?)```", output, re.DOTALL)
                code_text = "\n".join(code_sections) if code_sections else ""

                # Also check non-fenced code (indented blocks after prose)
                if not code_text:
                    # Try to find function/class defs
                    try:
                        ast.parse(output)
                        code_text = output
                    except SyntaxError:
                        pass

                indicators_present = any(ind in code_text for ind in code_indicators)
                if code_text and not indicators_present:
                    signals.append(UncertaintySignal(
                        id=_sig_id(),
                        uncertainty_type=UncertaintyType.SELF_CONTRADICTION,
                        node_id="",
                        step_name="",
                        confidence=0.65,
                        evidence=(
                            f"Output states {claim_name} is present "
                            f"but code doesn't demonstrate it."
                        ),
                        output_excerpt=output[:200],
                        question=(
                            f"Output states `{claim_name}` is present but "
                            f"the code doesn't demonstrate it. Is the assertion "
                            f"accurate (A) or should the step be retried (B)?"
                        ),
                        option_a="Assertion is accurate",
                        option_b="Retry — code doesn't match claim",
                        default_resolution=Resolution.ESCALATE,
                    ))

        return signals

    def check_all(
        self,
        node: TaskNode,
        step: StepRecord,
        output: str,
    ) -> list[UncertaintySignal]:
        """Run all applicable uncertainty checks."""
        signals: list[UncertaintySignal] = []

        signals.extend(self.check_ambiguous_scope(node, output))
        signals.extend(self.check_ambiguous_phase(step, output))
        signals.extend(self.check_partial_adherence(step, output))
        signals.extend(self.check_token_velocity(step, output))
        signals.extend(self.check_self_contradiction(output))
        # schema_near_miss needs a registry — called separately by runner

        # Fill in node_id for signals that don't have it
        for s in signals:
            if not s.node_id:
                s.node_id = node.id

        return signals
