"""DriftLog — append-only JSONL log for drift and uncertainty signals.

Each signal + resolution is one JSON line. Never rewritten.
Independent of tree.json — survives tree corruption.

Depends on: schema layer only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from superpowers_runner.schema.signals import (
    DriftSignal,
    UncertaintySignal,
)


class DriftLog:
    """Append-only JSONL log for signal + resolution records."""

    def __init__(self, session_dir: str, session_id: str) -> None:
        self._path = Path(session_dir) / session_id / "drift_log.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_uncertainty(
        self,
        signal: UncertaintySignal,
        session_id: str,
    ) -> None:
        """Write an uncertainty signal resolution to the log."""
        record: dict[str, Any] = {
            "signal_id": signal.id,
            "uncertainty_type": signal.uncertainty_type.value,
            "confidence": signal.confidence,
            "resolution": signal.resolution.value if signal.resolution else None,
            "resolved_by": signal.resolved_by,
            "human_note": signal.human_note,
            "session_id": session_id,
            "node_id": signal.node_id,
            "step_name": signal.step_name,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._append(record)

    def log_drift(
        self,
        signal: DriftSignal,
        session_id: str,
        resolution: str = "retried",
        retry_succeeded: bool = False,
    ) -> None:
        """Write a drift signal resolution to the log."""
        record: dict[str, Any] = {
            "signal_id": signal.id,
            "drift_type": signal.drift_type.value,
            "severity": signal.severity.value,
            "resolution": resolution,
            "retry_succeeded": retry_succeeded,
            "session_id": session_id,
            "node_id": signal.node_id,
            "step_name": signal.step_name,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._append(record)

    def read_all(self) -> list[dict[str, Any]]:
        """Read all log entries."""
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def read_by_type(self, signal_type: str) -> list[dict[str, Any]]:
        """Read entries filtered by uncertainty_type or drift_type."""
        return [
            r for r in self.read_all()
            if r.get("uncertainty_type") == signal_type
            or r.get("drift_type") == signal_type
        ]

    @property
    def path(self) -> Path:
        return self._path

    def _append(self, record: dict[str, Any]) -> None:
        """Append a single JSON line to the log file."""
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")
