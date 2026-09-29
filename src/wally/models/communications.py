"""Communications domain models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EmailSummary:
    message_id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    snippet: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    thread_id: str
    subject: str
    sender: str
    to: str
    date: str
    body: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    summary: str
    start: str
    end: str
    location: str = ""
    description: str = ""
    attendees: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailDraftResult:
    draft_id: str
    message_id: str


@dataclass(frozen=True)
class EmailSendResult:
    message_id: str
    thread_id: str


@dataclass(frozen=True)
class CalendarCreateResult:
    event_id: str
    html_link: str = ""


@dataclass
class AvailabilitySlot:
    start: str
    end: str
    busy: bool = True


@dataclass
class AvailabilityResult:
    calendar_id: str
    slots: list[AvailabilitySlot] = field(default_factory=list)
