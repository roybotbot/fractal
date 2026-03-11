"""CLI entry point for superpowers_runner.

Usage:
    python -m superpowers_runner run "user can reset password"
    python -m superpowers_runner run --dry-run "test task"
    python -m superpowers_runner list
    python -m superpowers_runner resume <session_id>
    python -m superpowers_runner login
    python -m superpowers_runner auth-status
"""

from __future__ import annotations

import argparse
import sys

from superpowers_runner.detector.drift import DriftDetector
from superpowers_runner.detector.uncertainty import UncertaintyDetector
from superpowers_runner.notify.display import format_drift_signals, format_uncertainty_batch
from superpowers_runner.notify.notifier import Notifier
from superpowers_runner.planner.planner import Planner
from superpowers_runner.runner.runner import HumanReviewRequired, Runner, StuckSession
from superpowers_runner.schema.signals import Resolution, UncertaintySignal
from superpowers_runner.session.state import StateManager


class StubLLMClient:
    """Stub LLM client for testing / dry-run mode."""

    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str:
        return f"[STUB] Would call LLM with prompt ({len(prompt)} chars)"


def _resolve_llm_client(args: argparse.Namespace):
    """Resolve LLM client based on CLI args."""
    if getattr(args, "dry_run", False):
        return StubLLMClient()

    # Try to import and create real client
    from superpowers_runner.client.llm import LLMClient, AuthenticationError

    api_key = getattr(args, "api_key", None)
    model = getattr(args, "model", None)

    try:
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if model:
            kwargs["model"] = model
        return LLMClient(**kwargs)
    except AuthenticationError as e:
        print(f"Error: {e}")
        print()
        print("To authenticate:")
        print("  Option 1: export ANTHROPIC_API_KEY=sk-ant-...")
        print("  Option 2: python -m superpowers_runner login")
        print("  Option 3: python -m superpowers_runner run --api-key sk-ant-... 'task'")
        sys.exit(1)


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
            resolutions.append((signal.default_resolution, ""))

    return resolutions


def cmd_run(args: argparse.Namespace) -> None:
    """Run a new task."""
    task = args.task
    session_dir = args.session_dir

    llm_client = _resolve_llm_client(args)

    state_mgr = StateManager(session_dir=session_dir)
    drift_detector = DriftDetector(llm_client=llm_client)
    uncertainty_detector = UncertaintyDetector()
    notifier = Notifier(human_input=_terminal_human_input)
    planner = Planner(llm_client=llm_client)

    auth_label = ""
    if hasattr(llm_client, "auth_type"):
        auth_label = f" (auth: {llm_client.auth_type})"
    if hasattr(llm_client, "model"):
        auth_label += f" (model: {llm_client.model})"

    print(f"Task: {task}")
    print(f"Session dir: {session_dir}{auth_label}")
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
        detector=drift_detector,
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
    except HumanReviewRequired as e:
        state_mgr.save(tree)
        print(f"\n⚠ Human review required: {e}")
        print(f"  Node: {e.node.name} ({e.node.primitive_type.value})")
        for s in e.signals:
            if hasattr(s, "drift_type"):
                print(f"  Signal: {s.drift_type.value} — {s.evidence}")
            elif hasattr(s, "gate"):
                print(f"  Gate: {s.gate.name} — {s.evidence}")
        print(f"\n  Resume with: python -m superpowers_runner resume {tree.session_id}")
        sys.exit(2)
    except StuckSession as e:
        state_mgr.save(tree)
        print(f"\n✗ Session stuck: {e}")
        print(f"  Resume with: python -m superpowers_runner resume {tree.session_id}")
        sys.exit(3)
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

    llm_client = _resolve_llm_client(args)

    drift_detector = DriftDetector(llm_client=llm_client)
    uncertainty_detector = UncertaintyDetector()
    notifier = Notifier(human_input=_terminal_human_input)

    runner = Runner(
        tree=tree,
        llm_client=llm_client,
        detector=drift_detector,
        uncertainty_detector=uncertainty_detector,
        notifier=notifier,
        state_manager=state_mgr,
    )

    try:
        result = runner.run()
        print()
        print(result.summary())
    except HumanReviewRequired as e:
        state_mgr.save(tree)
        print(f"\n⚠ Human review required: {e}")
        print(f"  Resume with: python -m superpowers_runner resume {session_id}")
        sys.exit(2)
    except StuckSession as e:
        state_mgr.save(tree)
        print(f"\n✗ Session stuck: {e}")
        sys.exit(3)
    except KeyboardInterrupt:
        print("\nInterrupted. Session saved.")
        state_mgr.save(tree)
        sys.exit(130)


def cmd_login(args: argparse.Namespace) -> None:
    """Authenticate via Anthropic OAuth."""
    from superpowers_runner.client.oauth import authorize

    try:
        token = authorize()
        prefix = token[:15] + "..." if len(token) > 15 else token
        print(f"\n✓ Authenticated. Token: {prefix}")
        print("Token stored in ~/.superpowers_runner/auth.json")
    except Exception as e:
        print(f"Error during authentication: {e}")
        sys.exit(1)


def cmd_auth_status(args: argparse.Namespace) -> None:
    """Show current authentication status."""
    import os
    from superpowers_runner.client.oauth import load_tokens, is_token_expired, AUTH_FILE

    # Check env var
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        prefix = env_key[:15] + "..." if len(env_key) > 15 else env_key
        print(f"ANTHROPIC_API_KEY: {prefix}")
        if env_key.startswith("sk-ant-oat"):
            print("  Type: OAuth token (from environment)")
        else:
            print("  Type: API key")
        print("  Status: ✓ set")
        return

    # Check stored OAuth
    tokens = load_tokens()
    if tokens:
        prefix = tokens["access_token"][:15] + "..."
        expired = is_token_expired(tokens)
        print(f"OAuth token: {prefix}")
        print(f"  Source: {AUTH_FILE}")
        print(f"  Status: {'✗ expired' if expired else '✓ valid'}")
        if expired:
            print("  Run: python -m superpowers_runner login")
        return

    print("No authentication configured.")
    print()
    print("Options:")
    print("  1. export ANTHROPIC_API_KEY=sk-ant-...")
    print("  2. python -m superpowers_runner login  (OAuth via Claude.ai)")
    print("  3. Pass --api-key to run/resume commands")


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
    run_parser.add_argument("--api-key", help="Anthropic API key (overrides env/OAuth)")
    run_parser.add_argument("--model", default=None, help="Model to use (default: claude-sonnet-4-20250514)")
    run_parser.set_defaults(func=cmd_run)

    # list
    list_parser = subparsers.add_parser("list", help="List all sessions")
    list_parser.set_defaults(func=cmd_list)

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume a session")
    resume_parser.add_argument("session_id", help="Session ID to resume")
    resume_parser.add_argument("--dry-run", action="store_true", help="Use stub LLM client")
    resume_parser.add_argument("--api-key", help="Anthropic API key")
    resume_parser.add_argument("--model", default=None, help="Model to use")
    resume_parser.set_defaults(func=cmd_resume)

    # login
    login_parser = subparsers.add_parser("login", help="Authenticate via Anthropic OAuth")
    login_parser.set_defaults(func=cmd_login)

    # auth-status
    auth_status_parser = subparsers.add_parser("auth-status", help="Show authentication status")
    auth_status_parser.set_defaults(func=cmd_auth_status)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
