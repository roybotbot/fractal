"""Tests for client/oauth.py and client/llm.py."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from superpowers_runner.client.oauth import (
    AUTH_FILE,
    EXPIRY_BUFFER_MS,
    _generate_pkce,
    exchange_code,
    is_token_expired,
    load_tokens,
    save_tokens,
)
from superpowers_runner.client.llm import AuthenticationError, LLMClient


# ============================================================================
# OAuth — PKCE generation
# ============================================================================


class TestPKCE:
    def test_generates_verifier_and_challenge(self):
        verifier, challenge = _generate_pkce()
        assert len(verifier) > 20
        assert len(challenge) > 20
        assert verifier != challenge

    def test_generates_unique_pairs(self):
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2
        assert c1 != c2

    def test_verifier_is_url_safe(self):
        verifier, challenge = _generate_pkce()
        # No padding, no +, no /
        assert "=" not in verifier
        assert "+" not in verifier
        assert "/" not in verifier


# ============================================================================
# OAuth — token storage
# ============================================================================


class TestTokenStorage:
    def test_save_and_load(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_FILE", auth_file)
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_DIR", tmp_path)

        tokens = {
            "access_token": "sk-ant-oat01-test-access",
            "refresh_token": "sk-ant-ort01-test-refresh",
            "expires": int(time.time() * 1000) + 3600000,
        }
        save_tokens(tokens)
        assert auth_file.exists()

        loaded = load_tokens()
        assert loaded is not None
        assert loaded["access_token"] == "sk-ant-oat01-test-access"
        assert loaded["refresh_token"] == "sk-ant-ort01-test-refresh"

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_FILE", auth_file)
        assert load_tokens() is None

    def test_load_returns_none_when_no_oauth_key(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_FILE", auth_file)
        with open(auth_file, "w") as f:
            json.dump({"other_stuff": {}}, f)
        assert load_tokens() is None

    def test_file_permissions(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_FILE", auth_file)
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_DIR", tmp_path)

        save_tokens({
            "access_token": "test",
            "refresh_token": "test",
            "expires": 0,
        })
        mode = oct(auth_file.stat().st_mode & 0o777)
        assert mode == "0o600"

    def test_save_preserves_other_data(self, tmp_path, monkeypatch):
        auth_file = tmp_path / "auth.json"
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_FILE", auth_file)
        monkeypatch.setattr("superpowers_runner.client.oauth.AUTH_DIR", tmp_path)

        # Pre-existing data
        with open(auth_file, "w") as f:
            json.dump({"other_provider": {"key": "value"}}, f)

        save_tokens({
            "access_token": "new",
            "refresh_token": "new",
            "expires": 0,
        })

        with open(auth_file) as f:
            data = json.load(f)
        assert "other_provider" in data
        assert "anthropic_oauth" in data


# ============================================================================
# OAuth — token expiry
# ============================================================================


class TestTokenExpiry:
    def test_valid_token(self):
        tokens = {"expires": int(time.time() * 1000) + 3600000}
        assert not is_token_expired(tokens)

    def test_expired_token(self):
        tokens = {"expires": int(time.time() * 1000) - 1000}
        assert is_token_expired(tokens)

    def test_just_expired(self):
        tokens = {"expires": int(time.time() * 1000) - 1}
        assert is_token_expired(tokens)


# ============================================================================
# LLMClient — authentication resolution
# ============================================================================


class TestLLMClientAuth:
    def test_explicit_api_key(self):
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            client = LLMClient(api_key="sk-ant-test-key")
            assert client.auth_type == "api_key"

    def test_oauth_token_detected(self):
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic, \
             patch("superpowers_runner.client.llm.get_valid_token", return_value="sk-ant-oat01-test"):
            client = LLMClient()
            assert client.auth_type == "oauth"

    def test_env_var_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key")
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            client = LLMClient()
            assert client.auth_type == "api_key"

    def test_no_auth_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("superpowers_runner.client.llm.get_valid_token", return_value=None), \
             patch("superpowers_runner.client.llm.anthropic"):
            with pytest.raises(AuthenticationError):
                LLMClient()

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key")
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            client = LLMClient(api_key="sk-ant-explicit-key")
            # Should use explicit, not env
            mock_anthropic.Anthropic.assert_called_once()
            call_kwargs = mock_anthropic.Anthropic.call_args
            assert call_kwargs.kwargs["api_key"] == "sk-ant-explicit-key"


# ============================================================================
# LLMClient — call interface
# ============================================================================


class TestLLMClientCall:
    def test_call_returns_text(self):
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Hello from Claude")]
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_response

            client = LLMClient(api_key="sk-ant-test")
            result = client.call("Hello")
            assert result == "Hello from Claude"

    def test_call_passes_system(self):
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="ok")]
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_response

            client = LLMClient(api_key="sk-ant-test")
            client.call("Hello", system="Be helpful")

            create_kwargs = mock_anthropic.Anthropic.return_value.messages.create.call_args.kwargs
            assert create_kwargs["system"] == "Be helpful"

    def test_call_with_custom_model(self):
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="ok")]
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_response

            client = LLMClient(api_key="sk-ant-test", model="claude-opus-4-6")
            client.call("Hello")

            create_kwargs = mock_anthropic.Anthropic.return_value.messages.create.call_args.kwargs
            assert create_kwargs["model"] == "claude-opus-4-6"

    def test_call_model_override(self):
        with patch("superpowers_runner.client.llm.anthropic") as mock_anthropic:
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="ok")]
            mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_response

            client = LLMClient(api_key="sk-ant-test", model="claude-sonnet-4-6-20250311")
            client.call("Hello", model="claude-opus-4-6")

            create_kwargs = mock_anthropic.Anthropic.return_value.messages.create.call_args.kwargs
            assert create_kwargs["model"] == "claude-opus-4-6"

    def test_default_model(self):
        with patch("superpowers_runner.client.llm.anthropic"):
            client = LLMClient(api_key="sk-ant-test")
            assert client.model == "claude-sonnet-4-6-20250311"


# ============================================================================
# CLI auth commands
# ============================================================================


class TestCLIAuth:
    def test_auth_status_no_auth(self, monkeypatch):
        import subprocess
        import sys

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "auth-status"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
            env={**os.environ, "ANTHROPIC_API_KEY": ""},
        )
        # Should show "No authentication configured" or env var info
        assert result.returncode == 0

    def test_login_help(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "login", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert result.returncode == 0
        assert "login" in result.stdout.lower()

    def test_run_with_api_key_flag(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "superpowers_runner", "run", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/roy/Projects/fractal",
        )
        assert "--api-key" in result.stdout
        assert "--model" in result.stdout
