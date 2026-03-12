from __future__ import annotations

from typing import Protocol


class StepLLMClient(Protocol):
    """Minimal v2 client interface.

    The scratch-built runner asks the client for one step at a time.
    This keeps the first execution loop explicit and easy to test.
    """

    def generate(self, step_name: str, prompt: str) -> str: ...
