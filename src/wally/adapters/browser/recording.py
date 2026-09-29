"""In-memory browser adapter for tests and dry runs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from wally.models.browser import (
    BrowserAction,
    BrowserActionType,
    BrowserSession,
    BrowserStepResult,
    BrowserStepStatus,
)


@dataclass
class _RecordingSession:
    session_id: str
    url: str
    actions: list[BrowserAction] = field(default_factory=list)
    closed: bool = False
    run_count: int = 0


class RecordingBrowserAdapter:
    """Records browser calls without launching a real browser."""

    def __init__(self) -> None:
        self._sessions: dict[str, _RecordingSession] = {}
        self.opened_urls: list[str] = []

    @property
    def name(self) -> str:
        return "browser"

    def is_healthy(self) -> bool:
        return True

    def open_session(self, *, url: str) -> BrowserSession:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = _RecordingSession(session_id=session_id, url=url)
        self.opened_urls.append(url)
        return BrowserSession(session_id=session_id, url=url)

    def run_actions(
        self,
        session_id: str,
        actions: tuple[BrowserAction, ...],
    ) -> BrowserStepResult:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.FAILED,
                message="Unknown or closed browser session.",
            )

        session.actions.extend(_redact_recorded_actions(actions))
        session.run_count += 1
        for action in actions:
            if action.action_type == BrowserActionType.WAIT_FOR_USER:
                return BrowserStepResult(
                    session_id=session_id,
                    status=BrowserStepStatus.WAITING_FOR_USER,
                    message=str(action.parameters.get("reason", "Waiting for user.")),
                )
            if action.action_type == BrowserActionType.VERIFY_AUTH:
                return BrowserStepResult(
                    session_id=session_id,
                    status=BrowserStepStatus.COMPLETED,
                    message="Authentication verified.",
                    authenticated=True,
                )
            if action.action_type == BrowserActionType.READ_PAGE:
                return BrowserStepResult(
                    session_id=session_id,
                    status=BrowserStepStatus.COMPLETED,
                    page_text=f"Recorded page at {session.url}",
                )

        return BrowserStepResult(
            session_id=session_id,
            status=BrowserStepStatus.COMPLETED,
            message="Actions completed.",
        )

    def close_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.closed = True

    def is_session_open(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return session is not None and not session.closed

    def session_run_count(self, session_id: str) -> int:
        session = self._sessions.get(session_id)
        return session.run_count if session is not None else 0

    def session_actions(self, session_id: str) -> tuple[BrowserAction, ...]:
        session = self._sessions.get(session_id)
        if session is None:
            return ()
        return tuple(session.actions)


def _redact_recorded_actions(actions: tuple[BrowserAction, ...]) -> list[BrowserAction]:
    from wally.runtime.secrets_safety import redact_secret_parameters

    return [
        BrowserAction(action.action_type, redact_secret_parameters(action.parameters))
        for action in actions
    ]
