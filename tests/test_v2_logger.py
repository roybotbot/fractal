from __future__ import annotations

import json
from pathlib import Path

from superpowers_runner_v2.logger import ExecutionLogger


def test_logger_appends_execution_events(tmp_path: Path) -> None:
    logger = ExecutionLogger(base_dir=tmp_path, session_id="sess-123")

    logger.log_event("session_started", task="convert celsius to fahrenheit")
    logger.log_event("step_started", step="define_input_schema", attempt=1)
    logger.close()

    log_path = tmp_path / "sess-123" / "execution_log.jsonl"
    lines = log_path.read_text().splitlines()

    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["event"] == "session_started"
    assert first["session_id"] == "sess-123"
    assert first["task"] == "convert celsius to fahrenheit"
    assert second["event"] == "step_started"
    assert second["step"] == "define_input_schema"
    assert second["attempt"] == 1


def test_logger_writes_step_content_markdown(tmp_path: Path) -> None:
    logger = ExecutionLogger(base_dir=tmp_path, session_id="sess-123")

    logger.log_step_content(
        task_name="celsius_to_fahrenheit",
        step_name="define_input_schema",
        attempt=1,
        prompt="Define the input schema.",
        response="celsius: float",
    )
    logger.close()

    content_path = (
        tmp_path
        / "sess-123"
        / "content_log"
        / "celsius_to_fahrenheit"
        / "step_01_define_input_schema.md"
    )
    text = content_path.read_text()

    assert "# step: define_input_schema" in text
    assert "# attempt: 1" in text
    assert "## Prompt" in text
    assert "Define the input schema." in text
    assert "## Response" in text
    assert "celsius: float" in text
