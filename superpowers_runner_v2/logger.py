from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


class ExecutionLogger:
    """Tiny dual-purpose logger for the v2 prototype.

    - `execution_log.jsonl` captures structural events.
    - `content_log/` captures the prompt/response text for each step attempt.

    This keeps the first vertical slice debuggable without pulling in the much
    larger logging system from the original package.
    """

    def __init__(self, base_dir: str | Path, session_id: str) -> None:
        self.session_id = session_id
        self.session_dir = Path(base_dir) / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.content_dir = self.session_dir / "content_log"
        self.content_dir.mkdir(exist_ok=True)
        self._handle = (self.session_dir / "execution_log.jsonl").open("a")

    def close(self) -> None:
        """Close the JSONL handle explicitly when the session ends."""
        if not self._handle.closed:
            self._handle.close()

    def log_event(self, event: str, **fields: Any) -> None:
        """Append one structural event and flush immediately.

        Immediate flushing makes the tiny prototype resilient to interruption.
        """
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "session_id": self.session_id,
            **fields,
        }
        self._handle.write(json.dumps(record) + "\n")
        self._handle.flush()

    def log_step_content(
        self,
        task_name: str,
        step_name: str,
        attempt: int,
        prompt: str,
        response: str,
    ) -> Path:
        """Write a human-readable markdown file for one step attempt."""
        task_dir = self.content_dir / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        filename = f"step_{attempt:02d}_{step_name}.md"
        path = task_dir / filename
        path.write_text(
            "\n".join(
                [
                    f"# step: {step_name}",
                    f"# attempt: {attempt}",
                    "",
                    "## Prompt",
                    "",
                    prompt,
                    "",
                    "## Response",
                    "",
                    response,
                ]
            )
        )
        return path
