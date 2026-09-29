"""Governed secret resolution — authorization and audit before provider calls."""

from __future__ import annotations

from typing import Any

from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.models.browser import BrowserAction, BrowserActionType
from wally.providers.secrets import SecretsProvider
from wally.runtime.secrets_safety import (
    evaluate_secret_access,
    portal_login_refs,
    portal_login_selectors,
    workflow_secret_refs,
)


class GovernedSecretsResolver:
    """Resolve secrets only after runtime authorization. Never log secret values."""

    def __init__(
        self,
        provider: SecretsProvider | None,
        *,
        audit: AuditLogger | None = None,
    ) -> None:
        self._provider = provider
        self._audit = audit

    @property
    def provider(self) -> SecretsProvider | None:
        return self._provider

    def is_available(self) -> bool:
        return self._provider is not None and self._provider.is_healthy()

    def resolve(
        self,
        reference: str,
        *,
        authorized: bool,
        session_id: str = "",
        purpose: str = "execution",
    ) -> str:
        policy = evaluate_secret_access(reference=reference, authorized=authorized)
        if not policy.allowed:
            self._log(
                session_id=session_id,
                outcome="denied",
                reference=reference,
                purpose=purpose,
                reason=policy.reason,
            )
            raise ProviderUnavailableError("secrets", policy.reason or "Secret access denied.")
        if self._provider is None or not self._provider.is_healthy():
            raise ProviderUnavailableError(
                "secrets",
                "SecretsProvider is not configured. Sign in to 1Password CLI (`op signin`) "
                "and enable providers.secrets in config.",
            )
        value = self._provider.resolve(reference)
        self._log(
            session_id=session_id,
            outcome="resolved",
            reference=reference,
            purpose=purpose,
        )
        return value

    def build_portal_login_actions(
        self,
        knowledge: dict[str, Any],
        *,
        authorized: bool,
        session_id: str = "",
    ) -> tuple[BrowserAction, ...] | None:
        """Return FILL/CLICK actions when Knowledge has refs + selectors and secrets are healthy."""
        if not self.is_available():
            return None
        username_ref, password_ref = portal_login_refs(knowledge)
        user_sel, pass_sel, submit_sel = portal_login_selectors(knowledge)
        if not username_ref or not password_ref or not user_sel or not pass_sel:
            return None

        username = self.resolve(
            username_ref, authorized=authorized, session_id=session_id, purpose="portal_login"
        )
        password = self.resolve(
            password_ref, authorized=authorized, session_id=session_id, purpose="portal_login"
        )
        actions: list[BrowserAction] = [
            BrowserAction(
                BrowserActionType.FILL,
                {"selector": user_sel, "value": username},
            ),
            BrowserAction(
                BrowserActionType.FILL,
                {"selector": pass_sel, "value": password},
            ),
        ]
        if submit_sel:
            actions.append(BrowserAction(BrowserActionType.CLICK, {"selector": submit_sel}))
        return tuple(actions)

    def inject_workflow_parameters(
        self,
        *,
        knowledge: dict[str, Any],
        parameters: dict[str, object] | None,
        authorized: bool,
        session_id: str = "",
    ) -> dict[str, object]:
        """Merge runtime-resolved secrets from Knowledge `workflow_secret_refs` into n8n params."""
        merged: dict[str, object] = dict(parameters or {})
        refs = workflow_secret_refs(knowledge)
        if not refs:
            return merged
        if not self.is_available():
            raise ProviderUnavailableError(
                "secrets",
                "This payment lists workflow_secret_refs but SecretsProvider is unavailable.",
            )
        for name, reference in refs.items():
            merged[name] = self.resolve(
                reference,
                authorized=authorized,
                session_id=session_id,
                purpose=f"workflow:{name}",
            )
        return merged

    def _log(
        self,
        *,
        session_id: str,
        outcome: str,
        reference: str,
        purpose: str,
        reason: str | None = None,
    ) -> None:
        if self._audit is None:
            return
        parameters: dict[str, Any] = {"reference": reference, "purpose": purpose}
        if reason:
            parameters["reason"] = reason
        self._audit.log_simple(
            event_type="secrets_resolve",
            session_id=session_id or "runtime",
            outcome=outcome,
            provider="secrets",
            parameters=parameters,
        )
