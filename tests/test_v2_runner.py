from __future__ import annotations

from pathlib import Path

import pytest

from superpowers_runner_v2.logger import ExecutionLogger
from superpowers_runner_v2.planner import Planner
from superpowers_runner_v2.runner import Runner
from superpowers_runner_v2.state import StateManager


class FakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._responses: dict[str, list[str]] = {
            "define_input_schema": ["celsius: float"],
            "define_output_schema": ["fahrenheit: float"],
            "enumerate_edge_cases": ["- 0\n- 100\n- -40"],
            "write_failing_tests": [
                """
```python
from celsius_to_fahrenheit import celsius_to_fahrenheit


def test_freezing_point() -> None:
    assert celsius_to_fahrenheit(0) == 32
```
""".strip()
            ],
            "implement_minimal": [
                """
```python
import requests


def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32
```
""".strip(),
                """
```python
def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32
```
""".strip(),
            ],
            "refactor": ["nothing needed"],
        }

    def generate(self, step_name: str, prompt: str) -> str:
        self.calls.append(step_name)
        return self._responses[step_name].pop(0)


class AlwaysBadImplementationClient(FakeLLMClient):
    def __init__(self) -> None:
        super().__init__()
        self._responses["implement_minimal"] = [
            """
```python
import requests

def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32
```
""".strip(),
            """
```python
import requests

def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32
```
""".strip(),
        ]


def test_runner_executes_fixed_step_sequence_and_retries_implementation(tmp_path: Path) -> None:
    planner = Planner()
    session = planner.plan("write a pure function that converts celsius to fahrenheit, with tests")
    client = FakeLLMClient()
    state = StateManager(base_dir=tmp_path)
    logger = ExecutionLogger(base_dir=tmp_path, session_id=session.session_id)
    runner = Runner(
        llm_client=client,
        state_manager=state,
        logger=logger,
        base_dir=tmp_path,
    )

    result = runner.run(session)
    logger.close()

    assert [step.name for step in result.task.steps if step.status.value == "complete"] == [
        "define_input_schema",
        "define_output_schema",
        "enumerate_edge_cases",
        "write_failing_tests",
        "implement_minimal",
        "refactor",
    ]
    assert client.calls == [
        "define_input_schema",
        "define_output_schema",
        "enumerate_edge_cases",
        "write_failing_tests",
        "implement_minimal",
        "implement_minimal",
        "refactor",
    ]
    assert result.task.steps[4].attempt == 2

    artifact_dir = tmp_path / session.session_id / "artifacts"
    implementation = artifact_dir / "celsius_to_fahrenheit.py"
    test_file = artifact_dir / "test_celsius_to_fahrenheit.py"
    assert implementation.exists()
    assert test_file.exists()
    assert "import requests" not in implementation.read_text()

    loaded = state.load(session.session_id)
    assert loaded.task.steps[4].attempt == 2
    assert loaded.task.steps[4].status.value == "complete"


def test_runner_writes_execution_log_events(tmp_path: Path) -> None:
    planner = Planner()
    session = planner.plan("write a pure function that converts celsius to fahrenheit, with tests")
    client = FakeLLMClient()
    state = StateManager(base_dir=tmp_path)
    logger = ExecutionLogger(base_dir=tmp_path, session_id=session.session_id)
    runner = Runner(
        llm_client=client,
        state_manager=state,
        logger=logger,
        base_dir=tmp_path,
    )

    runner.run(session)
    logger.close()

    execution_log = (tmp_path / session.session_id / "execution_log.jsonl").read_text()
    assert '"event": "step_started"' in execution_log
    assert '"event": "step_complete"' in execution_log
    assert '"event": "gate_failed"' in execution_log
    assert '"event": "gate_passed"' in execution_log


def test_runner_logs_failure_before_raising_when_implementation_fails_twice(tmp_path: Path) -> None:
    planner = Planner()
    session = planner.plan("write a pure function that converts celsius to fahrenheit, with tests")
    client = AlwaysBadImplementationClient()
    state = StateManager(base_dir=tmp_path)
    logger = ExecutionLogger(base_dir=tmp_path, session_id=session.session_id)
    runner = Runner(
        llm_client=client,
        state_manager=state,
        logger=logger,
        base_dir=tmp_path,
    )

    with pytest.raises(RuntimeError):
        runner.run(session)
    logger.close()

    execution_log = (tmp_path / session.session_id / "execution_log.jsonl").read_text()
    assert '"event": "step_failed"' in execution_log
    assert '"event": "session_failed"' in execution_log
