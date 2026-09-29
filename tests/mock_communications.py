"""Mock communications provider for tests."""

from __future__ import annotations

import json
from typing import Any

from wally.models.actions import PlannedAction
from wally.models.communications import (
    AvailabilityResult,
    AvailabilitySlot,
    CalendarCreateResult,
    CalendarEvent,
    EmailDraftResult,
    EmailMessage,
    EmailSendResult,
    EmailSummary,
)


class MockCommunicationsProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.created_events: list[dict[str, object]] = []
        self.drafts: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "communications"

    def is_healthy(self) -> bool:
        return True

    def search_email(
        self, *, query: str = "", unread_only: bool = False, limit: int = 10
    ) -> list[EmailSummary]:
        return [
            EmailSummary(
                message_id="msg-1",
                thread_id="thread-1",
                subject="Property inspection",
                sender="manager@example.com",
                date="Mon, 1 Jun 2026 10:00:00 +0800",
                snippet="Please confirm the inspection time.",
                labels=("UNREAD",),
            )
        ]

    def get_email(self, message_id: str) -> EmailMessage:
        return EmailMessage(
            message_id=message_id,
            thread_id="thread-1",
            subject="Property inspection",
            sender="manager@example.com",
            to="you@example.com",
            date="Mon, 1 Jun 2026 10:00:00 +0800",
            body="Please confirm the inspection time.",
        )

    def draft_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailDraftResult:
        self.drafts.append(
            {"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to}
        )
        return EmailDraftResult(draft_id="draft-1", message_id="msg-draft-1")

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailSendResult:
        self.sent.append(
            {"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to}
        )
        return EmailSendResult(message_id="msg-sent-1", thread_id="thread-1")

    def list_calendar_events(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                event_id="evt-1",
                summary="Team sync",
                start=start,
                end=end,
            )
        ]

    def check_availability(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> AvailabilityResult:
        return AvailabilityResult(
            calendar_id=calendar_id or "primary",
            slots=[AvailabilitySlot(start=start, end=end, busy=True)],
        )

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
        self.created_events.append(
            {
                "summary": summary,
                "start": start,
                "end": end,
                "attendees": attendees or [],
            }
        )
        return CalendarCreateResult(event_id="evt-new", html_link="https://calendar.example/event")

    def tool_definitions(self) -> list[dict[str, Any]]:
        from wally.adapters.google.adapter import GoogleCommunicationsAdapter

        return GoogleCommunicationsAdapter(
            client_id="x",
            client_secret="y",
            refresh_token="z",
        ).tool_definitions()

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        from wally.adapters.google.adapter import GoogleCommunicationsAdapter

        return GoogleCommunicationsAdapter(
            client_id="x",
            client_secret="y",
            refresh_token="z",
        ).planned_action_for_tool(tool_name, arguments)

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "communications_email_search":
            emails = self.search_email(
                query=str(arguments.get("query", "")),
                unread_only=bool(arguments.get("unread_only", False)),
                limit=int(arguments.get("limit", 10)),
            )
            return json.dumps(
                {"emails": [{"message_id": e.message_id, "subject": e.subject} for e in emails]}
            )
        if tool_name == "communications_email_send":
            sent = self.send_email(
                to=str(arguments["to"]),
                subject=str(arguments["subject"]),
                body=str(arguments["body"]),
            )
            return json.dumps({"message_id": sent.message_id, "status": "sent"})
        if tool_name == "communications_calendar_list":
            events = self.list_calendar_events(
                start=str(arguments["start"]),
                end=str(arguments["end"]),
            )
            return json.dumps({"events": [{"summary": e.summary} for e in events]})
        raise ValueError(f"Unknown tool: {tool_name}")
