"""Decomposer — composition node → typed child node list.

For composition nodes (pipeline, router, orchestration), generates the
child node list via LLM, then validates each child's type against the
PrimitiveType enum. Untyped or invalid children cause a retry. After
max retries, raises DecompositionFailure.

Depends on: schema layer only.
"""

from __future__ import annotations

import json
from typing import Protocol

from superpowers_runner.schema.nodes import TaskNode
from superpowers_runner.schema.primitives import PrimitiveType


class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


class DecompositionFailure(Exception):
    """Raised when the decomposer cannot produce valid typed children."""

    def __init__(self, node_name: str, reason: str) -> None:
        self.node_name = node_name
        self.reason = reason
        super().__init__(f"Decomposition failed for '{node_name}': {reason}")


class InvalidChildType(Exception):
    """Raised when a child has an unrecognized type string."""
    pass


class CircularDependency(Exception):
    """Raised when children have circular dependencies."""
    pass


# Type descriptions for the decomposition prompt
_TYPE_DESCRIPTIONS = """\
  data_model       — schema, class, struct, or type definition with validation
  interface        — contract between two systems, no implementation
  config           — runtime parameters, environment variables, feature flags
  transformation   — pure function: input → output, no side effects
  aggregation      — reduce many to one, no side effects
  validation       — input → bool + errors, no side effects
  query            — read from external state, no writes
  mutation         — write to external state
  event_emit       — fire a signal, no response expected
  event_handler    — react to an incoming event
  pipeline         — ordered chain of operations
  router           — conditional dispatch to handlers
  orchestration    — stateful multi-step coordination with rollback
  cache            — memoize a query or transformation
  auth_guard       — enforce identity/permission
  retry_policy     — failure recovery wrapper
  observer         — logging, metrics, tracing wrapper
  unit_test        — verify a single node in isolation
  integration_test — verify multiple nodes together
  contract_test    — verify an interface is honored
  fixture          — test data factory"""

DECOMPOSITION_PROMPT = """\
You are decomposing a composition node into typed child nodes.

Parent node:
  Name: {node_name}
  Type: {node_type}
  Description: {node_description}

Each child must be classified as one of:
{available_types}
No other types are valid.

Each child should represent 5–15 minutes of work. If a child would take longer, \
decompose it further. If shorter, consider merging with an adjacent node.

Do not write any code. Do not specify implementation. Name the nodes, classify \
them, and define their dependencies.

Dependencies are node names from the list you produce. A node cannot depend on \
itself. A node cannot depend on a node that depends on it.

Respond with ONLY a JSON object in this exact format:
{{
  "children": [
    {{
      "name": "node_name",
      "type": "primitive_type_value",
      "description": "One sentence description",
      "dependencies": ["other_node_name"]
    }}
  ]
}}"""


MAX_DECOMPOSITION_RETRIES = 3


def decompose(
    llm_client: LLMClient,
    node: TaskNode,
) -> list[TaskNode]:
    """Decompose a composition node into typed child nodes.

    Makes an LLM call to generate children, then validates:
    - JSON structure
    - Required fields (name, type, description)
    - Type strings against PrimitiveType enum
    - No self-referential dependencies
    - No circular dependency chains

    Retries up to MAX_DECOMPOSITION_RETRIES on validation failure.

    Raises:
        DecompositionFailure: If all retries fail validation.
    """
    prompt = DECOMPOSITION_PROMPT.format(
        node_name=node.name,
        node_type=node.primitive_type.value,
        node_description=node.description,
        available_types=_TYPE_DESCRIPTIONS,
    )

    last_error = ""
    for attempt in range(MAX_DECOMPOSITION_RETRIES):
        response = llm_client.call(prompt, max_tokens=4096)

        try:
            children = _parse_and_validate(response, node)
            return children
        except (json.JSONDecodeError, KeyError, InvalidChildType, CircularDependency) as e:
            last_error = str(e)
            # Add error feedback to prompt for retry
            prompt = (
                f"{prompt}\n\n"
                f"Your previous response was invalid: {last_error}\n"
                f"Please try again with valid JSON and valid type strings."
            )

    raise DecompositionFailure(node.name, f"After {MAX_DECOMPOSITION_RETRIES} attempts: {last_error}")


