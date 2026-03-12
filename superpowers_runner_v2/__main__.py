from __future__ import annotations

import argparse

from superpowers_runner_v2.logger import ExecutionLogger
from superpowers_runner_v2.planner import Planner
from superpowers_runner_v2.runner import Runner
from superpowers_runner_v2.state import StateManager


class DryRunClient:
    """Deterministic client used by the v2 CLI for local end-to-end checks.

    The goal is not realism; it is to drive the narrow vertical slice through
    planning, artifact writing, gate execution, and resume behavior.
    """

    def __init__(self) -> None:
        self._responses: dict[str, list[str]] = {
            "define_input_schema": ["celsius: float"],
            "define_output_schema": ["fahrenheit: float"],
            "enumerate_edge_cases": ["- freezing point\n- boiling point\n- negative values"],
            "write_failing_tests": [
                """
```python
from celsius_to_fahrenheit import celsius_to_fahrenheit


def test_freezing_point() -> None:
    assert celsius_to_fahrenheit(0) == 32


def test_boiling_point() -> None:
    assert celsius_to_fahrenheit(100) == 212


def test_negative_point() -> None:
    assert celsius_to_fahrenheit(-40) == -40
```
""".strip()
            ],
            "implement_minimal": [
                """
```python
def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32
```
""".strip()
            ],
            "refactor": ["nothing needed"],
        }

    def generate(self, step_name: str, prompt: str) -> str:
        return self._responses[step_name].pop(0)


def cmd_run(args: argparse.Namespace) -> int:
    planner = Planner()
    state = StateManager(base_dir=args.base_dir)
    session = planner.plan(args.task)
    logger = ExecutionLogger(base_dir=args.base_dir, session_id=session.session_id)
    runner = Runner(
        llm_client=DryRunClient(),
        state_manager=state,
        logger=logger,
        base_dir=args.base_dir,
        stop_after_step=args.stop_after_step,
    )

    print(f"Session: {session.session_id}")
    runner.run(session)
    logger.close()
    print("Task complete")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    state = StateManager(base_dir=args.base_dir)
    session = state.load(args.session_id)
    logger = ExecutionLogger(base_dir=args.base_dir, session_id=session.session_id)
    runner = Runner(
        llm_client=DryRunClient(),
        state_manager=state,
        logger=logger,
        base_dir=args.base_dir,
        stop_after_step=None,
    )

    print(f"Resumed: {session.session_id}")
    runner.run(session)
    logger.close()
    print("Task complete")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="superpowers_runner_v2")
    parser.add_argument("--base-dir", default="sessions_v2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("task")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--stop-after-step", default=None)
    run_parser.set_defaults(func=cmd_run)

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("--dry-run", action="store_true")
    resume_parser.set_defaults(func=cmd_resume)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
