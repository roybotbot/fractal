"""Tests for the CLI entry point (__main__.py)."""

from __future__ import annotations

import subprocess
import sys
import tempfile

import pytest


class TestCLIHelp:
    def test_help_exits_cleanly(self):
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 0
        assert "superpowers_runner" in result.stdout

    def test_run_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "run", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 0
        assert "task" in result.stdout.lower()

    def test_list_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "list", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 0

    def test_resume_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "resume", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 0
        assert "session_id" in result.stdout


class TestCLIList:
    def test_list_empty(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "-m", "superpowers_runner",
                "--session-dir", str(tmp_path),
                "list",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 0
        assert "No sessions" in result.stdout


class TestCLINoCommand:
    def test_no_command_shows_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 1


class TestCLIResume:
    def test_resume_nonexistent(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "-m", "superpowers_runner",
                "--session-dir", str(tmp_path),
                "resume", "nonexistent",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 1
        assert "not found" in result.stdout


class TestCLIRunNoLLM:
    def test_run_without_dry_run_fails(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable, "-m", "superpowers_runner",
                "--session-dir", str(tmp_path),
                "run", "test task",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 1
        assert "dry-run" in result.stdout.lower()
