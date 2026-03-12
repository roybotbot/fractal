from __future__ import annotations

import re
from pathlib import Path


# The first scratch-built slice only needs a simple fenced-code extractor.
_CODE_BLOCK = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def extract_fenced_code(output: str) -> str:
    """Return the first fenced code block, or the raw text if none exists."""
    match = _CODE_BLOCK.search(output)
    if not match:
        return output.strip()
    return match.group(1).strip()


class ArtifactWriter:
    """Write the small set of durable files used by the v2 runner."""

    def __init__(self, base_dir: str | Path = "sessions_v2") -> None:
        self.base_dir = Path(base_dir)

    def write_implementation(self, session_id: str, task_name: str, content: str) -> Path:
        """Write the implementation module for the current transformation task."""
        path = self.base_dir / session_id / "artifacts" / f"{task_name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def write_test(self, session_id: str, task_name: str, content: str) -> Path:
        """Write the pytest file for the current transformation task."""
        path = self.base_dir / session_id / "artifacts" / f"test_{task_name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path
