from __future__ import annotations

from pathlib import Path

from superpowers_runner_v2.artifacts import ArtifactWriter, extract_fenced_code
from superpowers_runner_v2.client import StepLLMClient
from superpowers_runner_v2.gates import check_no_io, run_pytest
from superpowers_runner_v2.logger import ExecutionLogger
from superpowers_runner_v2.schema import V2Session
from superpowers_runner_v2.state import StateManager


class Runner:
    """Minimal vertical-slice runner for the scratch-built v2 package.

    Design constraints for this first version:
    - one task shape: a single transformation
    - one execution strategy: fixed step order
    - one retry path: implementation step retries once if gates fail
    - one artifact model: implementation file + test file
    """

    def __init__(
        self,
        llm_client: StepLLMClient,
        state_manager: StateManager,
        logger: ExecutionLogger,
        base_dir: str | Path = "sessions_v2",
    ) -> None:
        self.llm_client = llm_client
        self.state_manager = state_manager
        self.logger = logger
        self.artifacts = ArtifactWriter(base_dir=base_dir)

    def run(self, session: V2Session) -> V2Session:
        """Execute the fixed transformation step sequence.

        The runner persists after each completed step so the session can be
        resumed from the last durable checkpoint later.

        Before resuming execution, we rebuild any missing durable artifacts
        from already-completed steps. This keeps resume behavior aligned with
        the original run: later gated steps can rely on earlier outputs.
        """
        self.logger.log_event("session_started", task=session.task_prompt)
        self._restore_completed_artifacts(session)

        for step in session.task.steps:
            if step.status.value == "complete":
                continue

            if step.name == "implement_minimal":
                self._run_implementation_step(session, step)
            else:
                self._run_simple_step(session, step)

            self.state_manager.save(session)

        self.logger.log_event("session_complete", task_name=session.task.name)
        return session

    def _run_simple_step(self, session: V2Session, step) -> None:
        """Run a non-gated step exactly once.

        For the first v2 slice, only the test-writing step produces an artifact.
        The other planning and refactor steps just persist their text output.
        """
        attempt = step.attempt + 1
        prompt = self._build_prompt(session.task.name, step.name)
        self.logger.log_event("step_started", step=step.name, attempt=attempt)
        response = self.llm_client.generate(step.name, prompt)
        self.logger.log_step_content(session.task.name, step.name, attempt, prompt, response)

        step.output = response
        step.attempt = attempt
        step.status = step.status.__class__.COMPLETE

        if step.name == "write_failing_tests":
            self.artifacts.write_test(
                session_id=session.session_id,
                task_name=session.task.name,
                content=extract_fenced_code(response),
            )

        self.logger.log_event("step_complete", step=step.name, attempt=attempt)

    def _run_implementation_step(self, session: V2Session, step) -> None:
        """Run the implementation step with one gate-driven retry.

        The first written implementation is checked immediately against:
        - no-I/O purity
        - pytest on the generated test artifact

        If either gate fails, the step is retried once. This keeps the first
        vertical slice simple while still proving retry behavior is real.
        """
        max_attempts = 2
        while step.attempt < max_attempts:
            attempt = step.attempt + 1
            prompt = self._build_prompt(session.task.name, step.name)
            self.logger.log_event("step_started", step=step.name, attempt=attempt)
            response = self.llm_client.generate(step.name, prompt)
            self.logger.log_step_content(session.task.name, step.name, attempt, prompt, response)

            code = extract_fenced_code(response)
            implementation_path = self.artifacts.write_implementation(
                session_id=session.session_id,
                task_name=session.task.name,
                content=code,
            )
            test_path = self._test_path(session.session_id, session.task.name)

            # Gate 1: the transformation must remain pure.
            no_io_result = check_no_io(code)
            self._log_gate("no_io", no_io_result.passed, no_io_result.evidence)

            # Gate 2: the generated tests must pass against the written file.
            pytest_result = run_pytest(test_path)
            self._log_gate("pytest", pytest_result.passed, pytest_result.evidence)

            step.output = response
            step.attempt = attempt

            if no_io_result.passed and pytest_result.passed:
                step.status = step.status.__class__.COMPLETE
                self.logger.log_event("step_complete", step=step.name, attempt=attempt)
                return

        raise RuntimeError("implement_minimal failed its gates twice")

    def _restore_completed_artifacts(self, session: V2Session) -> None:
        """Rebuild files from completed steps before resuming unfinished work.

        The first v2 slice only has two durable artifact-producing steps:
        - write_failing_tests -> test file
        - implement_minimal -> implementation file

        Recreating those files on resume keeps gate execution deterministic.
        """
        for step in session.task.steps:
            if step.status.value != "complete" or not step.output:
                continue

            if step.name == "write_failing_tests":
                self.artifacts.write_test(
                    session_id=session.session_id,
                    task_name=session.task.name,
                    content=extract_fenced_code(step.output),
                )
            elif step.name == "implement_minimal":
                self.artifacts.write_implementation(
                    session_id=session.session_id,
                    task_name=session.task.name,
                    content=extract_fenced_code(step.output),
                )

    def _log_gate(self, gate_name: str, passed: bool, evidence: str) -> None:
        event = "gate_passed" if passed else "gate_failed"
        self.logger.log_event(event, gate=gate_name, evidence=evidence)

    def _build_prompt(self, task_name: str, step_name: str) -> str:
        # Keep the first prompt builder deliberately small and obvious.
        return f"Task: {task_name}\nStep: {step_name}"

    def _test_path(self, session_id: str, task_name: str) -> Path:
        return self.artifacts.base_dir / session_id / "artifacts" / f"test_{task_name}.py"
