"""Pending browser sessions awaiting manual authentication."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from wally.models.browser import BrowserAction


@dataclass(frozen=True)
class PendingBrowserSession:
    """A browser session paused after WAIT_FOR_USER with remaining actions."""

    session_id: str
    portal_url: str
    knowledge: dict[str, Any]
    pending_actions: tuple[BrowserAction, ...]
    capability: str
    parameters: dict[str, object]
    registered_at: float


class BrowserSessionStore:
    """In-memory registry of resumable browser sessions."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingBrowserSession] = {}

    def register(self, pending: PendingBrowserSession) -> None:
        self._pending[pending.session_id] = pending

    def get(self, session_id: str) -> PendingBrowserSession | None:
        return self._pending.get(session_id)

    def clear(self, session_id: str) -> PendingBrowserSession | None:
        return self._pending.pop(session_id, None)

    def is_expired(
        self, session_id: str, timeout_seconds: float, *, now: float | None = None
    ) -> bool:
        pending = self.get(session_id)
        if pending is None:
            return True
        current = now if now is not None else time.time()
        return (current - pending.registered_at) > timeout_seconds

    def expired_session_ids(
        self, timeout_seconds: float, *, now: float | None = None
    ) -> tuple[str, ...]:
        current = now if now is not None else time.time()
        return tuple(
            session_id
            for session_id, pending in self._pending.items()
            if (current - pending.registered_at) > timeout_seconds
        )
