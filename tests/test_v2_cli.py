from __future__ import annotations

import subprocess
from pathlib import Path


class TestV2CLI:
    def test_run_command_creates_session_artifacts(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                "python",
                "-m",
                "superpowers_runner_v2",
                "--base-dir",
                str(tmp_path),
                "run",
                "write a pure function that converts celsius to fahrenheit, with tests",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0
        assert "Session:" in result.stdout
        assert "Task complete" in result.stdout

        session_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]
        assert (session_dir / "session.json").exists()
        assert (session_dir / "execution_log.jsonl").exists()
        assert (session_dir / "artifacts" / "celsius_to_fahrenheit.py").exists()
        assert (session_dir / "artifacts" / "test_celsius_to_fahrenheit.py").exists()

    def test_resume_command_continues_existing_session(self, tmp_path: Path) -> None:
        first = subprocess.run(
            [
                "python",
                "-m",
                "superpowers_runner_v2",
                "--base-dir",
                str(tmp_path),
                "run",
                "write a pure function that converts celsius to fahrenheit, with tests",
                "--dry-run",
                "--stop-after-step",
                "write_failing_tests",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert first.returncode == 0
        session_line = next(line for line in first.stdout.splitlines() if line.startswith("Session:"))
        session_id = session_line.split(":", 1)[1].strip()

        resumed = subprocess.run(
            [
                "python",
                "-m",
                "superpowers_runner_v2",
                "--base-dir",
                str(tmp_path),
                "resume",
                session_id,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert resumed.returncode == 0
        assert "Resumed:" in resumed.stdout
        assert "Task complete" in resumed.stdout
