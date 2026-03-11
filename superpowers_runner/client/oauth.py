"""Anthropic OAuth — PKCE authorization code flow.

Implements the same flow pi uses: authorize via claude.ai, exchange code
at console.anthropic.com, get back an sk-ant-oat token that works as a
Bearer token against api.anthropic.com.

Tokens stored in ~/.superpowers_runner/auth.json with 600 permissions.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import webbrowser
from base64 import urlsafe_b64encode
from pathlib import Path
from urllib.parse import urlencode

import httpx


# OAuth configuration
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference"

# Token storage
AUTH_DIR = Path.home() / ".superpowers_runner"
AUTH_FILE = AUTH_DIR / "auth.json"

# Safety buffer: consider token expired 5 minutes early
EXPIRY_BUFFER_MS = 5 * 60 * 1000


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge_bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize() -> str:
    """Run the OAuth authorization flow. Returns an access token.

    Opens the browser for user authorization, prompts for the callback
    code, exchanges it for tokens, and stores them.
    """
    verifier, challenge = _generate_pkce()

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }

    auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    print("Opening browser for Anthropic authorization...")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    callback = input("Paste the code#state from the callback page: ").strip()

    if "#" in callback:
        code, state = callback.split("#", 1)
    else:
        code = callback
        state = verifier

    tokens = exchange_code(code, state, verifier)
    save_tokens(tokens)
    return tokens["access_token"]


def exchange_code(code: str, state: str, verifier: str) -> dict:
    """Exchange authorization code for tokens."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }

    response = httpx.post(TOKEN_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    expires_ms = int(time.time() * 1000) + (data["expires_in"] * 1000) - EXPIRY_BUFFER_MS

    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires": expires_ms,
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Refresh an expired access token."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }

    response = httpx.post(TOKEN_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    expires_ms = int(time.time() * 1000) + (data["expires_in"] * 1000) - EXPIRY_BUFFER_MS

    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires": expires_ms,
    }


def save_tokens(tokens: dict) -> None:
    """Save tokens to auth file with restricted permissions."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)

    auth_data = {}
    if AUTH_FILE.exists():
        with open(AUTH_FILE) as f:
            auth_data = json.load(f)

    auth_data["anthropic_oauth"] = {
        "type": "oauth",
        "access": tokens["access_token"],
        "refresh": tokens["refresh_token"],
        "expires": tokens["expires"],
    }

    with open(AUTH_FILE, "w") as f:
        json.dump(auth_data, f, indent=2)
    os.chmod(AUTH_FILE, 0o600)


def load_tokens() -> dict | None:
    """Load stored OAuth tokens. Returns None if not found."""
    if not AUTH_FILE.exists():
        return None

    with open(AUTH_FILE) as f:
        auth_data = json.load(f)

    oauth = auth_data.get("anthropic_oauth")
    if not oauth:
        return None

    return {
        "access_token": oauth["access"],
        "refresh_token": oauth["refresh"],
        "expires": oauth["expires"],
    }


def is_token_expired(tokens: dict) -> bool:
    """Check if the access token has expired."""
    now_ms = int(time.time() * 1000)
    return now_ms >= tokens["expires"]


def get_valid_token() -> str | None:
    """Get a valid access token, refreshing if needed.

    Returns None if no tokens are stored.
    """
    tokens = load_tokens()
    if tokens is None:
        return None

    if is_token_expired(tokens):
        try:
            tokens = refresh_access_token(tokens["refresh_token"])
            save_tokens(tokens)
        except httpx.HTTPError:
            return None

    return tokens["access_token"]
