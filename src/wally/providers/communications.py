"""Communications provider protocol."""

from __future__ import annotations

from typing import Protocol

from wally.models.communications import (
    AvailabilityResult,
    CalendarCreateResult,
    CalendarEvent,
    EmailDraftResult,
    EmailMessage,
    EmailSendResult,
    EmailSummary,
)
from wally.providers.capability import CapabilityProvider


class CommunicationsProvider(CapabilityProvider, Protocol):
    """Email and calendar transport for the personal Chief of Staff.

    Institutional contact details (property managers, utilities, banks, etc.)
    belong in the Knowledge Provider (Notion), not here. A future People
    provider will aggregate personal contacts from multiple sources.
    """

    def search_email(
        self, *, query: str = "", unread_only: bool = False, limit: int = 10
    ) -> list[EmailSummary]:
        """Search or list emails."""
        ...

    def get_email(self, message_id: str) -> EmailMessage:
        """Fetch a single email by ID."""
        ...

    def draft_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailDraftResult:
        """Create a draft email (does not send)."""
        ...

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailSendResult:
        """Send an email immediately."""
        ...

    def list_calendar_events(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> list[CalendarEvent]:
        """List calendar events in a time range (ISO 8601 bounds)."""
        ...

    def check_availability(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> AvailabilityResult:
        """Return busy blocks for the calendar in a time range."""
        ...

    def create_calendar_event(
        self,
        *,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        calendar_id: str | None = None,
    ) -> CalendarCreateResult:
        """Create a calendar event."""
        ...
