"""Signal types for drift detection and uncertainty handling.

Defines DriftSignal, UncertaintySignal, ResolutionRecord, and all supporting
enums and constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class DriftType(Enum):
    SCOPE = "scope"
    PHASE = "phase"
    INSTRUCTION = "instruction"
    SCHEMA = "schema"
    COMPLETION = "completion"


class UncertaintyType(Enum):
    AMBIGUOUS_SCOPE = "ambiguous_scope"
    AMBIGUOUS_PHASE = "ambiguous_phase"
    PARTIAL_ADHERENCE = "partial_adherence"
    SCHEMA_NEAR_MISS = "schema_near_miss"
    SUSPICIOUSLY_FAST = "suspiciously_fast"
    SELF_CONTRADICTION = "self_contradiction"


class Severity(Enum):
    WARN = "warn"
    BLOCK = "block"
    ABORT = "abort"


class Resolution(Enum):
    PROCEED = "proceed"
    RETRY = "retry"
    ESCALATE = "escalate"


# Notification policy constants
INTERRUPT_IMMEDIATELY: frozenset[UncertaintyType] = frozenset({
    UncertaintyType.SCHEMA_NEAR_MISS,
    UncertaintyType.SELF_CONTRADICTION,
})

BATCH_AND_NOTIFY: frozenset[UncertaintyType] = frozenset({
    UncertaintyType.AMBIGUOUS_SCOPE,
    UncertaintyType.AMBIGUOUS_PHASE,
    UncertaintyType.PARTIAL_ADHERENCE,
    UncertaintyType.SUSPICIOUSLY_FAST,
})

# Batch flush thresholds
BATCH_FLUSH_SIGNAL_COUNT: int = 3
BATCH_FLUSH_TIMEOUT_SECONDS: int = 120

# Timeout defaults per uncertainty type
DEFAULT_TIMEOUT_SECONDS: int = 300

DEFAULT_TIMEOUT_RESOLUTION: dict[UncertaintyType, Resolution] = {
    UncertaintyType.AMBIGUOUS_SCOPE: Resolution.PROCEED,
    UncertaintyType.AMBIGUOUS_PHASE: Resolution.PROCEED,
    UncertaintyType.PARTIAL_ADHERENCE: Resolution.RETRY,
    UncertaintyType.SCHEMA_NEAR_MISS: Resolution.RETRY,
    UncertaintyType.SUSPICIOUSLY_FAST: Resolution.RETRY,
    UncertaintyType.SELF_CONTRADICTION: Resolution.ESCALATE,
}

# Auto-resolution safety valve
MAX_AUTO_RESOLUTIONS_BEFORE_FORCE_INTERRUPT: int = 5


@dataclass
class DriftSignal:
    id: str
    drift_type: DriftType
    severity: Severity
    node_id: str
    step_name: str
    evidence: str
    output_excerpt: str
    correction_template: str
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def correction_context(self) -> str:
        """Build the correction context string for retry prompts."""
        return (
            f"DRIFT DETECTED: {self.drift_type.value}\n"
            f"EVIDENCE: {self.evidence}\n"
            f"PROBLEMATIC OUTPUT: {self.output_excerpt}\n"
            f"CORRECTION REQUIRED: {self.correction_template}"
        )


@dataclass
class UncertaintySignal:
    id: str
    uncertainty_type: UncertaintyType
    node_id: str
    step_name: str
    confidence: float  # 0.0-1.0
    evidence: str
    output_excerpt: str
    question: str
    option_a: str
    option_b: str
    default_resolution: Resolution
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    relevant_node_spec: str = ""
    detected_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    resolved_by: Literal["human", "timeout"] | None = None
    resolution: Resolution | None = None
    human_note: str = ""


@dataclass
class ResolutionRecord:
    signal_id: str
    session_id: str
    node_id: str
    step_name: str
    timestamp: datetime

    # For uncertainty signals
    uncertainty_type: UncertaintyType | None = None
    confidence: float | None = None
    resolved_by: Literal["human", "timeout"] | None = None
    human_note: str = ""

    # For drift signals
    drift_type: DriftType | None = None
    severity: Severity | None = None
    retry_succeeded: bool | None = None

    # Common
    resolution: Resolution | None = None
