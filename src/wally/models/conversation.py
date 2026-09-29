"""Conversation intelligence domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationHit:
    session_id: str
    message_id: int
    role: str
    content: str
    snippet: str
    session_created_at: str


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    summary: str
    covers_message_count: int
    updated_at: str
