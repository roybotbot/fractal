from __future__ import annotations

import json
from pathlib import Path

from superpowers_runner_v2.schema import STEP_NAMES, StepRecord, StepStatus, TransformationTask, V2Session


class StateManager:
    """Persist and restore the v2 session model.

    The state format intentionally stores only runtime fields. On load, the
    canonical fixed step list is rebuilt from `STEP_NAMES`, then runtime values
    are merged back in by step name.
    """

    def __init__(self, base_dir: str | Path = "sessions_v2") -> None:
        self.base_dir = Path(base_dir)

    def save(self, session: V2Session) -> Path:
        """Write the current session snapshot to disk."""
        session_dir = self.base_dir / session.session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / "session.json"
        path.write_text(json.dumps(self._to_dict(session), indent=2))
        return path

    def load(self, session_id: str) -> V2Session:
        """Load a previously saved session snapshot."""
        path = self.base_dir / session_id / "session.json"
        data = json.loads(path.read_text())
        return self._from_dict(data)

    def _to_dict(self, session: V2Session) -> dict:
        # Keep the persisted shape simple and explicit while the prototype is small.
        return {
            "session_id": session.session_id,
            "task_prompt": session.task_prompt,
            "task": {
                "name": session.task.name,
                "description": session.task.description,
                "steps": [
                    {
                        "name": step.name,
                        "status": step.status.value,
                        "output": step.output,
                        "attempt": step.attempt,
                    }
                    for step in session.task.steps
                ],
            },
        }

    def _from_dict(self, data: dict) -> V2Session:
        task_data = data["task"]

        # Runtime step state is keyed by step name so we can restore the fixed
        # workflow even if the on-disk JSON only contains already-touched steps.
        runtime_by_name = {
            step["name"]: step
            for step in task_data.get("steps", [])
        }
        steps: list[StepRecord] = []
        for name in STEP_NAMES:
            runtime = runtime_by_name.get(name, {})
            steps.append(
                StepRecord(
                    name=name,
                    status=StepStatus(runtime.get("status", StepStatus.PENDING.value)),
                    output=runtime.get("output", ""),
                    attempt=runtime.get("attempt", 0),
                )
            )

        return V2Session(
            session_id=data["session_id"],
            task_prompt=data["task_prompt"],
            task=TransformationTask(
                name=task_data["name"],
                description=task_data["description"],
                steps=steps,
            ),
        )
