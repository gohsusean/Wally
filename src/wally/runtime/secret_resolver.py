"""Governed secret resolution — authorization and audit before provider calls."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.models.browser import BrowserAction, BrowserActionType
from wally.providers.secrets import SecretsProvider
from wally.runtime.secrets_safety import (
    evaluate_secret_access,
    evaluate_secret_reference,
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
        # Readiness is advisory, not credential authorization. Probing the app
        # before each read can require a second desktop authentication; the
        # provider's actual read enforces its own access and fails closed.
        if self._provider is None:
            raise ProviderUnavailableError(
                "secrets",
                "SecretsProvider is unavailable. Enable providers.secrets in config and "
                "unlock/authorize 1Password CLI in the desktop app, or use `op signin`.",
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
        if self._provider is None:
            return None
        username_ref, password_ref = portal_login_refs(knowledge)
        user_sel, pass_sel, submit_sel = portal_login_selectors(knowledge)
        if not username_ref or not password_ref or not user_sel or not pass_sel:
            return None

        username, password = self.resolve_many(
            (username_ref, password_ref),
            authorized=authorized,
            session_id=session_id,
            purpose="portal_login",
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

    def resolve_many(
        self,
        references: tuple[str, ...],
        *,
        authorized: bool,
        session_id: str = "",
        purpose: str = "execution",
    ) -> tuple[str, ...]:
        """Authorize every reference before a scoped batch read; never cache values."""
        for reference in references:
            policy = evaluate_secret_access(reference=reference, authorized=authorized)
            if not policy.allowed:
                self._log(
                    session_id=session_id,
                    outcome="denied",
                    reference=reference,
                    purpose=purpose,
                    reason=policy.reason,
                )
                raise ProviderUnavailableError("secrets", "Secret access denied by runtime policy.")
        if self._provider is None:
            raise ProviderUnavailableError("secrets", "SecretsProvider is unavailable.")
        batch = getattr(self._provider, "resolve_many", None)
        try:
            values = (
                batch(references)
                if batch
                else tuple(self._provider.resolve(ref) for ref in references)
            )
            if len(values) != len(references) or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise ProviderUnavailableError("secrets", "Credential response was incomplete.")
        except ProviderUnavailableError:
            for reference in references:
                self._log(
                    session_id=session_id,
                    outcome="unavailable",
                    reference=reference,
                    purpose=purpose,
                    reason="Provider did not complete the scoped read.",
                )
            raise
        for reference in references:
            self._log(
                session_id=session_id, outcome="resolved", reference=reference, purpose=purpose
            )
        return tuple(values)

    def resolve_totp(self, reference: str, *, authorized: bool, session_id: str = "") -> str:
        """Ask 1Password for a current code, never read the TOTP seed into Wally.

        The reviewed policy supplies a plain field reference. The runtime alone
        adds the documented attribute query; arbitrary query arguments are denied.
        """
        parts = urlsplit(reference)
        if parts.scheme != "op" or parts.query or parts.fragment:
            raise ProviderUnavailableError("secrets", "TOTP requires a plain 1Password field ref.")
        value = self.resolve(
            reference + "?attribute=otp",
            authorized=authorized,
            session_id=session_id,
            purpose="payment_totp",
        )
        if not value.isascii() or not value.isdigit() or len(value) not in {6, 8}:
            raise ProviderUnavailableError("secrets", "1Password did not return a valid TOTP code.")
        return value

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
        safe_reference = (
            reference if evaluate_secret_reference(reference).allowed else "[invalid reference]"
        )
        parameters: dict[str, Any] = {"reference": safe_reference, "purpose": purpose}
        if reason:
            parameters["reason"] = reason
        self._audit.log_simple(
            event_type="secrets_resolve",
            session_id=session_id or "runtime",
            outcome=outcome,
            provider="secrets",
            parameters=parameters,
        )
