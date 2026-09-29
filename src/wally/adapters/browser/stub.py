"""Unconfigured browser automation stub for future Playwright adapter."""

from __future__ import annotations

from wally.exceptions import ProviderUnavailableError
from wally.models.browser import BrowserAction, BrowserSession, BrowserStepResult

# v0.10 Playwright adapter must call runtime/browser_safety.py before any navigation.
# See docs/browser-automation.md — trusted portal URLs from Knowledge only.


class UnconfiguredBrowserAutomationProvider:
    """Placeholder until a BrowserAutomationProvider adapter is implemented."""

    @property
    def name(self) -> str:
        return "browser"

    def is_healthy(self) -> bool:
        return False

    def open_session(self, *, url: str) -> BrowserSession:
        raise ProviderUnavailableError(
            self.name,
            "BrowserAutomationProvider is not configured. "
            "Enable browser automation (v0.10+) with a Playwright adapter.",
        )

    def run_actions(
        self,
        session_id: str,
        actions: tuple[BrowserAction, ...],
    ) -> BrowserStepResult:
        raise ProviderUnavailableError(
            self.name,
            "BrowserAutomationProvider is not configured.",
        )

    def close_session(self, session_id: str) -> None:
        raise ProviderUnavailableError(
            self.name,
            "BrowserAutomationProvider is not configured.",
        )
