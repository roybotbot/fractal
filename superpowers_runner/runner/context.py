"""ContextBuilder — assembles LLM call inputs.

Builds the structured context block sent with every LLM call. Injects:
current node summary, completed step outputs, global schema registry,
current step prompt, and correction context (on retry).

Manages context window budget by summarizing older step outputs when
the total would exceed the token limit.

Depends on: schema layer only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from superpowers_runner.schema.nodes import (
    NodeSchema,
    SchemaField,
    StepRecord,
    StepStatus,
    TaskNode,
)


# Immutable system block — the three sentences that survive context dilution.
SYSTEM_BLOCK = (
    "You are operating inside a structured task execution system.\n"
    "Complete ONLY the current step. Do not proceed to subsequent steps.\n"
    "Do not implement work that belongs to a different node."
)

# Approximate chars-per-token ratio for budget estimation.
# Conservative: real ratio varies, but 4 chars/token is a safe floor.
_CHARS_PER_TOKEN = 4


@dataclass
class SchemaRegistry:
    """Tracks established data models and interfaces for global consistency.

    Every DATA_MODEL and INTERFACE node, once completed, gets registered here.
    All subsequent LLM calls receive this registry so they can't accidentally
    diverge from established types.
    """

    entries: dict[str, str] = field(default_factory=dict)
    # key: node name, value: schema summary string

    def register(self, name: str, summary: str) -> None:
        self.entries[name] = summary

    def to_string(self) -> str:
        if not self.entries:
            return "(no established schemas yet)"
        parts: list[str] = []
        for name, summary in self.entries.items():
            parts.append(f"  {name}:\n    {summary}")
        return "\n".join(parts)

    def token_estimate(self) -> int:
        return len(self.to_string()) // _CHARS_PER_TOKEN


class ContextBuilder:
    """Assembles the full context for each LLM call.

    The output is a single string with clearly delimited sections.
    The step prompt is always last — it's what the model reads
    immediately before generating its response.
    """

    def __init__(
        self,
        session_id: str = "",
        max_tokens: int = 100_000,
    ) -> None:
        self._session_id = session_id
        self._max_tokens = max_tokens

    def build(
        self,
        node: TaskNode,
        step: StepRecord,
        global_schema: SchemaRegistry | None = None,
        correction_context: str = "",
    ) -> str:
        """Build the full context string for an LLM call.

        Sections (in order):
        1. System block — immutable, always present
        2. Correction context — present on retry only
        3. Node context — identity, spec, progress
        4. Completed step outputs — for continuity
        5. Global schema registry — for type consistency
        6. Step prompt — always last
        """
        sections: list[str] = []

        # 1. System block
        sections.append(self._build_system_block(node))

        # 2. Correction context (retry only)
        if correction_context:
            sections.append(self._build_correction_block(correction_context))

        # 3. Node context
        sections.append(self._build_node_context(node, step))

        # 4. Completed step outputs
        completed_block = self._build_completed_steps(node, step, global_schema)
        if completed_block:
            sections.append(completed_block)

        # 5. Global schema registry
        if global_schema and global_schema.entries:
            sections.append(self._build_schema_registry(global_schema))

        # 6. Step prompt — always last
        sections.append(self._build_step_prompt(node, step))

        full = "\n\n".join(sections)

        # Budget check — if over limit, summarize completed steps
        if self._estimate_tokens(full) > self._max_tokens:
            full = self._apply_budget(
                node, step, global_schema, correction_context
            )

        return full

    def build_system_prompt(self) -> str:
        """Return just the system prompt (for LLM clients that take it separately)."""
        return SYSTEM_BLOCK

    # -------------------------------------------------------------------
    # Section builders
    # -------------------------------------------------------------------

    def _build_system_block(self, node: TaskNode) -> str:
        lines = [
            "=== SYSTEM CONTEXT ===",
            SYSTEM_BLOCK,
            "",
            f"Session: {self._session_id}",
            f"Node: {node.name} [{node.id}]",
            f"Type: {node.primitive_type.value}",
        ]
        return "\n".join(lines)

    def _build_correction_block(self, correction_context: str) -> str:
        return (
            "=== CORRECTION CONTEXT ===\n"
            f"{correction_context}\n"
            "─────────────────────────────────────────"
        )

    def _build_node_context(self, node: TaskNode, step: StepRecord) -> str:
        completed = [s for s in node.steps if s.status == StepStatus.COMPLETE]
        remaining = [
            s for s in node.steps
            if s.status == StepStatus.PENDING and s is not step
        ]
        completed_names = ", ".join(s.template.name for s in completed) or "none"
        remaining_names = ", ".join(s.template.name for s in remaining) or "none"

        input_summary = self._schema_summary(node.input_schema)
        output_summary = self._schema_summary(node.output_schema)

        lines = [
            "=== NODE SPEC ===",
            f"Description: {node.description}",
            f"Input: {input_summary}",
            f"Output: {output_summary}",
            "",
            "=== PROGRESS ===",
            f"Completed steps: {completed_names}",
            f"Current step: {step.template.name}",
            f"Remaining steps: {remaining_names}",
        ]
        if node.implementation_notes:
            lines.insert(4, f"Notes: {node.implementation_notes}")
        return "\n".join(lines)

    def _build_completed_steps(
        self,
        node: TaskNode,
        current_step: StepRecord,
        global_schema: SchemaRegistry | None,
    ) -> str:
        """Include completed step outputs for context continuity."""
        completed = [
            s for s in node.steps
            if s.status == StepStatus.COMPLETE and s.output and s is not current_step
        ]
        if not completed:
            return ""

        parts = ["=== COMPLETED STEPS — for context only, do not repeat this work ==="]
        for s in completed:
            parts.append(f"\n--- {s.template.name} ---")
            parts.append(s.output)

        return "\n".join(parts)

    def _build_schema_registry(self, registry: SchemaRegistry) -> str:
        return (
            "=== GLOBAL SCHEMA REGISTRY ===\n"
            f"{registry.to_string()}"
        )

    def _build_step_prompt(self, node: TaskNode, step: StepRecord) -> str:
        # Inject {node.name} and {node} references in the template
        prompt = step.template.prompt_template
        try:
            # The template uses {node.name}, {node.description}, etc.
            # We create a simple namespace object for .format_map()
            prompt = prompt.replace("{node.name}", node.name)
            prompt = prompt.replace("{node.description}", node.description)
            prompt = prompt.replace("{node.primitive_type.value}", node.primitive_type.value)
        except (KeyError, AttributeError):
            pass

        return f"=== STEP PROMPT ===\n{prompt}"

    # -------------------------------------------------------------------
    # Budget management
    # -------------------------------------------------------------------

    def _apply_budget(
        self,
        node: TaskNode,
        step: StepRecord,
        global_schema: SchemaRegistry | None,
        correction_context: str,
    ) -> str:
        """Rebuild context with summarized completed steps to fit budget."""
        sections: list[str] = []

        sections.append(self._build_system_block(node))
        if correction_context:
            sections.append(self._build_correction_block(correction_context))
        sections.append(self._build_node_context(node, step))

        # Summarize completed steps instead of including verbatim
        summarized = self._summarize_completed_steps(node, step)
        if summarized:
            sections.append(summarized)

        if global_schema and global_schema.entries:
            sections.append(self._build_schema_registry(global_schema))

        sections.append(self._build_step_prompt(node, step))
        return "\n\n".join(sections)

    def _summarize_completed_steps(
        self, node: TaskNode, current_step: StepRecord
    ) -> str:
        """Extract key artifacts from completed steps, discard prose."""
        completed = [
            s for s in node.steps
            if s.status == StepStatus.COMPLETE and s.output and s is not current_step
        ]
        if not completed:
            return ""

        parts = [
            "=== COMPLETED STEPS (summarized — full output exceeded context budget) ==="
        ]
        for s in completed:
            # Take first 500 chars as summary
            summary = s.output[:500]
            if len(s.output) > 500:
                summary += "\n... (truncated)"
            parts.append(f"\n--- {s.template.name} ---")
            parts.append(summary)

        return "\n".join(parts)

    def _schema_summary(self, schema: NodeSchema) -> str:
        if not schema.fields:
            return "none"
        return ", ".join(
            f"{f.name}: {f.type_annotation}" for f in schema.fields
        )

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // _CHARS_PER_TOKEN

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value: int) -> None:
        self._max_tokens = value
