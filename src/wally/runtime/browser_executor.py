"""Governed browser execution — policy before Playwright."""

from __future__ import annotations

import time
from typing import Any

from wally.exceptions import ExecutionNotStartedError, ProviderUnavailableError
from wally.models.browser import (
    BrowserAction,
    BrowserActionType,
    BrowserSession,
    BrowserStepResult,
    BrowserStepStatus,
)
from wally.providers.browser import BrowserAutomationProvider
from wally.runtime.browser_safety import (
    evaluate_browser_session_actions,
    evaluate_trusted_portal_navigation,
    resolve_trusted_portal_url,
)
from wally.runtime.browser_scripts import (
    card_portal_post_auth_actions,
    card_portal_pre_auth_actions,
    portal_verify_auth_actions,
)
from wally.runtime.browser_session_store import BrowserSessionStore, PendingBrowserSession
from wally.runtime.policy import PolicyDecision
from wally.runtime.secret_resolver import GovernedSecretsResolver
from wally.runtime.secrets_safety import scrub_secret_values

DEFAULT_SESSION_TIMEOUT_SECONDS = 3600.0
_REVIEW_LOGIN_ACTION_TYPES = frozenset({BrowserActionType.FILL, BrowserActionType.CLICK})


class GovernedBrowserExecutor:
    """Wrap BrowserAutomationProvider with trusted-portal URL policy."""

    def __init__(
        self,
        provider: BrowserAutomationProvider | None,
        *,
        session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS,
        session_store: BrowserSessionStore | None = None,
        secrets: GovernedSecretsResolver | None = None,
    ) -> None:
        self._provider = provider
        self._session_timeout_seconds = session_timeout_seconds
        self._pending = session_store or BrowserSessionStore()
        self._secrets = secrets

    @property
    def provider(self) -> BrowserAutomationProvider | None:
        return self._provider

    @property
    def pending_sessions(self) -> BrowserSessionStore:
        return self._pending

    def _require_provider(self) -> BrowserAutomationProvider:
        if self._provider is None or not self._provider.is_healthy():
            raise ProviderUnavailableError(
                "browser",
                "Browser automation is not configured. "
                "Enable providers.browser in config and install Playwright "
                "(uv sync --extra browser && playwright install).",
            )
        return self._provider

    def open_trusted_session(self, knowledge: dict[str, Any]) -> BrowserSession:
        """Open a session only at the trusted portal URL from Knowledge."""
        provider = self._require_provider()
        trusted_url, policy = resolve_trusted_portal_url(knowledge)
        if not policy.allowed or not trusted_url:
            raise ProviderUnavailableError("browser", policy.reason or "Policy denied.")

        navigation = evaluate_trusted_portal_navigation(
            requested_url=trusted_url,
            knowledge=knowledge,
        )
        if not navigation.allowed:
            raise ProviderUnavailableError("browser", navigation.reason or "Policy denied.")

        return provider.open_session(url=trusted_url)

    def run_governed_actions(
        self,
        *,
        knowledge: dict[str, Any],
        session_id: str,
        actions: tuple,
    ) -> BrowserStepResult:
        """Run actions after validating NAVIGATE targets against Knowledge."""
        provider = self._require_provider()
        policy = evaluate_browser_session_actions(actions=actions, knowledge=knowledge)
        if not policy.allowed:
            raise ProviderUnavailableError("browser", policy.reason or "Policy denied.")
        return provider.run_actions(session_id, actions)

    def run_card_portal_payment(
        self,
        *,
        bill: dict[str, Any],
        parameters: dict[str, object] | None = None,
        authorized: bool = False,
    ) -> dict[str, object]:
        """Open portal; inject credentials only after authorization, else manual login."""
        self.expire_stale_sessions()
        session = self.open_trusted_session(bill)
        login_actions = None
        fill_secrets: tuple[str, ...] = ()
        if self._secrets is not None and authorized:
            login_actions = self._secrets.build_portal_login_actions(
                bill,
                authorized=True,
                session_id=session.session_id,
            )
            fill_secrets = _fill_values(login_actions)
        pre_auth = login_actions or card_portal_pre_auth_actions(bill)
        result = self.run_governed_actions(
            knowledge=bill,
            session_id=session.session_id,
            actions=pre_auth,
        )
        if result.status == BrowserStepStatus.WAITING_FOR_USER:
            self._pending.register(
                PendingBrowserSession(
                    session_id=session.session_id,
                    portal_url=session.url or "",
                    knowledge=dict(bill),
                    pending_actions=card_portal_post_auth_actions(bill),
                    capability="pay-bill-card-portal",
                    parameters=dict(parameters or {}),
                    registered_at=time.time(),
                )
            )
            return self._waiting_response(
                session=session,
                result=result,
                parameters=parameters,
            )

        if result.status != BrowserStepStatus.COMPLETED:
            return self._finalize_session(
                session_id=session.session_id,
                portal_url=session.url,
                result=result,
                parameters=parameters,
                clear_pending=False,
                fill_secrets=fill_secrets,
            )

        post_auth = card_portal_post_auth_actions(bill)
        result = self.run_governed_actions(
            knowledge=bill,
            session_id=session.session_id,
            actions=post_auth,
        )
        return self._finalize_session(
            session_id=session.session_id,
            portal_url=session.url,
            result=result,
            parameters=parameters,
            clear_pending=False,
            fill_secrets=fill_secrets,
        )

    def run_portal_review_login(
        self,
        *,
        bill: dict[str, Any],
        authorized: bool,
    ) -> BrowserStepResult:
        """Log in to the trusted portal for a bill review. Never reaches a payment step.

        Credentials are resolved only when ``authorized`` is true, and before any
        session opens, so a secrets failure is known to have submitted nothing.
        Raises ExecutionNotStartedError for every stop before the login submits.
        The session stays open for ``verify_portal_review``; the caller closes it.
        """
        if not authorized:
            raise ExecutionNotStartedError("not_authorized", "Execution was not authorized.")
        try:
            self._require_provider()
        except ProviderUnavailableError as exc:
            raise ExecutionNotStartedError("browser_unavailable", exc.reason) from exc
        if self._secrets is None:
            raise ExecutionNotStartedError(
                "credentials_unavailable", "Portal credentials are not configured."
            )
        try:
            login_actions = self._secrets.build_portal_login_actions(bill, authorized=True)
        except ProviderUnavailableError as exc:
            raise ExecutionNotStartedError("credentials_unavailable", exc.reason) from exc
        if not login_actions:
            raise ExecutionNotStartedError(
                "credentials_unavailable",
                "Knowledge lists no usable portal login references or secrets are unavailable.",
            )
        if any(
            action.action_type not in _REVIEW_LOGIN_ACTION_TYPES for action in login_actions
        ):
            raise ExecutionNotStartedError(
                "policy_denied", "Portal review login may only fill fields and submit login."
            )
        try:
            session = self.open_trusted_session(bill)
        except ProviderUnavailableError as exc:
            raise ExecutionNotStartedError("untrusted_target", exc.reason) from exc
        try:
            result = self.run_governed_actions(
                knowledge=bill,
                session_id=session.session_id,
                actions=login_actions,
            )
        except Exception:
            self.close_session(session.session_id)
            raise
        message = scrub_secret_values(result.message or "", _fill_values(login_actions))
        return BrowserStepResult(
            session_id=session.session_id,
            status=result.status,
            message=message,
        )

    def verify_portal_review(
        self,
        *,
        bill: dict[str, Any],
        session_id: str,
    ) -> BrowserStepResult:
        """Read-only login check in an existing session. Never repeats the login."""
        actions = portal_verify_auth_actions(bill)
        if not actions:
            return BrowserStepResult(
                session_id=session_id,
                status=BrowserStepStatus.FAILED,
                message="Knowledge configures no login success condition.",
            )
        result = self.run_governed_actions(
            knowledge=bill,
            session_id=session_id,
            actions=actions,
        )
        return BrowserStepResult(
            session_id=session_id,
            status=result.status,
            message="",
            authenticated=result.authenticated,
        )

    def close_session(self, session_id: str) -> None:
        provider = self._provider
        if provider is not None:
            provider.close_session(session_id)

    def resume_card_portal_session(self, session_id: str) -> dict[str, object]:
        """Continue post-login actions in the same browser session."""
        pending = self._pending.get(session_id)
        if pending is None:
            raise ProviderUnavailableError(
                "browser",
                f"No resumable browser session '{session_id}'. "
                "It may have completed, been cancelled, or timed out.",
            )
        if self._pending.is_expired(session_id, self._session_timeout_seconds):
            return self._timeout_session(session_id)

        result = self.run_governed_actions(
            knowledge=pending.knowledge,
            session_id=session_id,
            actions=pending.pending_actions,
        )
        return self._finalize_session(
            session_id=session_id,
            portal_url=pending.portal_url,
            result=result,
            parameters=pending.parameters,
            clear_pending=True,
        )

    def cancel_card_portal_session(
        self,
        session_id: str,
        *,
        reason: str = "Payment cancelled by user.",
    ) -> dict[str, object]:
        """Close a pending browser session without completing post-login steps."""
        pending = self._pending.clear(session_id)
        provider = self._provider
        if provider is not None:
            provider.close_session(session_id)
        return {
            "execution": "browser",
            "session_id": session_id,
            "status": BrowserStepStatus.CANCELLED.value,
            "message": reason,
            "portal_url": pending.portal_url if pending else None,
            "cancelled": True,
        }

    def expire_stale_sessions(self) -> list[dict[str, object]]:
        """Close sessions that exceeded the manual-auth timeout."""
        expired: list[dict[str, object]] = []
        for session_id in self._pending.expired_session_ids(self._session_timeout_seconds):
            expired.append(self._timeout_session(session_id))
        return expired

    def has_resumable_session(self, session_id: str) -> bool:
        pending = self._pending.get(session_id)
        if pending is None:
            return False
        return not self._pending.is_expired(session_id, self._session_timeout_seconds)

    def evaluate_portal_policy(self, knowledge: dict[str, Any]) -> PolicyDecision:
        """Check whether a trusted portal URL exists before approval/execution."""
        _, policy = resolve_trusted_portal_url(knowledge)
        return policy

    def _timeout_session(self, session_id: str) -> dict[str, object]:
        pending = self._pending.clear(session_id)
        provider = self._provider
        if provider is not None:
            provider.close_session(session_id)
        message = (
            "Browser session timed out waiting for manual authentication. "
            f"Resume within {int(self._session_timeout_seconds)} seconds."
        )
        return {
            "execution": "browser",
            "session_id": session_id,
            "status": BrowserStepStatus.TIMED_OUT.value,
            "message": message,
            "portal_url": pending.portal_url if pending else None,
            "timed_out": True,
        }

    def _waiting_response(
        self,
        *,
        session: BrowserSession,
        result: BrowserStepResult,
        parameters: dict[str, object] | None,
    ) -> dict[str, object]:
        return {
            "execution": "browser",
            "capability": "pay-bill-card-portal",
            "session_id": session.session_id,
            "portal_url": session.url,
            "status": result.status.value,
            "message": result.message,
            "waiting_for_user": True,
            "resumable": True,
            "next_step": "resume",
            "session_timeout_seconds": int(self._session_timeout_seconds),
            "parameters": parameters or {},
        }

    def _finalize_session(
        self,
        *,
        session_id: str,
        portal_url: str | None,
        result: BrowserStepResult,
        parameters: dict[str, object] | None,
        clear_pending: bool,
        fill_secrets: tuple[str, ...] = (),
    ) -> dict[str, object]:
        if clear_pending:
            self._pending.clear(session_id)
        provider = self._provider
        if provider is not None and result.status != BrowserStepStatus.WAITING_FOR_USER:
            provider.close_session(session_id)
        page_text = result.page_text
        if page_text and fill_secrets:
            page_text = scrub_secret_values(page_text, fill_secrets)
        if result.authenticated is True:
            page_text = None
        payload: dict[str, object] = {
            "execution": "browser",
            "capability": "pay-bill-card-portal",
            "session_id": session_id,
            "portal_url": portal_url,
            "status": result.status.value,
            "message": result.message,
            "waiting_for_user": result.status == BrowserStepStatus.WAITING_FOR_USER,
            "resumable": result.status == BrowserStepStatus.WAITING_FOR_USER,
            "parameters": parameters or {},
        }
        if result.authenticated is not None:
            payload["authenticated"] = result.authenticated
        if page_text is not None:
            payload["page_text"] = page_text
        return payload


def _fill_values(actions: tuple[BrowserAction, ...] | None) -> tuple[str, ...]:
    if not actions:
        return ()
    values: list[str] = []
    for action in actions:
        if action.action_type != BrowserActionType.FILL:
            continue
        value = action.parameters.get("value")
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)
