"""LLM client — Anthropic API with OAuth and API key support.

Two authentication modes:
1. API key: set ANTHROPIC_API_KEY env var or pass api_key directly
2. OAuth: run `python -m superpowers_runner login` to authorize via Claude.ai

OAuth tokens (sk-ant-oat) use Bearer auth; API keys use x-api-key header.
The Anthropic Python SDK handles this automatically when given the right key.
"""

from __future__ import annotations

import os
from typing import Protocol

import anthropic

from superpowers_runner.client.oauth import get_valid_token, authorize


# Default model — can be overridden per call or at client construction
DEFAULT_MODEL = "claude-sonnet-4-20250514"


class LLMClient:
    """Anthropic API client with OAuth + API key support.

    Auth resolution order:
    1. Explicit api_key parameter
    2. ANTHROPIC_API_KEY environment variable
    3. Stored OAuth token (from `login` command)

    The client satisfies the LLMClient Protocol used throughout the runner.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_retries: int = 2,
        timeout: float = 120.0,
    ) -> None:
        self._model = model
        self._api_key, self._is_oauth = self._resolve_key(api_key)

        # OAuth tokens use auth_token (Bearer), API keys use api_key (x-api-key)
        if self._is_oauth:
            self._client = anthropic.Anthropic(
                auth_token=self._api_key,
                max_retries=max_retries,
                timeout=timeout,
                default_headers={
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )
        else:
            self._client = anthropic.Anthropic(
                api_key=self._api_key,
                max_retries=max_retries,
                timeout=timeout,
            )

    def _resolve_key(self, explicit_key: str | None) -> tuple[str, bool]:
        """Resolve API key from explicit, env, or OAuth.

        Returns (key, is_oauth).
        """
        # 1. Explicit
        if explicit_key:
            return explicit_key, explicit_key.startswith("sk-ant-oat")

        # 2. Environment variable
        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key:
            return env_key, env_key.startswith("sk-ant-oat")

        # 3. OAuth token
        oauth_token = get_valid_token()
        if oauth_token:
            return oauth_token, True

        raise AuthenticationError(
            "No Anthropic API key found. Either:\n"
            "  1. Set ANTHROPIC_API_KEY environment variable\n"
            "  2. Pass api_key to LLMClient()\n"
            "  3. Run: python -m superpowers_runner login"
        )

    def call(
        self,
        prompt: str,
        max_tokens: int = 4096,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Send a message to Claude and return the text response.

        This is the main interface used by runner, planner, and detector.
        """
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": model or self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)
        return response.content[0].text

    @property
    def model(self) -> str:
        return self._model

    @property
    def auth_type(self) -> str:
        """Return the authentication type being used."""
        if self._api_key.startswith("sk-ant-oat"):
            return "oauth"
        return "api_key"


class AuthenticationError(Exception):
    """Raised when no valid authentication method is available."""
    pass
