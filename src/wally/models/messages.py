"""Conversation message types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    role: Role
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_llm_dict(self) -> dict[str, str]:
        return {"role": self.role.value, "content": self.content}


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    messages: list[Message] = field(default_factory=list)
    summary: str | None = None
    summary_covers_through: int = 0


@dataclass
class WallyResponse:
    """Structured response returned to the user."""

    content: str
    session_id: str
    facts: list[str] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
