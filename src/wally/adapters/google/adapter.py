"""Google Workspace adapter for CommunicationsProvider."""

from __future__ import annotations

import base64
import json
from email.mime.text import MIMEText
from typing import Any

import httpx
from google.auth.exceptions import RefreshError

from wally.adapters.google.auth import credentials_from_refresh_token
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ActionClass, PlannedAction
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

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

_COMMUNICATIONS_TOOL_ACTIONS: dict[str, str] = {
    "communications_email_search": "email_search",
    "communications_email_get": "email_get",
    "communications_email_draft": "email_draft",
    "communications_email_send": "email_send",
    "communications_calendar_list": "calendar_list",
    "communications_calendar_availability": "calendar_availability",
    "communications_calendar_create": "calendar_create",
}

_COMMUNICATIONS_ACTION_CLASS: dict[str, ActionClass] = {
    "email_search": ActionClass.READ,
    "email_get": ActionClass.READ,
    "email_draft": ActionClass.REVERSIBLE,
    "email_send": ActionClass.IRREVERSIBLE,
    "calendar_list": ActionClass.READ,
    "calendar_availability": ActionClass.READ,
    "calendar_create": ActionClass.IRREVERSIBLE,
}


def _header_value(headers: list[dict[str, str]], name: str) -> str:
    target = name.lower()
    for header in headers:
        if header.get("name", "").lower() == target:
            return header.get("value", "")
    return ""


def _decode_body(payload: dict[str, Any]) -> str:
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data and mime_type.startswith("text/"):
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text
    return ""


def _encode_rfc822(
    *,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
) -> str:
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


