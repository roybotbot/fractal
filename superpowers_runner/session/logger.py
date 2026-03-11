"""ExecutionLogger — dual-target structured logging.

Two log targets, never merged:
1. execution_log.jsonl — machine-readable structural events, append-only
2. content_log/ — human-readable markdown files per step attempt

Depends on: schema layer only. No runner imports.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from superpowers_runner.schema.nodes import (
    GateResult,
    StepRecord,
    TaskNode,
    TaskTree,
)
from superpowers_runner.schema.primitives import GateTemplate
from superpowers_runner.schema.signals import (
    DriftSignal,
    Resolution,
    UncertaintySignal,
)


def compute_node_path(tree: TaskTree, node: TaskNode) -> str:
    """Compute slash-separated tree path for a node.

    Walks the parent_id chain upward, then reverses.
    Root node returns just its name. Children return "root/child".

    >>> compute_node_path(tree, leaf)
    'password_reset_flow/generate_token'
    """
    parts: list[str] = []
    current: TaskNode | None = node
    while current is not None:
        parts.append(current.name)
        if current.parent_id:
            current = tree.get(current.parent_id)
        else:
            current = None
    return "/".join(reversed(parts))


class ExecutionLogger:
    """Dual-target logger for structured session logging.

    Passed into the runner at construction. The runner calls event methods
    (e.g. self.logger.step_started(node, step)) and the logger handles
    formatting, file writes, and both targets simultaneously.

    The logger has no dependency on the runner — dependency flows one way.
    """

    def __init__(self, session_dir: Path | str, session_id: str) -> None:
        self.session_id = session_id
        self._session_dir = Path(session_dir) / session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Execution log — append-only JSONL
        self._exec_log_path = self._session_dir / "execution_log.jsonl"
        self._exec_log = open(self._exec_log_path, "a")

        # Content log directory
        self.content_log_dir = self._session_dir / "content_log"
        self.content_log_dir.mkdir(exist_ok=True)

        # node_id → computed path (schema is frozen, can't add path to TaskNode)
        self._node_paths: dict[str, str] = {}

    def close(self) -> None:
        """Close the execution log file handle."""
        if not self._exec_log.closed:
            self._exec_log.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Node registration
    # -------------------------------------------------------------------

    def register_node(self, tree: TaskTree, node: TaskNode) -> None:
        """Compute and cache node_path. Call once per node."""
        if node.id not in self._node_paths:
            self._node_paths[node.id] = compute_node_path(tree, node)

    def get_node_path(self, node: TaskNode) -> str | None:
        """Return cached node path, or None if not registered."""
        return self._node_paths.get(node.id)

    # -------------------------------------------------------------------
    # Session events
    # -------------------------------------------------------------------

    def session_started(self, session_id: str, task: str) -> None:
        self._write_event("session_started", None, task=task)

    def session_complete(self, session_id: str, duration_ms: int) -> None:
        self._write_event("session_complete", None, duration_ms=duration_ms)

    def session_failed(
        self, session_id: str, reason: str, signal_ids: list[str]
    ) -> None:
        self._write_event(
            "session_failed", None, reason=reason, signal_ids=signal_ids
        )

    # -------------------------------------------------------------------
    # Node events
    # -------------------------------------------------------------------

    def node_started(self, node: TaskNode) -> None:
        self._write_event("node_started", node)

    def node_complete(self, node: TaskNode, duration_ms: int) -> None:
        self._write_event("node_complete", node, duration_ms=duration_ms)

    def node_blocked(self, node: TaskNode, signals: list[DriftSignal]) -> None:
        self._write_event(
            "node_blocked",
            node,
            signal_ids=[s.id for s in signals],
        )

    def node_failed(
        self, node: TaskNode, reason: str, signal_ids: list[str]
    ) -> None:
        self._write_event(
            "node_failed", node, reason=reason, signal_ids=signal_ids
        )

    def node_awaiting_human(
        self, node: TaskNode, signals: list[UncertaintySignal]
    ) -> None:
        self._write_event(
            "node_awaiting_human",
            node,
            signal_ids=[s.id for s in signals],
        )

    # -------------------------------------------------------------------
    # Step events
    # -------------------------------------------------------------------

    def step_started(self, node: TaskNode, step: StepRecord) -> None:
        self._write_event(
            "step_started",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
        )

    def step_complete(
        self,
        node: TaskNode,
        step: StepRecord,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: int = 0,
    ) -> None:
        self._write_event(
            "step_complete",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
            duration_ms=duration_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    def step_retrying(self, node: TaskNode, step: StepRecord) -> None:
        self._write_event(
            "step_retrying",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
        )

    def step_failed(self, node: TaskNode, step: StepRecord) -> None:
        self._write_event(
            "step_failed",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
        )

    # -------------------------------------------------------------------
    # Gate events
    # -------------------------------------------------------------------

    def gate_started(self, node: TaskNode, gate: GateTemplate) -> None:
        self._write_event(
            "gate_started",
            node,
            gate_name=gate.name,
            check_type=gate.check_type,
        )

    def gate_passed(
        self, node: TaskNode, gate: GateTemplate, evidence: str
    ) -> None:
        self._write_event(
            "gate_passed",
            node,
            gate_name=gate.name,
            check_type=gate.check_type,
            evidence=evidence,
        )

    def gate_failed(
        self, node: TaskNode, gate: GateTemplate, evidence: str
    ) -> None:
        self._write_event(
            "gate_failed",
            node,
            gate_name=gate.name,
            check_type=gate.check_type,
            evidence=evidence,
        )

    # -------------------------------------------------------------------
    # Signal events
    # -------------------------------------------------------------------

    def drift_detected(
        self, node: TaskNode, step: StepRecord, signal: DriftSignal
    ) -> None:
        self._write_event(
            "drift_detected",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
            drift_type=signal.drift_type.value,
            severity=signal.severity.value,
            signal_id=signal.id,
        )

    def uncertainty_detected(
        self, node: TaskNode, step: StepRecord, signal: UncertaintySignal
    ) -> None:
        self._write_event(
            "uncertainty_detected",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
            uncertainty_type=signal.uncertainty_type.value,
            confidence=signal.confidence,
            signal_id=signal.id,
        )

    def human_resolved(
        self, signal: UncertaintySignal, resolution: Resolution
    ) -> None:
        self._write_event(
            "human_resolved",
            None,
            signal_id=signal.id,
            resolution=resolution.value,
        )

    def timeout_resolved(
        self, signal: UncertaintySignal, resolution: Resolution
    ) -> None:
        self._write_event(
            "timeout_resolved",
            None,
            signal_id=signal.id,
            resolution=resolution.value,
        )

    # -------------------------------------------------------------------
    # LLM call events
    # -------------------------------------------------------------------

    def llm_call_started(
        self,
        node: TaskNode,
        step: StepRecord,
        purpose: str,
        model: str = "",
    ) -> None:
        self._write_event(
            "llm_call_started",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
            call_purpose=purpose,
            model=model,
        )

    def llm_call_complete(
        self,
        node: TaskNode,
        step: StepRecord,
        purpose: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_ms: int = 0,
        model: str = "",
    ) -> None:
        self._write_event(
            "llm_call_complete",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
            call_purpose=purpose,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            model=model,
        )

    def llm_call_failed(
        self,
        node: TaskNode,
        step: StepRecord,
        purpose: str,
        error: str,
    ) -> None:
        self._write_event(
            "llm_call_failed",
            node,
            step=step.template.name,
            attempt=step.retry_count + 1,
            call_purpose=purpose,
            error=error,
        )

    # -------------------------------------------------------------------
    # Content log
    # -------------------------------------------------------------------

    def log_prompt(
        self,
        node: TaskNode,
        step: StepRecord,
        attempt: int,
        prompt: str,
        started_at: str = "",
    ) -> None:
        """Write the prompt section of a content log file.

        Creates the markdown file with header + prompt. The response
        section is appended later by log_response().
        """
        node_path = self._node_paths.get(node.id, node.name)
        step_index = self._step_index(node, step)

        header = (
            f"# step: {step.template.name}\n"
            f"# node: {node.name} [{node.id}]\n"
            f"# path: {node_path}\n"
            f"# node_type: {node.primitive_type.value}\n"
            f"# attempt: {attempt}\n"
            f"# started: {started_at or datetime.now(UTC).isoformat() + 'Z'}\n"
        )

        content = f"{header}\n---\n\n## Prompt\n\n{prompt}\n"
        self._write_content_file(node, step, attempt, content, mode="w")

    def log_response(
        self,
        node: TaskNode,
        step: StepRecord,
        attempt: int,
        response: str,
        signals: list[DriftSignal | UncertaintySignal] | None = None,
        completed_at: str = "",
        duration_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        outcome: str = "complete",
    ) -> None:
        """Append the response section to an existing content log file."""
        # Build completion header fields
        completion_header = (
            f"# completed: {completed_at or datetime.now(UTC).isoformat() + 'Z'}\n"
            f"# duration_ms: {duration_ms}\n"
            f"# tokens_in: {tokens_in}\n"
            f"# tokens_out: {tokens_out}\n"
            f"# outcome: {outcome}\n"
        )

        content = f"\n{completion_header}\n---\n\n## Response\n\n{response}\n"

        # Append signal section if any
        if signals:
            content += "\n---\n\n## Signals\n"
            for sig in signals:
                if isinstance(sig, DriftSignal):
                    content += (
                        f"\n### Drift detected\n\n"
                        f"type: {sig.drift_type.value}\n"
                        f"severity: {sig.severity.value}\n"
                        f"evidence: {sig.evidence}\n"
                        f"correction: {sig.correction_template}\n"
                    )
                elif isinstance(sig, UncertaintySignal):
                    content += (
                        f"\n### Uncertainty detected\n\n"
                        f"type: {sig.uncertainty_type.value}\n"
                        f"confidence: {sig.confidence}\n"
                        f"evidence: {sig.evidence}\n"
                        f"question: {sig.question}\n"
                    )

        self._write_content_file(node, step, attempt, content, mode="a")

    # -------------------------------------------------------------------
    # Internal — execution log
    # -------------------------------------------------------------------

    def _write_event(
        self, event: str, node: TaskNode | None, **kwargs: Any
    ) -> None:
        """Write a single JSONL entry to the execution log."""
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat() + "Z",
            "event": event,
            "session_id": self.session_id,
            "node_id": node.id if node else None,
            "node_path": self._node_paths.get(node.id) if node else None,
            "node_type": node.primitive_type.value if node else None,
        }

        # Add step/attempt from kwargs if present, else null
        entry["step"] = kwargs.pop("step", None)
        entry["attempt"] = kwargs.pop("attempt", None)

        # Event-specific fields
        entry.update(kwargs)

        self._exec_log.write(json.dumps(entry) + "\n")
        self._exec_log.flush()

    # -------------------------------------------------------------------
    # Internal — content log
    # -------------------------------------------------------------------

    def _step_index(self, node: TaskNode, step: StepRecord) -> int:
        """1-indexed step position within the node."""
        try:
            return node.steps.index(step) + 1
        except ValueError:
            return 0

    def _content_file_path(
        self, node: TaskNode, step: StepRecord, attempt: int
    ) -> Path:
        """Compute the content log file path per spec naming conventions.

        Directory: {node_id}_{node_name}/
        File: step_{NN}_{step_name}.md  or  step_{NN}_{step_name}_attempt{M}.md
        """
        dir_name = f"{node.id}_{node.name}"
        step_index = self._step_index(node, step)

        if attempt <= 1:
            filename = f"step_{step_index:02d}_{step.template.name}.md"
        else:
            filename = f"step_{step_index:02d}_{step.template.name}_attempt{attempt}.md"

        return self.content_log_dir / dir_name / filename

    def _write_content_file(
        self,
        node: TaskNode,
        step: StepRecord,
        attempt: int,
        content: str,
        mode: str = "w",
    ) -> None:
        """Write or append to a content log markdown file."""
        path = self._content_file_path(node, step, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, mode) as f:
            f.write(content)

    # -------------------------------------------------------------------
    # Read-back (for testing and analysis)
    # -------------------------------------------------------------------

    def read_execution_log(self) -> list[dict[str, Any]]:
        """Read all execution log entries."""
        entries: list[dict[str, Any]] = []
        if not self._exec_log_path.exists():
            return entries
        with open(self._exec_log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    @property
    def execution_log_path(self) -> Path:
        return self._exec_log_path


class NullLogger:
    """No-op logger that satisfies the ExecutionLogger interface.

    Used when no logging is desired (e.g. tests, dry-run).
    Every method is a no-op.
    """

    def register_node(self, tree: TaskTree, node: TaskNode) -> None:
        pass

    def get_node_path(self, node: TaskNode) -> str | None:
        return None

    def session_started(self, session_id: str, task: str) -> None:
        pass

    def session_complete(self, session_id: str, duration_ms: int) -> None:
        pass

    def session_failed(self, session_id: str, reason: str, signal_ids: list[str]) -> None:
        pass

    def node_started(self, node: TaskNode) -> None:
        pass

    def node_complete(self, node: TaskNode, duration_ms: int) -> None:
        pass

    def node_blocked(self, node: TaskNode, signals: list[DriftSignal]) -> None:
        pass

    def node_failed(self, node: TaskNode, reason: str, signal_ids: list[str]) -> None:
        pass

    def node_awaiting_human(self, node: TaskNode, signals: list[UncertaintySignal]) -> None:
        pass

    def step_started(self, node: TaskNode, step: StepRecord) -> None:
        pass

    def step_complete(self, node: TaskNode, step: StepRecord, tokens_in: int = 0, tokens_out: int = 0, duration_ms: int = 0) -> None:
        pass

    def step_retrying(self, node: TaskNode, step: StepRecord) -> None:
        pass

    def step_failed(self, node: TaskNode, step: StepRecord) -> None:
        pass

    def gate_started(self, node: TaskNode, gate: GateTemplate) -> None:
        pass

    def gate_passed(self, node: TaskNode, gate: GateTemplate, evidence: str) -> None:
        pass

    def gate_failed(self, node: TaskNode, gate: GateTemplate, evidence: str) -> None:
        pass

    def drift_detected(self, node: TaskNode, step: StepRecord, signal: DriftSignal) -> None:
        pass

    def uncertainty_detected(self, node: TaskNode, step: StepRecord, signal: UncertaintySignal) -> None:
        pass

    def human_resolved(self, signal: UncertaintySignal, resolution: Resolution) -> None:
        pass

    def timeout_resolved(self, signal: UncertaintySignal, resolution: Resolution) -> None:
        pass

    def llm_call_started(self, node: TaskNode, step: StepRecord, purpose: str, model: str = "") -> None:
        pass

    def llm_call_complete(self, node: TaskNode, step: StepRecord, purpose: str, tokens_in: int = 0, tokens_out: int = 0, duration_ms: int = 0, model: str = "") -> None:
        pass

    def llm_call_failed(self, node: TaskNode, step: StepRecord, purpose: str, error: str) -> None:
        pass

    def log_prompt(self, node: TaskNode, step: StepRecord, attempt: int, prompt: str, started_at: str = "") -> None:
        pass

    def log_response(self, node: TaskNode, step: StepRecord, attempt: int, response: str, signals: list | None = None, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass
