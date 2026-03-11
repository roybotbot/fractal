"""CLI entry point for superpowers_runner.

Usage:
    python -m superpowers_runner run "user can reset password"
    python -m superpowers_runner list
    python -m superpowers_runner resume <session_id>
"""

from __future__ import annotations

import argparse
import sys
from typing import Protocol

from superpowers_runner.detector.drift import DriftDetector
from superpowers_runner.detector.uncertainty import UncertaintyDetector
from superpowers_runner.notify.display import format_drift_signals, format_uncertainty_batch
from superpowers_runner.notify.notifier import Notifier
from superpowers_runner.planner.planner import Planner
from superpowers_runner.runner.runner import Runner
from superpowers_runner.schema.signals import (
    BATCH_AND_NOTIFY,
    INTERRUPT_IMMEDIATELY,
    Resolution,
    UncertaintySignal,
)
from superpowers_runner.session.log import DriftLog
from superpowers_runner.session.state import StateManager


class LLMClient(Protocol):
    """Protocol for LLM calls — implemented by the actual provider."""

    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


class StubLLMClient:
    """Stub LLM client for testing / dry-run mode."""

    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str:
        return f"[STUB] Would call LLM with prompt ({len(prompt)} chars)"


def _terminal_human_input(
    signals: list[UncertaintySignal],
) -> list[tuple[Resolution, str]] | None:
    """Interactive terminal handler for uncertainty signals."""
    display = format_uncertainty_batch(signals)
    print(display)

    resolutions: list[tuple[Resolution, str]] = []
    for i, signal in enumerate(signals, 1):
        try:
            answer = input(f"\n  [{i}] A / B / skip: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\n  (timeout — using defaults)")
            return None

        if answer == "A":
            resolutions.append((Resolution.PROCEED, ""))
        elif answer == "B":
            if signal.default_resolution == Resolution.ESCALATE:
                resolutions.append((Resolution.ESCALATE, ""))
            else:
                resolutions.append((Resolution.RETRY, ""))
        else:
            # Skip — use default
            resolutions.append((signal.default_resolution, ""))

    return resolutions


def cmd_run(args: argparse.Namespace) -> None:
    """Run a new task."""
    task = args.task
    session_dir = args.session_dir

    if args.dry_run:
        llm_client = StubLLMClient()
    else:
        # TODO: wire up real LLM client (OpenAI, Anthropic, etc.)
        print("Error: --dry-run is required until a real LLM client is configured.")
        print("Usage: python -m superpowers_runner run --dry-run 'task description'")
        sys.exit(1)

    state_mgr = StateManager(session_dir=session_dir)
    drift_detector = DriftDetector(llm_client=llm_client)
    uncertainty_detector = UncertaintyDetector()
    notifier = Notifier(human_input=_terminal_human_input)
    planner = Planner(llm_client=llm_client)

    print(f"Task: {task}")
    print(f"Session dir: {session_dir}")
    print()

    # Step 1: Plan
    print("Planning...")
    tree = planner.plan(task)
    print(f"Created session: {tree.session_id}")
    print(f"Root node: {tree.root.name} ({tree.root.primitive_type.value})")
    print(f"Sub-nodes: {len(tree.root.sub_nodes)}")
    state_mgr.save(tree)
    print()

    # Step 2: Execute
    print("Executing...")
    runner = Runner(
        tree=tree,
        llm_client=llm_client,
        drift_detector=drift_detector,
        uncertainty_detector=uncertainty_detector,
        notifier=notifier,
        state_manager=state_mgr,
    )

    try:
        result = runner.run()
        print()
        print(result.summary())
        print()
        if result.is_complete():
            print("✓ Task complete.")
        else:
            print("✗ Task incomplete.")
    except KeyboardInterrupt:
        print("\nInterrupted. Session saved.")
        state_mgr.save(tree)
        sys.exit(130)


def cmd_list(args: argparse.Namespace) -> None:
    """List all sessions."""
    state_mgr = StateManager(session_dir=args.session_dir)
    sessions = state_mgr.list_sessions()

    if not sessions:
        print("No sessions found.")
        return

    # Header
    print(f"{'Session ID':<30} {'Task':<35} {'Status':<15} {'Last save'}")
    print("-" * 100)

    for s in sessions:
        sid = s.get("session_id", "?")[:28]
        task = s.get("task", "")[:33]
        status = s.get("status", "?")
        last_save = s.get("last_save", "?")
        if isinstance(last_save, str) and len(last_save) > 19:
            last_save = last_save[:19]
        print(f"{sid:<30} {task:<35} {status:<15} {last_save}")


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume an existing session."""
    session_id = args.session_id
    session_dir = args.session_dir

    state_mgr = StateManager(session_dir=session_dir)

    if not state_mgr.session_exists(session_id):
        print(f"Error: Session '{session_id}' not found.")
        sys.exit(1)

    tree = state_mgr.load(session_id)
    print(f"Resuming session: {session_id}")
    print(tree.summary())

    if args.dry_run:
        llm_client = StubLLMClient()
    else:
        print("Error: --dry-run is required until a real LLM client is configured.")
        sys.exit(1)

    drift_detector = DriftDetector(llm_client=llm_client)
    uncertainty_detector = UncertaintyDetector()
    notifier = Notifier(human_input=_terminal_human_input)

    runner = Runner(
        tree=tree,
        llm_client=llm_client,
        drift_detector=drift_detector,
        uncertainty_detector=uncertainty_detector,
        notifier=notifier,
        state_manager=state_mgr,
    )

    try:
        result = runner.run()
        print()
        print(result.summary())
    except KeyboardInterrupt:
        print("\nInterrupted. Session saved.")
        state_mgr.save(tree)
        sys.exit(130)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="superpowers_runner",
        description="Fractal LLM task execution engine",
    )
    parser.add_argument(
        "--session-dir",
        default="sessions",
        help="Directory for session data (default: sessions)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run
    run_parser = subparsers.add_parser("run", help="Run a new task")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument("--dry-run", action="store_true", help="Use stub LLM client")
    run_parser.set_defaults(func=cmd_run)

    # list
    list_parser = subparsers.add_parser("list", help="List all sessions")
    list_parser.set_defaults(func=cmd_list)

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume a session")
    resume_parser.add_argument("session_id", help="Session ID to resume")
    resume_parser.add_argument("--dry-run", action="store_true", help="Use stub LLM client")
    resume_parser.set_defaults(func=cmd_resume)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
