"""Trusted portal URL policy tests for browser automation."""

from wally.models.browser import BrowserAction, BrowserActionType
from wally.runtime.browser_safety import (
    MISSING_TRUSTED_PORTAL_MESSAGE,
    evaluate_browser_session_actions,
    evaluate_trusted_portal_navigation,
    normalize_portal_url,
    portal_urls_match,
    resolve_trusted_portal_url,
    trusted_portal_url_from_knowledge,
)


def _knowledge(portal: str | None = None) -> dict:
    if portal is None:
        return {"provider": "Streaming Co"}
    return {"provider": "Streaming Co", "payment_portal_url": portal}


def test_trusted_portal_url_from_knowledge() -> None:
    assert (
        trusted_portal_url_from_knowledge(
            {"payment_portal_url": "https://pay.example.com/streaming"}
        )
        == "https://pay.example.com/streaming"
    )
    assert trusted_portal_url_from_knowledge({"provider": "X"}) is None


def test_normalize_portal_url_ignores_trailing_slash_and_case() -> None:
    assert portal_urls_match(
        "https://Pay.Example.COM/portal/",
        "https://pay.example.com/portal",
    )


def test_navigation_allowed_when_url_matches_knowledge() -> None:
    decision = evaluate_trusted_portal_navigation(
        requested_url="https://pay.example.com/portal",
        knowledge=_knowledge("https://pay.example.com/portal/"),
    )
    assert decision.allowed


def test_navigation_denied_when_knowledge_missing_portal_url() -> None:
    decision = evaluate_trusted_portal_navigation(
        requested_url="https://pay.example.com/portal",
        knowledge=_knowledge(),
    )
    assert not decision.allowed
    assert MISSING_TRUSTED_PORTAL_MESSAGE in (decision.reason or "")


def test_navigation_denied_for_statement_or_email_url() -> None:
    """URLs from external evidence must not become navigation targets."""
    decision = evaluate_trusted_portal_navigation(
        requested_url="https://phishing.example.com/login",
        knowledge=_knowledge("https://pay.example.com/portal"),
    )
    assert not decision.allowed
    assert "does not match" in (decision.reason or "").lower()
    assert "Knowledge Asset" in (decision.reason or "")


def test_resolve_trusted_portal_url_returns_denial_when_missing() -> None:
    url, decision = resolve_trusted_portal_url(_knowledge())
    assert url is None
    assert not decision.allowed


def test_resolve_trusted_portal_url_returns_knowledge_url() -> None:
    url, decision = resolve_trusted_portal_url(
        _knowledge("https://pay.example.com/portal")
    )
    assert url == "https://pay.example.com/portal"
    assert decision.allowed


def test_session_navigate_actions_must_match_trusted_portal() -> None:
    decision = evaluate_browser_session_actions(
        actions=(
            BrowserAction(
                BrowserActionType.NAVIGATE,
                {"url": "https://pay.example.com/portal/bills"},
            ),
        ),
        knowledge=_knowledge("https://pay.example.com/portal"),
    )
    assert not decision.allowed

    decision_ok = evaluate_browser_session_actions(
        actions=(
            BrowserAction(
                BrowserActionType.NAVIGATE,
                {"url": "https://pay.example.com/portal"},
            ),
        ),
        knowledge=_knowledge("https://pay.example.com/portal/"),
    )
    assert decision_ok.allowed


def test_non_navigate_actions_do_not_require_url_match() -> None:
    decision = evaluate_browser_session_actions(
        actions=(
            BrowserAction(BrowserActionType.CLICK, {"selector": "#pay"}),
            BrowserAction(BrowserActionType.WAIT_FOR_USER, {"reason": "login"}),
        ),
        knowledge=_knowledge("https://pay.example.com/portal"),
    )
    assert decision.allowed


def test_normalize_portal_url_adds_https_scheme() -> None:
    assert normalize_portal_url("pay.example.com/portal") == "https://pay.example.com/portal"