def _parse_and_validate(response: str, parent: TaskNode) -> list[TaskNode]:
    """Parse LLM response and validate all children."""
    # Extract JSON from response (handle markdown code blocks)
    json_str = _extract_json(response)
    data = json.loads(json_str)

    if "children" not in data:
        raise KeyError("Missing 'children' key in response")

    children_specs = data["children"]
    if not isinstance(children_specs, list) or len(children_specs) == 0:
        raise KeyError("'children' must be a non-empty list")

    valid_types = {t.value for t in PrimitiveType}
    children: list[TaskNode] = []
    name_to_node: dict[str, TaskNode] = {}

    for spec in children_specs:
        # Validate required fields
        if "name" not in spec:
            raise KeyError("Child missing 'name' field")
        if "type" not in spec:
            raise KeyError(f"Child '{spec['name']}' missing 'type' field")
        if "description" not in spec:
            raise KeyError(f"Child '{spec['name']}' missing 'description' field")

        # Validate type
        type_str = spec["type"].strip().lower()
        if type_str not in valid_types:
            raise InvalidChildType(
                f"Child '{spec['name']}' has invalid type '{spec['type']}'. "
                f"Valid types: {', '.join(sorted(valid_types))}"
            )

        child = TaskNode(
            name=spec["name"],
            description=spec["description"],
            primitive_type=PrimitiveType(type_str),
            parent_id=parent.id,
        )
        children.append(child)
        name_to_node[spec["name"]] = child

    # Resolve dependency names to ids and validate
    for spec, child in zip(children_specs, children):
        dep_names = spec.get("dependencies", [])
        if not isinstance(dep_names, list):
            dep_names = []

        for dep_name in dep_names:
            if dep_name == spec["name"]:
                raise CircularDependency(
                    f"Child '{spec['name']}' depends on itself"
                )
            if dep_name in name_to_node:
                child.dependency_ids.append(name_to_node[dep_name].id)

    # Check for circular dependencies
    _check_circular_deps(children, name_to_node, children_specs)

    return children


def _check_circular_deps(
    children: list[TaskNode],
    name_to_node: dict[str, TaskNode],
    specs: list[dict],
) -> None:
    """Detect circular dependency chains via topological sort attempt."""
    # Build adjacency list by name for easier checking
    name_to_deps: dict[str, set[str]] = {}
    for spec in specs:
        dep_names = spec.get("dependencies", [])
        if not isinstance(dep_names, list):
            dep_names = []
        name_to_deps[spec["name"]] = set(
            d for d in dep_names if d in name_to_node
        )

    # Kahn's algorithm for cycle detection
    in_degree: dict[str, int] = {name: 0 for name in name_to_deps}
    for name, deps in name_to_deps.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[name] += 1  # This is wrong — should count incoming

    # Redo properly: in_degree[X] = number of nodes X depends on
    in_degree = {name: len(deps) for name, deps in name_to_deps.items()}
    # Reverse: for each dependency, it blocks the dependent
    dependents: dict[str, list[str]] = {name: [] for name in name_to_deps}
    for name, deps in name_to_deps.items():
        for dep in deps:
            if dep in dependents:
                dependents[dep].append(name)

    queue = [name for name, deg in in_degree.items() if deg == 0]
    visited = 0

    while queue:
        current = queue.pop(0)
        visited += 1
        for dependent in dependents.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if visited < len(name_to_deps):
        raise CircularDependency(
            "Circular dependency detected among children"
        )


def _extract_json(text: str) -> str:
    """Extract JSON from text that might be wrapped in markdown code blocks."""
    # Try to find JSON in code block
    if "```json" in text:
        start = text.index("```json") + len("```json")
        end = text.index("```", start)
        return text[start:end].strip()
    if "```" in text:
        start = text.index("```") + len("```")
        end = text.index("```", start)
        return text[start:end].strip()
    # Try the raw text
    # Find the first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        return text[first_brace:last_brace + 1]
    return text.strip()
