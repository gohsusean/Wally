"""Browser automation safety — trusted portal URL policy."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from wally.models.browser import BrowserAction, BrowserActionType
from wally.runtime.policy import PolicyDecision

TRUSTED_PORTAL_KNOWLEDGE_KEYS = (
    "payment_portal_url",
    "portal_url",
)

UNTRUSTED_URL_SOURCE_HINT = (
    "Browser automation may only navigate to portal URLs from approved Knowledge Assets. "
    "URLs from emails, web pages, PDFs, unverified user text, or LLM output are not "
    "permitted as navigation targets."
)

MISSING_TRUSTED_PORTAL_MESSAGE = (
    "Payment portal URL is not in the trusted Knowledge Asset. "
    "Add and approve payment_portal_url in knowledge before browser execution."
)


def trusted_portal_url_from_knowledge(source: dict[str, Any]) -> str | None:
    """Extract the trusted portal URL from a Knowledge Asset / bill dict."""
    for key in TRUSTED_PORTAL_KNOWLEDGE_KEYS:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def normalize_portal_url(url: str) -> str:
    """Canonical portal URL for trusted comparison."""
    text = str(url).strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    query = ""
    if parsed.query:
        query = f"?{parsed.query}"
    return f"{scheme}://{netloc}{path}{query}"


def portal_urls_match(trusted: str, requested: str) -> bool:
    return normalize_portal_url(trusted) == normalize_portal_url(requested)


def evaluate_trusted_portal_navigation(
    *,
    requested_url: str,
    knowledge: dict[str, Any],
) -> PolicyDecision:
    """Allow navigation only to the trusted portal URL from Knowledge."""
    trusted_url = trusted_portal_url_from_knowledge(knowledge)
    if not trusted_url:
        return PolicyDecision(allowed=False, reason=MISSING_TRUSTED_PORTAL_MESSAGE)

    if not str(requested_url).strip():
        return PolicyDecision(
            allowed=False,
            reason="Browser navigation URL is required.",
        )

    if not portal_urls_match(trusted_url, requested_url):
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Navigation blocked: requested URL does not match the trusted portal "
                f"URL from the Knowledge Asset ({trusted_url}). {UNTRUSTED_URL_SOURCE_HINT}"
            ),
        )

    return PolicyDecision(allowed=True)


def evaluate_browser_session_actions(
    *,
    actions: tuple[BrowserAction, ...],
    knowledge: dict[str, Any],
) -> PolicyDecision:
    """Validate NAVIGATE actions against the trusted Knowledge portal URL."""
    trusted_url = trusted_portal_url_from_knowledge(knowledge)
    if not trusted_url:
        return PolicyDecision(allowed=False, reason=MISSING_TRUSTED_PORTAL_MESSAGE)

    for action in actions:
        if action.action_type == BrowserActionType.SCREENSHOT:
            return PolicyDecision(
                allowed=False,
                reason=(
                    "Screenshots, traces, and other browser diagnostic artifacts are "
                    "disabled by default so credentials and authenticated pages are "
                    "not written to disk."
                ),
            )
        if action.action_type != BrowserActionType.NAVIGATE:
            continue
        requested = action.parameters.get("url")
        if requested is None or not str(requested).strip():
            return PolicyDecision(
                allowed=False,
                reason="NAVIGATE action requires a url parameter.",
            )
        if not portal_urls_match(trusted_url, str(requested)):
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Navigation blocked: action URL does not match the trusted portal "
                    f"URL from the Knowledge Asset ({trusted_url}). "
                    f"{UNTRUSTED_URL_SOURCE_HINT}"
                ),
            )

    return PolicyDecision(allowed=True)


def resolve_trusted_portal_url(knowledge: dict[str, Any]) -> tuple[str | None, PolicyDecision]:
    """Return the trusted portal URL or a policy denial."""
    trusted_url = trusted_portal_url_from_knowledge(knowledge)
    if not trusted_url:
        return None, PolicyDecision(allowed=False, reason=MISSING_TRUSTED_PORTAL_MESSAGE)
    return trusted_url, PolicyDecision(allowed=True)
