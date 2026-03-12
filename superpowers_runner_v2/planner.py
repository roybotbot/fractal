from __future__ import annotations

import re
from uuid import uuid4

from superpowers_runner_v2.schema import TransformationTask, V2Session


class Planner:
    """Very small planner for the v2 prototype.

    It does not classify, decompose, or ask the model for structure yet.
    Its job is only to turn a raw task prompt into one transformation session.
    """

    def plan(self, task_prompt: str) -> V2Session:
        return V2Session(
            session_id=f"sess-{uuid4().hex[:8]}",
            task_prompt=task_prompt,
            task=TransformationTask(
                name=self._derive_name(task_prompt),
                description=task_prompt.strip(),
            ),
        )

    def _derive_name(self, task_prompt: str) -> str:
        """Derive a stable-ish snake_case task name from a natural-language prompt."""
        lowered = task_prompt.lower()
        match = re.search(r"converts?\s+([a-z]+)\s+to\s+([a-z]+)", lowered)
        if match:
            return f"{match.group(1)}_to_{match.group(2)}"

        words = re.findall(r"[a-z0-9]+", lowered)
        if not words:
            return "transformation_task"
        return "_".join(words[:4])
