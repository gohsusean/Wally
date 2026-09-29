"""Playwright-backed browser automation adapter."""

from __future__ import annotations

import uuid
from typing import Any

from wally.exceptions import ProviderUnavailableError
from wally.models.browser import (
    BrowserAction,
    BrowserActionType,
    BrowserSession,
    BrowserStepResult,
    BrowserStepStatus,
)


class PlaywrightBrowserAdapter:
    """Execute browser actions via Playwright.

    Trusted portal URL policy must be enforced by GovernedBrowserExecutor before
    calling open_session or NAVIGATE actions.
    """

    def __init__(self, *, headless: bool = False) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "browser"

    def is_healthy(self) -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ProviderUnavailableError(
                self.name,
                "Playwright is not installed. Run: uv sync --extra browser && playwright install",
            ) from exc
        self._playwright = sync_playwright().start()
        # Do not enable tracing, HAR, or video — they persist credentials and session pages.
        self._browser = self._playwright.chromium.launch(headless=self._headless)

    def open_session(self, *, url: str) -> BrowserSession:
        self._ensure_browser()
        assert self._browser is not None
        page = self._browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        session_id = str(uuid.uuid4())
        self._pages[session_id] = page
        return BrowserSession(session_id=session_id, url=url)

    def run_actions(
        self,
        session_id: str,
        actions: tuple[BrowserAction, ...],
    ) -> BrowserStepResult:
        page = self._pages.get(session_id)
        if page is None:
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.FAILED,
                message="Unknown browser session.",
            )

        for action in actions:
            result = self._run_action(session_id, page, action)
            if result.status != BrowserStepStatus.COMPLETED:
                return result

        return BrowserStepResult(
            session_id=session_id,
            status=BrowserStepStatus.COMPLETED,
            message="Actions completed.",
        )

    def _run_action(self, session_id: str, page: Any, action: BrowserAction) -> BrowserStepResult:
        params = action.parameters
        if action.action_type == BrowserActionType.NAVIGATE:
            url = str(params.get("url", ""))
            page.goto(url, wait_until="domcontentloaded")
            return BrowserStepResult(session_id=session_id, status=BrowserStepStatus.COMPLETED)

        if action.action_type == BrowserActionType.FILL:
            selector = str(params["selector"])
            value = str(params.get("value", ""))
            try:
                page.fill(selector, value)
            except Exception:
                return BrowserStepResult(
                    session_id=session_id,
                    status=BrowserStepStatus.FAILED,
                    message="Browser fill failed for a configured selector.",
                )
            return BrowserStepResult(session_id=session_id, status=BrowserStepStatus.COMPLETED)

        if action.action_type == BrowserActionType.CLICK:
            try:
                page.click(str(params["selector"]))
            except Exception:
                return BrowserStepResult(
                    session_id=session_id,
                    status=BrowserStepStatus.FAILED,
                    message="Browser click failed for a configured selector.",
                )
            return BrowserStepResult(session_id=session_id, status=BrowserStepStatus.COMPLETED)

        if action.action_type == BrowserActionType.WAIT:
            timeout_ms = int(params.get("timeout_ms", 1000))
            page.wait_for_timeout(timeout_ms)
            return BrowserStepResult(session_id=session_id, status=BrowserStepStatus.COMPLETED)

        if action.action_type == BrowserActionType.WAIT_FOR_USER:
            reason = str(params.get("reason", "Complete manual authentication in the browser."))
            print(f"[Wally browser] {reason}", flush=True)
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.WAITING_FOR_USER,
                message=reason,
            )

        if action.action_type == BrowserActionType.READ_PAGE:
            text = page.inner_text("body")
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.COMPLETED,
                page_text=text[:8000],
            )

        if action.action_type == BrowserActionType.VERIFY_AUTH:
            selector = params.get("selector")
            url_contains = params.get("url_contains")
            timeout_ms = int(params.get("timeout_ms", 15000))
            try:
                if selector:
                    page.wait_for_selector(str(selector), timeout=timeout_ms)
                if url_contains and str(url_contains) not in str(page.url):
                    return BrowserStepResult(
                        session_id=session_id,
                        status=BrowserStepStatus.FAILED,
                        message="Authentication URL condition was not met.",
                        authenticated=False,
                    )
            except Exception:
                return BrowserStepResult(
                    session_id=session_id,
                    status=BrowserStepStatus.FAILED,
                    message="Authentication verification failed.",
                    authenticated=False,
                )
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.COMPLETED,
                message="Authentication verified.",
                authenticated=True,
            )

        if action.action_type == BrowserActionType.SCREENSHOT:
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.FAILED,
                message=(
                    "Screenshots are disabled so credentials and authenticated "
                    "pages are not written to disk."
                ),
            )

        return BrowserStepResult(
            session_id=session_id,
            status=BrowserStepStatus.FAILED,
            message=f"Unsupported browser action: {action.action_type}",
        )

    def close_session(self, session_id: str) -> None:
        page = self._pages.pop(session_id, None)
        if page is not None:
            page.close()

    def shutdown(self) -> None:
        for session_id in list(self._pages):
            self.close_session(session_id)
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
