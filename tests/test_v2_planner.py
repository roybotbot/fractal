from __future__ import annotations

from superpowers_runner_v2.planner import Planner
from superpowers_runner_v2.schema import STEP_NAMES


def test_planner_creates_single_transformation_session() -> None:
    planner = Planner()

    session = planner.plan("write a pure function that converts celsius to fahrenheit, with tests")

    assert session.session_id.startswith("sess-")
    assert session.task.name == "celsius_to_fahrenheit"
    assert session.task.description
    assert [step.name for step in session.task.steps] == list(STEP_NAMES)
    assert session.task_prompt == "write a pure function that converts celsius to fahrenheit, with tests"
