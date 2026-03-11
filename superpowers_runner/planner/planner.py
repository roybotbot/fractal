"""Planner — orchestrates classification, root creation, and decomposition.

Takes a raw task description string and produces a TaskTree with typed nodes.
The most LLM-dependent layer and therefore the most rigorously checked.

Depends on: schema layer, planner/classifier, planner/decomposer.
"""

from __future__ import annotations

import json
from typing import Protocol

from superpowers_runner.schema.nodes import TaskNode, TaskTree
from superpowers_runner.schema.primitives import COMPOSITION_TYPES, PrimitiveType
from superpowers_runner.planner.classifier import classify, ClassificationFailure
from superpowers_runner.planner.decomposer import decompose, DecompositionFailure


class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


ROOT_NODE_PROMPT = """\
You are creating the root node for a task execution tree.
The task type has already been determined — do not change it.

Task: {task}
Type: {type}

Respond with ONLY a JSON object:
{{
  "name": "short_snake_case_name",
  "description": "One or two sentence description of what this node produces",
  "notes": "Optional implementation guidance"
}}"""


class Planner:
    """Main planner. Classifies task, creates root, decomposes if needed."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def plan(self, task_description: str, session_id: str = "") -> TaskTree:
        """Generate a typed TaskTree from a raw task description.

        Phases:
        1. Classify task → PrimitiveType
        2. Create root node (LLM slot-filling)
        3. Decompose if composition type
        4. Return tree (human checkpoint happens outside this method)

        Raises:
            ClassificationFailure: If task can't be classified.
            DecompositionFailure: If composition node can't be decomposed.
        """
        # Phase 1: classify
        primitive_type = classify(self._llm_client, task_description)

        # Phase 2: create root node
        root = self._create_root(task_description, primitive_type)

        # Phase 3: build tree
        tree = TaskTree(session_id=session_id)
        tree.root = root
        tree.register(root)

        # Phase 3b: decompose if composition type
        if primitive_type in COMPOSITION_TYPES:
            children = decompose(self._llm_client, root)
            root.sub_nodes = children
            for child in children:
                tree.register(child)

                # Recursively decompose composition children
                if child.primitive_type in COMPOSITION_TYPES:
                    grandchildren = decompose(self._llm_client, child)
                    child.sub_nodes = grandchildren
                    for gc in grandchildren:
                        tree.register(gc)

        return tree

    def _create_root(
        self, task: str, primitive_type: PrimitiveType
    ) -> TaskNode:
        """Create the root node via LLM slot-filling."""
        prompt = ROOT_NODE_PROMPT.format(task=task, type=primitive_type.value)
        response = self._llm_client.call(prompt, max_tokens=500)

        try:
            json_str = _extract_json(response)
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            # Fallback: use task description as name
            return TaskNode(
                name=task[:50].replace(" ", "_").lower(),
                description=task,
                primitive_type=primitive_type,
            )

        return TaskNode(
            name=data.get("name", task[:50].replace(" ", "_").lower()),
            description=data.get("description", task),
            implementation_notes=data.get("notes", ""),
            primitive_type=primitive_type,
        )


def _extract_json(text: str) -> str:
    """Extract JSON from text that might be wrapped in markdown code blocks."""
    if "```json" in text:
        start = text.index("```json") + len("```json")
        end = text.index("```", start)
        return text[start:end].strip()
    if "```" in text:
        start = text.index("```") + len("```")
        end = text.index("```", start)
        return text[start:end].strip()
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]
    return text.strip()
