"""Google OAuth2 token refresh for Workspace APIs.

Scopes follow least-privilege for v0.6:
- gmail.readonly — read messages
- gmail.compose — create/update drafts (no send)
- gmail.send — send messages (approval-gated in Wally)
- calendar.events — read and create/update events (no calendar settings access)
"""

from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
)
CALENDAR_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
)

DEFAULT_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES


def credentials_from_refresh_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(scopes),
    )
    creds.refresh(Request())
    return creds
