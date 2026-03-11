"""Classifier — constrained LLM call that maps a task to a PrimitiveType.

Output is validated against the enum. If it doesn't match, it retries
up to 3 times. If all retries fail, raises ClassificationFailure.

Depends on: schema layer only.
"""

from __future__ import annotations

from typing import Protocol

from superpowers_runner.schema.primitives import PrimitiveType


class LLMClient(Protocol):
    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str: ...


class ClassificationFailure(Exception):
    """Raised when the classifier cannot map a task to a PrimitiveType."""

    def __init__(self, task: str, attempts: list[str]) -> None:
        self.task = task
        self.attempts = attempts
        super().__init__(
            f"Failed to classify task after {len(attempts)} attempts. "
            f"Responses: {attempts}"
        )


# The classification prompt — presents all types with one-sentence descriptions
# and asks for exactly the type string. No explanation. No hedging.
CLASSIFICATION_PROMPT = """\
You are classifying a programming task into exactly one of the following types.
Return only the type string — no explanation.

Types:
  data_model       — schema, class, struct, or type definition with validation
  interface        — contract between two systems, no implementation
  config           — runtime parameters, environment variables, feature flags
  transformation   — pure function: input → output, no side effects
  aggregation      — reduce many to one, no side effects
  validation       — input → bool + errors, no side effects
  query            — read from external state, no writes
  mutation         — write to external state
  event_emit       — fire a signal, no response expected
  event_handler    — react to an incoming event
  pipeline         — ordered chain of operations
  router           — conditional dispatch to handlers
  orchestration    — stateful multi-step coordination with rollback
  cache            — memoize a query or transformation
  auth_guard       — enforce identity/permission
  retry_policy     — failure recovery wrapper
  observer         — logging, metrics, tracing wrapper
  unit_test        — verify a single node in isolation
  integration_test — verify multiple nodes together
  contract_test    — verify an interface is honored
  fixture          — test data factory

Task: {task}

Type:"""


MAX_CLASSIFICATION_RETRIES = 3


def classify(llm_client: LLMClient, task_description: str) -> PrimitiveType:
    """Classify a task description into a PrimitiveType.

    Makes a constrained LLM call. The response must be exactly one of the
    PrimitiveType value strings. Retries up to MAX_CLASSIFICATION_RETRIES
    times on invalid responses.

    Raises:
        ClassificationFailure: If all retries produce invalid type strings.
    """
    valid_values = {t.value for t in PrimitiveType}
    prompt = CLASSIFICATION_PROMPT.format(task=task_description)
    attempts: list[str] = []

    for _ in range(MAX_CLASSIFICATION_RETRIES):
        response = llm_client.call(prompt, max_tokens=50)
        cleaned = response.strip().lower().strip(".")

        attempts.append(cleaned)

        if cleaned in valid_values:
            return PrimitiveType(cleaned)

        # Some models wrap in quotes or add extra words — try to extract
        for value in valid_values:
            if value in cleaned:
                return PrimitiveType(value)

    raise ClassificationFailure(task_description, attempts)
