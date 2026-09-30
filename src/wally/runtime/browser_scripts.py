"""Default browser action scripts for execution capabilities."""

from __future__ import annotations

from typing import Any

from wally.models.browser import BrowserAction, BrowserActionType
from wally.runtime.secrets_safety import auth_success_selector, auth_success_url_contains


def card_portal_pre_auth_actions(bill: dict[str, Any]) -> tuple[BrowserAction, ...]:
    """Pause for manual portal login."""
    provider = bill.get("provider") or bill.get("payee") or "payment portal"
    return (
        BrowserAction(
            BrowserActionType.WAIT_FOR_USER,
            {
                "reason": (
                    f"Log in to the {provider} payment portal manually, "
                    "then resume this payment when ready."
                ),
            },
        ),
    )


def card_portal_post_auth_actions(
    bill: dict[str, Any],
    *,
    extra: tuple[BrowserAction, ...] = (),
) -> tuple[BrowserAction, ...]:
    """Verify login or read the page. Never clicks pay or submit-payment controls."""
    selector = auth_success_selector(bill)
    url_contains = auth_success_url_contains(bill)
    actions: list[BrowserAction] = []
    if selector or url_contains:
        params: dict[str, Any] = {}
        if selector:
            params["selector"] = selector
        if url_contains:
            params["url_contains"] = url_contains
        actions.append(BrowserAction(BrowserActionType.VERIFY_AUTH, params))
    else:
        actions.append(BrowserAction(BrowserActionType.READ_PAGE, {}))
    actions.extend(extra)
    return tuple(actions)


def portal_verify_auth_actions(bill: dict[str, Any]) -> tuple[BrowserAction, ...]:
    """Read-only login check. Empty when Knowledge configures no success condition."""
    selector = auth_success_selector(bill)
    url_contains = auth_success_url_contains(bill)
    if not selector and not url_contains:
        return ()
    params: dict[str, Any] = {}
    if selector:
        params["selector"] = selector
    if url_contains:
        params["url_contains"] = url_contains
    return (BrowserAction(BrowserActionType.VERIFY_AUTH, params),)


def card_portal_payment_actions(
    bill: dict[str, Any],
    *,
    extra: tuple[BrowserAction, ...] = (),
) -> tuple[BrowserAction, ...]:
    """Full card-portal script (pre-auth + post-auth)."""
    return (
        *card_portal_pre_auth_actions(bill),
        *card_portal_post_auth_actions(bill, extra=extra),
    )