class GoogleCommunicationsAdapter:
    """Google Gmail and Calendar implementation."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        calendar_id: str = "primary",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._calendar_id = calendar_id
        self._client = httpx.Client(timeout=30.0)

    @property
    def name(self) -> str:
        return "communications"

    def _access_token(self) -> str:
        try:
            creds = credentials_from_refresh_token(
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token=self._refresh_token,
            )
        except RefreshError as exc:
            raise ProviderUnavailableError(
                self.name,
                "Google OAuth refresh failed — refresh token is invalid, expired, or "
                "was issued for different scopes. Re-run scripts/google_auth.py and "
                "update GOOGLE_REFRESH_TOKEN in .env.",
            ) from exc
        if not creds.token:
            raise ProviderUnavailableError(
                self.name, "Could not refresh Google access token."
            )
        return creds.token

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._access_token()
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                self.name, f"Google API request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise ProviderUnavailableError(
                self.name,
                f"Google API error {response.status_code}: {response.text}",
            )
        if not response.content:
            return {}
        return response.json()

    def is_healthy(self) -> bool:
        if not all([self._client_id, self._client_secret, self._refresh_token]):
            return False
        try:
            self._request("GET", f"{GMAIL_API}/users/me/profile")
            return True
        except ProviderUnavailableError:
            return False

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        action = _COMMUNICATIONS_TOOL_ACTIONS.get(tool_name)
        if action is None:
            return None
        action_class = _COMMUNICATIONS_ACTION_CLASS.get(action, ActionClass.READ)
        return PlannedAction("communications", action, arguments, action_class)

    def search_email(
        self, *, query: str = "", unread_only: bool = False, limit: int = 10
    ) -> list[EmailSummary]:
        q_parts: list[str] = []
        if unread_only:
            q_parts.append("is:unread")
        if query.strip():
            q_parts.append(query.strip())
        gmail_query = " ".join(q_parts)

        params: dict[str, Any] = {"maxResults": max(1, min(limit, 50))}
        if gmail_query:
            params["q"] = gmail_query

        listing = self._request(
            "GET", f"{GMAIL_API}/users/me/messages", params=params
        )
        summaries: list[EmailSummary] = []
        for item in listing.get("messages", []):
            message_id = item["id"]
            detail = self._request(
                "GET",
                f"{GMAIL_API}/users/me/messages/{message_id}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            headers = detail.get("payload", {}).get("headers", [])
            summaries.append(
                EmailSummary(
                    message_id=message_id,
                    thread_id=detail.get("threadId", ""),
                    subject=_header_value(headers, "Subject") or "(no subject)",
                    sender=_header_value(headers, "From") or "unknown",
                    date=_header_value(headers, "Date") or "",
                    snippet=detail.get("snippet", ""),
                    labels=tuple(detail.get("labelIds", [])),
                )
            )
        return summaries

    def get_email(self, message_id: str) -> EmailMessage:
        detail = self._request(
            "GET",
            f"{GMAIL_API}/users/me/messages/{message_id}",
            params={"format": "full"},
        )
        headers = detail.get("payload", {}).get("headers", [])
        return EmailMessage(
            message_id=message_id,
            thread_id=detail.get("threadId", ""),
            subject=_header_value(headers, "Subject") or "(no subject)",
            sender=_header_value(headers, "From") or "unknown",
            to=_header_value(headers, "To") or "",
            date=_header_value(headers, "Date") or "",
            body=_decode_body(detail.get("payload", {})),
            labels=tuple(detail.get("labelIds", [])),
        )

    def draft_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailDraftResult:
        raw = _encode_rfc822(
            to=to, subject=subject, body=body, in_reply_to=in_reply_to
        )
        result = self._request(
            "POST",
            f"{GMAIL_API}/users/me/drafts",
            json_body={"message": {"raw": raw}},
        )
        message = result.get("message", {})
        return EmailDraftResult(
            draft_id=result.get("id", ""),
            message_id=message.get("id", ""),
        )

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailSendResult:
        raw = _encode_rfc822(
            to=to, subject=subject, body=body, in_reply_to=in_reply_to
        )
        result = self._request(
            "POST",
            f"{GMAIL_API}/users/me/messages/send",
            json_body={"raw": raw},
        )
        return EmailSendResult(
            message_id=result.get("id", ""),
            thread_id=result.get("threadId", ""),
        )

    def list_calendar_events(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> list[CalendarEvent]:
        cal_id = calendar_id or self._calendar_id
        result = self._request(
            "GET",
            f"{CALENDAR_API}/calendars/{cal_id}/events",
            params={
                "timeMin": start,
                "timeMax": end,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        events: list[CalendarEvent] = []
        for item in result.get("items", []):
            start_info = item.get("start", {})
            end_info = item.get("end", {})
            attendees = tuple(
                attendee.get("email", "")
                for attendee in item.get("attendees", [])
                if attendee.get("email")
            )
            events.append(
                CalendarEvent(
                    event_id=item.get("id", ""),
                    summary=item.get("summary", "(no title)"),
                    start=start_info.get("dateTime") or start_info.get("date", ""),
                    end=end_info.get("dateTime") or end_info.get("date", ""),
                    location=item.get("location", ""),
                    description=item.get("description", ""),
                    attendees=attendees,
                )
            )
        return events

    def check_availability(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> AvailabilityResult:
        cal_id = calendar_id or self._calendar_id
        result = self._request(
            "POST",
            f"{CALENDAR_API}/freeBusy",
            json_body={
                "timeMin": start,
                "timeMax": end,
                "items": [{"id": cal_id}],
            },
        )
        calendars = result.get("calendars", {}).get(cal_id, {})
        slots = [
            AvailabilitySlot(start=block["start"], end=block["end"], busy=True)
            for block in calendars.get("busy", [])
        ]
        return AvailabilityResult(calendar_id=cal_id, slots=slots)

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
        cal_id = calendar_id or self._calendar_id
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": email} for email in attendees]

        result = self._request(
            "POST",
            f"{CALENDAR_API}/calendars/{cal_id}/events",
            json_body=body,
        )
        return CalendarCreateResult(
            event_id=result.get("id", ""),
            html_link=result.get("htmlLink", ""),
        )

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "communications_email_search",
                "description": "Search or list emails in the user's inbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Gmail search query (e.g. from:alice subject:invoice)",
                        },
                        "unread_only": {
                            "type": "boolean",
                            "description": "If true, only return unread messages",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum messages to return (default 10)",
                        },
                    },
                },
            },
            {
                "type": "function",
                "name": "communications_email_get",
                "description": "Get the full content of an email by message ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "Gmail message ID"},
                    },
                    "required": ["message_id"],
                },
            },
            {
                "type": "function",
                "name": "communications_email_draft",
                "description": "Create an email draft without sending.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "in_reply_to": {
                            "type": "string",
                            "description": "Optional Message-ID header to reply to",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            {
                "type": "function",
                "name": "communications_email_send",
                "description": "Send an email immediately.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "in_reply_to": {
                            "type": "string",
                            "description": "Optional Message-ID header to reply to",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            {
                "type": "function",
                "name": "communications_calendar_list",
                "description": "List calendar events in a time range.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {
                            "type": "string",
                            "description": "Range start (ISO 8601, e.g. 2026-06-30T00:00:00+08:00)",
                        },
                        "end": {
                            "type": "string",
                            "description": "Range end (ISO 8601)",
                        },
                        "calendar_id": {
                            "type": "string",
                            "description": "Calendar ID (default: primary)",
                        },
                    },
                    "required": ["start", "end"],
                },
            },
            {
                "type": "function",
                "name": "communications_calendar_availability",
                "description": "Check busy times on the calendar in a range.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "description": "Range start (ISO 8601)"},
                        "end": {"type": "string", "description": "Range end (ISO 8601)"},
                        "calendar_id": {
                            "type": "string",
                            "description": "Calendar ID (default: primary)",
                        },
                    },
                    "required": ["start", "end"],
                },
            },
            {
                "type": "function",
                "name": "communications_calendar_create",
                "description": "Create a calendar event.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "start": {"type": "string", "description": "Event start (ISO 8601)"},
                        "end": {"type": "string", "description": "Event end (ISO 8601)"},
                        "description": {"type": "string"},
                        "location": {"type": "string"},
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Attendee email addresses",
                        },
                        "calendar_id": {"type": "string"},
                    },
                    "required": ["summary", "start", "end"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "communications_email_search":
            emails = self.search_email(
                query=str(arguments.get("query", "")),
                unread_only=bool(arguments.get("unread_only", False)),
                limit=int(arguments.get("limit", 10)),
            )
            return json.dumps(
                {
                    "emails": [
                        {
                            "message_id": e.message_id,
                            "thread_id": e.thread_id,
                            "subject": e.subject,
                            "from": e.sender,
                            "date": e.date,
                            "snippet": e.snippet,
                            "labels": list(e.labels),
                        }
                        for e in emails
                    ]
                }
            )
        if tool_name == "communications_email_get":
            email = self.get_email(str(arguments["message_id"]))
            return json.dumps(
                {
                    "message_id": email.message_id,
                    "thread_id": email.thread_id,
                    "subject": email.subject,
                    "from": email.sender,
                    "to": email.to,
                    "date": email.date,
                    "body": email.body,
                    "labels": list(email.labels),
                }
            )
        if tool_name == "communications_email_draft":
            draft = self.draft_email(
                to=str(arguments["to"]),
                subject=str(arguments["subject"]),
                body=str(arguments["body"]),
                in_reply_to=arguments.get("in_reply_to"),
            )
            return json.dumps({"draft_id": draft.draft_id, "message_id": draft.message_id})
        if tool_name == "communications_email_send":
            sent = self.send_email(
                to=str(arguments["to"]),
                subject=str(arguments["subject"]),
                body=str(arguments["body"]),
                in_reply_to=arguments.get("in_reply_to"),
            )
            return json.dumps(
                {"message_id": sent.message_id, "thread_id": sent.thread_id, "status": "sent"}
            )
        if tool_name == "communications_calendar_list":
            events = self.list_calendar_events(
                start=str(arguments["start"]),
                end=str(arguments["end"]),
                calendar_id=arguments.get("calendar_id"),
            )
            return json.dumps(
                {
                    "events": [
                        {
                            "event_id": e.event_id,
                            "summary": e.summary,
                            "start": e.start,
                            "end": e.end,
                            "location": e.location,
                            "description": e.description,
                            "attendees": list(e.attendees),
                        }
                        for e in events
                    ]
                }
            )
        if tool_name == "communications_calendar_availability":
            availability = self.check_availability(
                start=str(arguments["start"]),
                end=str(arguments["end"]),
                calendar_id=arguments.get("calendar_id"),
            )
            return json.dumps(
                {
                    "calendar_id": availability.calendar_id,
                    "busy": [
                        {"start": slot.start, "end": slot.end}
                        for slot in availability.slots
                    ],
                }
            )
        if tool_name == "communications_calendar_create":
            created = self.create_calendar_event(
                summary=str(arguments["summary"]),
                start=str(arguments["start"]),
                end=str(arguments["end"]),
                description=str(arguments.get("description", "")),
                location=str(arguments.get("location", "")),
                attendees=arguments.get("attendees"),
                calendar_id=arguments.get("calendar_id"),
            )
            return json.dumps(
                {
                    "event_id": created.event_id,
                    "html_link": created.html_link,
                    "status": "created",
                }
            )
        raise ValueError(f"Unknown tool: {tool_name}")

    def close(self) -> None:
        self._client.close()


def create_communications_provider(settings) -> GoogleCommunicationsAdapter | None:
    if not settings.communications_enabled:
        return None
    if settings.communications_adapter != "google":
        raise ProviderUnavailableError(
            "communications", f"Unsupported adapter: {settings.communications_adapter}"
        )
    if not all(
        [
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_refresh_token,
        ]
    ):
        raise ProviderUnavailableError(
            "communications",
            "Google credentials not set. "
            "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN.",
        )
    return GoogleCommunicationsAdapter(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        refresh_token=settings.google_refresh_token,
        calendar_id=settings.google_calendar_id,
    )
