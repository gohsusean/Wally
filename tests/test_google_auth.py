"""Google OAuth scope and refresh tests."""

from unittest.mock import patch

from google.auth.exceptions import RefreshError

from wally.adapters.google.adapter import GoogleCommunicationsAdapter
from wally.adapters.google.auth import CALENDAR_SCOPES, DEFAULT_SCOPES, GMAIL_SCOPES


def test_gmail_scopes_are_least_privilege() -> None:
    assert GMAIL_SCOPES == (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.send",
    )


def test_calendar_scopes_cover_events_only() -> None:
    assert CALENDAR_SCOPES == ("https://www.googleapis.com/auth/calendar.events",)


def test_no_people_scopes() -> None:
    assert not any("contacts" in scope or "people" in scope for scope in DEFAULT_SCOPES)


def test_is_healthy_false_on_refresh_error() -> None:
    adapter = GoogleCommunicationsAdapter(
        client_id="id",
        client_secret="secret",
        refresh_token="bad-token",
    )
    with patch(
        "wally.adapters.google.adapter.credentials_from_refresh_token",
        side_effect=RefreshError("invalid_grant: Bad Request"),
    ):
        assert adapter.is_healthy() is False
