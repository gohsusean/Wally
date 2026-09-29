"""Assemble context for LLM requests."""

from __future__ import annotations

from wally.config.loader import Settings, load_instructions
from wally.models.messages import Message, Role, Session


def build_instructions(settings: Settings) -> str:
    return load_instructions(settings)


def build_llm_messages(session: Session, user_input: str) -> list[Message]:
    """Return conversation history plus the new user message (not yet persisted)."""
    messages = list(session.messages)
    messages.append(Message(role=Role.USER, content=user_input))
    return messages
