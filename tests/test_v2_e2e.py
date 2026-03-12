from __future__ import annotations

import json
from pathlib import Path

from superpowers_runner_v2.logger import ExecutionLogger
from superpowers_runner_v2.planner import Planner
from superpowers_runner_v2.runner import Runner
from superpowers_runner_v2.state import StateManager


class ResumeAwareFakeLLMClient:
    def __init__(self) -> None:
        self._responses: dict[str, list[str]] = {
            "define_input_schema": ["celsius: float"],
            "define_output_schema": ["fahrenheit: float"],
            "enumerate_edge_cases": ["- freezing point\n- boiling point\n- negative values"],
            "write_failing_tests": [
                """
```python
from celsius_to_fahrenheit import celsius_to_fahrenheit


def test_freezing_point() -> None:
    assert celsius_to_fahrenheit(0) == 32


def test_boiling_point() -> None:
    assert celsius_to_fahrenheit(100) == 212


def test_negative_point() -> None:
    assert celsius_to_fahrenheit(-40) == -40
```
""".strip()
            ],
            "implement_minimal": [
                """
```python
def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32
```
""".strip()
            ],
            "refactor": ["nothing needed"],
        }
        self.calls: list[str] = []

    def generate(self, step_name: str, prompt: str) -> str:
        self.calls.append(step_name)
        return self._responses[step_name].pop(0)


def test_full_v2_vertical_slice_can_resume_mid_session(tmp_path: Path) -> None:
    planner = Planner()
    original = planner.plan("write a pure function that converts celsius to fahrenheit, with tests")

    # Simulate an interrupted run after planning-only steps and test generation.
    original.task.steps[0].status = original.task.steps[0].status.__class__.COMPLETE
    original.task.steps[0].attempt = 1
    original.task.steps[0].output = "celsius: float"

    original.task.steps[1].status = original.task.steps[1].status.__class__.COMPLETE
    original.task.steps[1].attempt = 1
    original.task.steps[1].output = "fahrenheit: float"

    original.task.steps[2].status = original.task.steps[2].status.__class__.COMPLETE
    original.task.steps[2].attempt = 1
    original.task.steps[2].output = "- freezing point\n- boiling point\n- negative values"

    original.task.steps[3].status = original.task.steps[3].status.__class__.COMPLETE
    original.task.steps[3].attempt = 1
    original.task.steps[3].output = (
        "```python\n"
        "from celsius_to_fahrenheit import celsius_to_fahrenheit\n\n"
        "def test_freezing_point() -> None:\n"
        "    assert celsius_to_fahrenheit(0) == 32\n\n"
        "def test_boiling_point() -> None:\n"
        "    assert celsius_to_fahrenheit(100) == 212\n\n"
        "def test_negative_point() -> None:\n"
        "    assert celsius_to_fahrenheit(-40) == -40\n"
        "```"
    )

    state = StateManager(base_dir=tmp_path)
    state.save(original)

    resumed = state.load(original.session_id)
    client = ResumeAwareFakeLLMClient()
    logger = ExecutionLogger(base_dir=tmp_path, session_id=resumed.session_id)
    runner = Runner(
        llm_client=client,
        state_manager=state,
        logger=logger,
        base_dir=tmp_path,
    )

    result = runner.run(resumed)
    logger.close()

    # Resume should only execute the unfinished steps.
    assert client.calls == ["implement_minimal", "refactor"]
    assert all(step.status.value == "complete" for step in result.task.steps)

    session_dir = tmp_path / result.session_id
    artifacts_dir = session_dir / "artifacts"
    implementation_path = artifacts_dir / "celsius_to_fahrenheit.py"
    state_path = session_dir / "session.json"
    execution_log = session_dir / "execution_log.jsonl"

    assert implementation_path.exists()
    assert state_path.exists()
    assert execution_log.exists()

    persisted = json.loads(state_path.read_text())
    assert persisted["task"]["steps"][4]["status"] == "complete"
    assert persisted["task"]["steps"][5]["status"] == "complete"

    log_text = execution_log.read_text()
    assert '"event": "session_started"' in log_text
    assert '"event": "session_complete"' in log_text
