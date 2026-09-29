"""Deterministic text cleanup for Observe & Brief. Not policy."""

from __future__ import annotations

import re
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from wally.ops.priority import parse_time
from wally.runtime.content_sanitizer import sanitize_external_text

TITLE_MAX = 120
EMAIL_SNIPPET_MAX = 140
RECEIPT_SNIPPET_MAX = 100
CALENDAR_DESC_MAX = 160

# Google appends these to auto-created events, always after any user-authored text.
# Stored legacy summaries were length-capped, so the tail may be truncated mid-sentence
# or mid-URL; each pattern therefore runs to the end of the text.
_CALENDAR_BOILERPLATE = (
    re.compile(r"To see detailed information.*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"This event was created from an email\b.*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"Automatically created(?: event)?(?: by Gmail)?\.?", re.IGNORECASE),
)
_CALENDAR_BOILERPLATE_URLS = re.compile(
    r"(?:https?://)?(?:www\.)?(?:g\.co|google\.com/cal|calendar\.google\.com)\S*",
    re.IGNORECASE,
)

UNMATCHED_RECEIPT_CHANGE = "Receipt noted; no matching open bill"
UNMATCHED_RECEIPT_REASON = "Unmatched receipt; no open bill"


def system_display_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo or UTC


def resolve_display_tz(name: str | None) -> tzinfo:
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    return system_display_tz()


def sanitize_ops_text(text: str, *, max_chars: int) -> str:
    cleaned = sanitize_external_text(text or "", max_chars=None)
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 1:
        return cleaned[:max_chars]
    return cleaned[: max_chars - 1].rstrip() + "…"


def strip_google_calendar_boilerplate(text: str) -> str:
    cleaned = text or ""
    for pattern in _CALENDAR_BOILERPLATE:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _CALENDAR_BOILERPLATE_URLS.sub(" ", cleaned)
    return cleaned


def clean_calendar_description(text: str) -> str:
    return sanitize_ops_text(
        strip_google_calendar_boilerplate(text),
        max_chars=CALENDAR_DESC_MAX,
    )


def clean_email_snippet(text: str, *, receipt: bool = False) -> str:
    limit = RECEIPT_SNIPPET_MAX if receipt else EMAIL_SNIPPET_MAX
    return sanitize_ops_text(text, max_chars=limit)


def clean_title(text: str) -> str:
    return sanitize_ops_text(text, max_chars=TITLE_MAX)


def format_brief_datetime(value: str, *, tz: tzinfo) -> str:
    parsed = parse_time(value)
    if parsed is None:
        return value
    local = parsed.astimezone(tz)
    date_part = f"{local.strftime('%a')} {local.day} {local.strftime('%b %Y')}"
    if local.hour == 0 and local.minute == 0 and local.second == 0 and local.microsecond == 0:
        return date_part
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{date_part}, {hour}:{local.strftime('%M')} {local.strftime('%p').lower()}"
