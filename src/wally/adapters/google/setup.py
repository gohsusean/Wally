"""Attended OAuth repair; credentials stay in process and the ignored .env."""

import logging
from pathlib import Path

from dotenv import set_key
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

from wally.adapters.google.auth import DEFAULT_SCOPES


def repair_oauth(settings, env_path: Path, *, port=0):
    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError("Configure the existing Google client in the local .env first.")
    original = env_path.read_bytes()
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=list(DEFAULT_SCOPES),
        autogenerate_code_verifier=True,
    )
    previous_logging = logging.root.manager.disable
    # The library's callback/debug logs can contain authorization codes or tokens.
    logging.disable(logging.CRITICAL)
    try:
        creds = flow.run_local_server(
            host="127.0.0.1",
            port=port,
            timeout_seconds=600,
            authorization_prompt_message="",
            success_message="Wally authorization received. You may close this window.",
            access_type="offline",
            prompt="consent",
        )
        if not creds.refresh_token:
            raise ValueError("Google did not grant offline access.")
        creds.refresh(Request())
        granted = creds.granted_scopes
        if isinstance(granted, str):
            granted = granted.split()
        if not granted or not set(DEFAULT_SCOPES).issubset(granted):
            raise ValueError("Google did not grant all required scopes.")
    finally:
        logging.disable(previous_logging)
    if env_path.read_bytes() != original:
        raise ValueError("Local configuration changed during consent; existing token preserved.")
    # python-dotenv replaces atomically, preserving other lines. Never emit tokens.
    set_key(env_path, "GOOGLE_REFRESH_TOKEN", creds.refresh_token, quote_mode="always")
    env_path.chmod(0o600)
