from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum


# The first v2 slice hard-codes one transformation workflow.
# Keeping the step list explicit makes the prototype easy to inspect.
STEP_NAMES = (
    "define_input_schema",
    "define_output_schema",
    "enumerate_edge_cases",
    "write_failing_tests",
    "implement_minimal",
    "refactor",
)


class StepStatus(StrEnum):
    """Minimal step state for the first scratch-built vertical slice."""

    PENDING = "pending"
    COMPLETE = "complete"


@dataclass
class StepRecord:
    """Runtime state for a single fixed transformation step."""

    name: str
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    attempt: int = 0


@dataclass
class TransformationTask:
    """The only supported task type in v2 right now.

    This deliberately avoids generic primitives until the basic execution,
    artifacts, logging, and resume flow are trustworthy.
    """

    name: str
    description: str
    steps: list[StepRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        # If no runtime state was provided, attach the canonical step sequence.
        if not self.steps:
            self.steps = [StepRecord(name=name) for name in STEP_NAMES]


@dataclass
class V2Session:
    """Top-level persisted unit for the v2 scratch prototype."""

    session_id: str
    task_prompt: str
    task: TransformationTask
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
