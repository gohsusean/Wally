"""Conversation intelligence provider protocol."""

from __future__ import annotations

from typing import Protocol

from wally.models.conversation import ConversationHit
from wally.providers.capability import CapabilityProvider


class ConversationProvider(CapabilityProvider, Protocol):
    """Search and recall prior conversation sessions (distinct from Knowledge)."""

    def set_exclude_session(self, session_id: str) -> None:
        """Exclude the active session from search results."""
        ...

    def recent(
        self, *, limit: int = 10, exclude_session: str | None = None
    ) -> list[ConversationHit]:
        """Return the most recent messages from prior sessions."""
        ...

    def search(
        self, query: str, *, limit: int = 10, exclude_session: str | None = None
    ) -> list[ConversationHit]:
        """Search message history across sessions."""
        ...
