"""Browser automation provider protocol — deterministic browser execution."""

from __future__ import annotations

from typing import Protocol

from wally.models.browser import BrowserAction, BrowserSession, BrowserStepResult


class BrowserAutomationProvider(Protocol):
    """Execute deterministic browser interactions. No business logic.

    The runtime (or an execution-capability workflow) supplies action scripts.
    The LLM does not invoke this provider directly for payment or finance flows.

    Responsibilities:
      - Open trusted portals, navigate, fill forms, click, upload, download
      - Read confirmation pages and return structured results
      - Pause for manual authentication when credentials are unavailable

    Not responsible for:
      - Payment method selection, verification, approval, or workflow routing
      - Storing or resolving secrets (runtime + SecretsProvider coordinate that)
    """

    @property
    def name(self) -> str:
        ...

    def is_healthy(self) -> bool:
        ...

    def open_session(self, *, url: str) -> BrowserSession:
        """Open a new browser session at a URL.

        Callers must pass a URL already validated by runtime/browser_safety.py
        against payment_portal_url (or equivalent) from an approved Knowledge Asset.
        """
        ...

    def run_actions(
        self,
        session_id: str,
        actions: tuple[BrowserAction, ...],
    ) -> BrowserStepResult:
        """Run a deterministic sequence of browser actions in an existing session."""
        ...

    def close_session(self, session_id: str) -> None:
        """Close a browser session and release resources."""
        ...
