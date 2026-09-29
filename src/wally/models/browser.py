"""Browser automation domain types — deterministic actions only."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BrowserActionType(StrEnum):
    """Deterministic browser primitives. No business semantics."""

    NAVIGATE = "navigate"
    FILL = "fill"
    CLICK = "click"
    SELECT = "select"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    WAIT = "wait"
    WAIT_FOR_USER = "wait_for_user"
    READ_PAGE = "read_page"
    VERIFY_AUTH = "verify_auth"
    SCREENSHOT = "screenshot"


@dataclass(frozen=True)
class BrowserAction:
    """A single deterministic browser step requested by the runtime."""

    action_type: BrowserActionType
    parameters: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        from wally.runtime.secrets_safety import redact_secret_parameters

        return (
            f"BrowserAction(action_type={self.action_type!r}, "
            f"parameters={redact_secret_parameters(self.parameters)!r})"
        )


@dataclass(frozen=True)
class BrowserSession:
    """An isolated browser session (e.g. one portal visit)."""

    session_id: str
    url: str | None = None


class BrowserStepStatus(StrEnum):
    COMPLETED = "completed"
    WAITING_FOR_USER = "waiting_for_user"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class BrowserStepResult:
    """Outcome of one or more browser actions in a session."""

    session_id: str
    status: BrowserStepStatus
    message: str | None = None
    page_text: str | None = None
    download_path: str | None = None
    screenshot_path: str | None = None
    authenticated: bool | None = None
