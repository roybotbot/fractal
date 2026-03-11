"""Notifier — UncertaintyBuffer, interrupt/batch routing, timeout handler.

Routes uncertainty signals to human review via interrupt (immediate) or
batch (buffered) based on signal type. Handles timeouts with per-type
default resolutions. Tracks auto-resolution count for the safety valve.

Depends on: schema layer only.
"""

from __future__ import annotations

import time
from datetime import datetime, UTC
from typing import Callable

from superpowers_runner.schema.signals import (
    BATCH_AND_NOTIFY,
    BATCH_FLUSH_SIGNAL_COUNT,
    BATCH_FLUSH_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_RESOLUTION,
    INTERRUPT_IMMEDIATELY,
    MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT,
    Resolution,
    UncertaintySignal,
    UncertaintyType,
)


# Type alias for the human input callback
# Takes a list of signals, returns a list of (Resolution, human_note) pairs.
# None means timeout/no response — use defaults.
HumanInputCallback = Callable[
    [list[UncertaintySignal]],
    list[tuple[Resolution, str]] | None,
]


def _default_human_input(signals: list[UncertaintySignal]) -> list[tuple[Resolution, str]] | None:
    """Default: auto-resolve with per-type timeout defaults."""
    return None


class Notifier:
    """Routes uncertainty signals to human review.

    Supports two modes:
    - interrupt: presents signals immediately and blocks until resolved
    - batch: buffers signals and flushes at checkpoints

    The human_input callback is the integration point for terminal UI,
    API endpoints, or test mocks.
    """

    def __init__(
        self,
        human_input: HumanInputCallback | None = None,
    ) -> None:
        self._human_input = human_input or _default_human_input
        self._buffer: list[UncertaintySignal] = []
        self._buffer_start_time: float | None = None
        self._auto_resolution_count: int = 0

    def interrupt(self, signals: list[UncertaintySignal]) -> list[Resolution]:
        """Present signals immediately and block until resolved.

        Used for INTERRUPT_IMMEDIATELY types (schema_near_miss, self_contradiction).
        """
        result = self._human_input(signals)

        if result is None:
            # Timeout — apply defaults
            resolutions = self._apply_defaults(signals)
            self._auto_resolution_count += len(signals)
            return resolutions

        self._auto_resolution_count = 0  # Human responded — reset counter
        resolutions = []
        for (resolution, note), signal in zip(result, signals):
            signal.resolution = resolution
            signal.resolved_by = "human"
            signal.resolved_at = datetime.now(UTC)
            signal.human_note = note
            resolutions.append(resolution)
        return resolutions

    def buffer(self, signals: list[UncertaintySignal]) -> None:
        """Add signals to the batch buffer."""
        if not self._buffer:
            self._buffer_start_time = time.monotonic()
        self._buffer.extend(signals)

    def should_flush(self) -> bool:
        """Check if the buffer should be flushed.

        Flush conditions (whichever first):
        - 3 signals accumulated
        - 120 seconds elapsed
        - Force interrupt (auto-resolution safety valve)
        """
        if not self._buffer:
            return False

        if len(self._buffer) >= BATCH_FLUSH_SIGNAL_COUNT:
            return True

        if self._buffer_start_time is not None:
            elapsed = time.monotonic() - self._buffer_start_time
            if elapsed >= BATCH_FLUSH_TIMEOUT_SECONDS:
                return True

        if self._auto_resolution_count >= MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT:
            return True

        return False

    def flush_for_node_end(self) -> bool:
        """Check if buffer should flush because a node just completed."""
        return len(self._buffer) > 0

    def drain(self) -> list[UncertaintySignal]:
        """Remove and return all buffered signals."""
        signals = list(self._buffer)
        self._buffer.clear()
        self._buffer_start_time = None
        return signals

    def notify_batch(self, signals: list[UncertaintySignal]) -> list[Resolution]:
        """Present a batch of signals for review.

        Same as interrupt but for batched signals.
        """
        if not signals:
            return []

        result = self._human_input(signals)

        if result is None:
            resolutions = self._apply_defaults(signals)
            self._auto_resolution_count += len(signals)
            return resolutions

        self._auto_resolution_count = 0
        resolutions = []
        for (resolution, note), signal in zip(result, signals):
            signal.resolution = resolution
            signal.resolved_by = "human"
            signal.resolved_at = datetime.now(UTC)
            signal.human_note = note
            resolutions.append(resolution)
        return resolutions

    def _apply_defaults(self, signals: list[UncertaintySignal]) -> list[Resolution]:
        """Apply per-type default resolutions for timeouts."""
        resolutions: list[Resolution] = []
        for signal in signals:
            default = DEFAULT_TIMEOUT_RESOLUTION.get(
                signal.uncertainty_type, Resolution.PROCEED
            )
            signal.resolution = default
            signal.resolved_by = "timeout"
            signal.resolved_at = datetime.now(UTC)
            resolutions.append(default)
        return resolutions

    @property
    def auto_resolution_count(self) -> int:
        return self._auto_resolution_count

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def force_interrupt_needed(self) -> bool:
        """True if auto-resolution safety valve has been tripped."""
        return self._auto_resolution_count >= MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT
