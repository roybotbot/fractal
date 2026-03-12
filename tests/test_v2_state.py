from __future__ import annotations

from pathlib import Path

from superpowers_runner_v2.schema import (
    STEP_NAMES,
    StepRecord,
    StepStatus,
    TransformationTask,
    V2Session,
)
from superpowers_runner_v2.state import StateManager


def test_transformation_task_uses_fixed_step_sequence() -> None:
    task = TransformationTask(
        name="celsius_to_fahrenheit",
        description="Convert celsius to fahrenheit with tests",
    )

    assert [step.name for step in task.steps] == list(STEP_NAMES)
    assert all(step.status == StepStatus.PENDING for step in task.steps)


def test_state_manager_round_trips_session_runtime_fields(tmp_path: Path) -> None:
    session = V2Session(
        session_id="sess-123",
        task_prompt="write a pure function that converts celsius to fahrenheit, with tests",
        task=TransformationTask(
            name="celsius_to_fahrenheit",
            description="Convert celsius to fahrenheit with tests",
        ),
    )
    session.task.steps[0].status = StepStatus.COMPLETE
    session.task.steps[0].output = "celsius: float"
    session.task.steps[0].attempt = 1

    manager = StateManager(base_dir=tmp_path)
    save_path = manager.save(session)
    loaded = manager.load("sess-123")

    assert save_path == tmp_path / "sess-123" / "session.json"
    assert loaded.session_id == session.session_id
    assert loaded.task_prompt == session.task_prompt
    assert loaded.task.name == session.task.name
    assert [step.name for step in loaded.task.steps] == list(STEP_NAMES)
    assert loaded.task.steps[0].status == StepStatus.COMPLETE
    assert loaded.task.steps[0].output == "celsius: float"
    assert loaded.task.steps[0].attempt == 1


def test_state_manager_load_preserves_fixed_steps_even_if_json_only_has_runtime_fields(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-456"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        """
        {
          "session_id": "sess-456",
          "task_prompt": "convert celsius to fahrenheit",
          "task": {
            "name": "celsius_to_fahrenheit",
            "description": "Convert celsius to fahrenheit",
            "steps": [
              {
                "name": "define_input_schema",
                "status": "complete",
                "output": "celsius: float",
                "attempt": 1
              }
            ]
          }
        }
        """.strip()
    )

    manager = StateManager(base_dir=tmp_path)
    loaded = manager.load("sess-456")

    assert [step.name for step in loaded.task.steps] == list(STEP_NAMES)
    assert loaded.task.steps[0].status == StepStatus.COMPLETE
    assert loaded.task.steps[0].output == "celsius: float"
    assert loaded.task.steps[0].attempt == 1
    assert loaded.task.steps[1].status == StepStatus.PENDING
