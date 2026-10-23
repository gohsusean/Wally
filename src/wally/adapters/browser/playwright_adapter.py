"""Playwright-backed browser automation adapter."""

from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import replace
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

    def __init__(self, *, headless: bool = False, channel: str | None = None) -> None:
        if channel not in (None, "chrome"):
            raise ProviderUnavailableError("browser", "Unsupported browser channel.")
        self._headless = headless
        self._channel = channel
        self._playwright: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "browser"

    @property
    def supports_live_auth_verification(self) -> bool:
        return True

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
        # Playwright owns a fresh temporary browser profile. Never attach to a
        # personal Chrome profile or restore its cookies/storage.
        options = {"headless": self._headless}
        if self._channel:
            options["channel"] = self._channel
        try:
            self._browser = self._playwright.chromium.launch(**options)
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise ProviderUnavailableError(
                "browser", "Configured Playwright browser could not start; no fallback was used."
            ) from None

    def open_session(self, *, url: str, allowed_origins: tuple[str, ...] = ()) -> BrowserSession:
        self._ensure_browser()
        assert self._browser is not None
        # new_page owns a separate non-persistent context, closed with the page.
        page = self._browser.new_page(service_workers="block", accept_downloads=False)
        session_id = str(uuid.uuid4())
        self._pages[session_id] = page
        try:
            if allowed_origins:
                # Install on the isolated context before any remote script/navigation.
                self.restrict_origins(session_id, allowed_origins)
            page.goto(url, wait_until="domcontentloaded")
            if allowed_origins:
                self._check_origin(page, allowed_origins)
            return BrowserSession(session_id=session_id, url=url)
        except Exception:
            self.close_session(session_id)
            raise ProviderUnavailableError(
                "browser", "Portal navigation/origin policy failed."
            ) from None

    @staticmethod
    def _check_origin(page, origins):
        from wally.finance.models import FinanceError, origin

        if origin(page.url) not in origins:
            raise FinanceError("Portal origin denied.")

    def restrict_origins(self, session_id: str, origins: tuple[str, ...]) -> None:
        from wally.finance.models import FinanceError, origin

        page = self._pages[session_id]
        if page.url != "about:blank":
            self._check_origin(page, origins)
        if getattr(page, "_wally_restricted", False):
            return

        def restrict(route):
            try:
                allowed = origin(route.request.url) in origins
            except FinanceError:
                allowed = False
            route.continue_() if allowed else route.abort()

        page.context.route("**/*", restrict)
        # Ordinary request routes do not cover WebSockets. Deny all on this bounded
        # auth-only path, including sockets an initial script could retain.
        page.context.route_web_socket("**/*", lambda socket: socket.close())
        page._wally_restricted = True

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

    def authentication_field_state(self, session_id, selector):
        from wally.adapters.browser.authentication import LoginState, field_state

        try:
            return field_state(self._pages[session_id], selector)
        except Exception:
            return LoginState.FAILED

    def submit_authentication_step(
        self, session_id, step, value, *, allowed_origins, before_submit
    ):
        from wally.adapters.browser.authentication import (
            NETWORK_ERROR_CODES,
            LoginResult,
            LoginState,
            advance_login,
        )
        from wally.finance.models import origin

        try:
            self.restrict_origins(session_id, allowed_origins)
            page = self._pages[session_id]
        except Exception:
            return LoginResult(LoginState.ORIGIN_CHANGED)
        network_errors: set[str] = set()
        http_errors: set[int] = set()

        def relevant(request):
            return (
                request.resource_type in ("xhr", "fetch") and origin(request.url) in allowed_origins
            )

        def failed(request):
            with suppress(Exception):
                if relevant(request) and len(network_errors) < 8:
                    error = request.failure
                    network_errors.add(
                        error if error in NETWORK_ERROR_CODES else "unclassified_network_failure"
                    )

        def responded(response):
            with suppress(Exception):
                status = response.status
                if (
                    relevant(response.request)
                    and type(status) is int
                    and 400 <= status <= 599
                    and len(http_errors) < 8
                ):
                    http_errors.add(status)

        try:
            page.on("requestfailed", failed)
            page.on("response", responded)
            result = advance_login(
                page,
                step,
                value,
                check_origin=lambda: self._check_origin(page, allowed_origins),
                before_submit=before_submit,
            )
            return replace(
                result,
                network_errors=tuple(sorted(network_errors)),
                http_error_statuses=tuple(sorted(http_errors)),
            )
        except Exception:
            return LoginResult(LoginState.FAILED)
        finally:
            with suppress(Exception):
                page.remove_listener("requestfailed", failed)
            with suppress(Exception):
                page.remove_listener("response", responded)

    def wait_for_hlb_login(self, session_id):
        """HLB redirects after DOMContentLoaded; wait for its actual username form."""
        from wally.adapters.browser.hlb import HLB_ORIGIN, check_hlb_origin
        from wally.finance.models import FinanceError

        try:
            self.restrict_origins(session_id, (HLB_ORIGIN,))
            page = self._pages[session_id]
            check_hlb_origin(page)
            page.locator("#idLoginId").wait_for(state="visible", timeout=20000)
            check_hlb_origin(page)
        except Exception:
            raise FinanceError("HLB login form/origin not ready; login stopped.") from None

    def enroll_hlb_security_picture(
        self, session_id, picture, *, confirm_owner, guard, reference=None, replace=False
    ):
        from wally.adapters.browser.hlb import HLB_ORIGIN, REFERENCE

        self.restrict_origins(session_id, (HLB_ORIGIN,))
        picture.enroll(
            self._pages[session_id],
            confirm_owner=confirm_owner,
            guard=guard,
            reference=reference or REFERENCE,
            replace=replace,
        )

    def submit_hlb_password(self, session_id, picture, step, value, *, guard, reference=None):
        """Native picture checkbox/focus, then the existing guarded password step."""
        from wally.adapters.browser.authentication import LoginResult, LoginState
        from wally.adapters.browser.hlb import HLB_ORIGIN, REFERENCE, check_hlb_origin

        reference = reference or REFERENCE
        try:
            self.restrict_origins(session_id, (HLB_ORIGIN,))
            page = self._pages[session_id]
            expected = picture.confirm(page, reference=reference, guard=guard)

            def checked_guard():
                guard()
                picture.verify(page, reference, expected=expected)
                check_hlb_origin(page)
                if not page.locator(picture.checkbox_selector).is_checked():
                    raise ValueError

            checked_guard()
            # HLB makes the password field editable on native focus. Never alter
            # readonly/disabled attributes or bank challenge controls.
            page.locator(step.input_selector).click(timeout=5000)
            checked_guard()
            return self.submit_authentication_step(
                session_id,
                step,
                value,
                allowed_origins=(HLB_ORIGIN,),
                before_submit=checked_guard,
            )
        except Exception:
            return LoginResult(LoginState.FAILED)

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
            try:
                page.close()
            except Exception:
                # Cleanup failures must not expose browser diagnostics or leave
                # the remaining owned sessions/driver unclosed at shutdown.
                with suppress(Exception):
                    page.context.close()

    def read_fields(self, session_id: str, selectors: dict[str, str]) -> dict[str, str]:
        """Trusted deterministic adapters read selected fields, never the page body.

        Callers must register selectors in reviewed code/config. This is not a
        model-facing tool and must never be used to read credential input fields.
        """
        try:
            return {
                name: self._pages[session_id].inner_text(selector).strip()
                for name, selector in selectors.items()
            }
        except Exception:
            raise ProviderUnavailableError("browser", "Configured field read failed.") from None

    def has_selector(self, session_id: str, selector: str) -> bool:
        try:
            return bool(selector and self._pages[session_id].locator(selector).is_visible())
        except Exception:
            raise ProviderUnavailableError(
                "browser", "Configured challenge check failed."
            ) from None

    def shutdown(self) -> None:
        for session_id in list(self._pages):
            self.close_session(session_id)
        if self._browser is not None:
            with suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with suppress(Exception):
                self._playwright.stop()
            self._playwright = None
