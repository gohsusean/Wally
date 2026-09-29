"""Runtime policy for execution-time secret access."""

from __future__ import annotations

import re
from typing import Any

from wally.runtime.policy import PolicyDecision

# 1Password secret references: op://vault/item/field[ /section...]
_OP_REFERENCE = re.compile(r"^op://[^/\s]+/[^/\s]+/.+$")

USERNAME_REF_KEYS = ("portal_username_ref", "login_username_ref")
PASSWORD_REF_KEYS = ("portal_password_ref", "login_password_ref")
USERNAME_SELECTOR_KEYS = ("login_username_selector",)
PASSWORD_SELECTOR_KEYS = ("login_password_selector",)
SUBMIT_SELECTOR_KEYS = ("login_submit_selector",)
AUTH_SUCCESS_SELECTOR_KEYS = ("auth_success_selector",)
AUTH_SUCCESS_URL_KEYS = ("auth_success_url_contains",)
WORKFLOW_SECRET_REFS_KEY = "workflow_secret_refs"


def evaluate_secret_reference(reference: str) -> PolicyDecision:
    """Allow only 1Password secret references — never raw credential values."""
    value = (reference or "").strip()
    if not value:
        return PolicyDecision(allowed=False, reason="Secret reference is empty.")
    if "\n" in value or "\r" in value:
        return PolicyDecision(allowed=False, reason="Secret reference must be a single line.")
    if not _OP_REFERENCE.match(value):
        return PolicyDecision(
            allowed=False,
            reason=(
                "Secret reference must be a 1Password pointer "
                "(op://vault/item/field). Raw credentials are not allowed."
            ),
        )
    return PolicyDecision(allowed=True)


def evaluate_secret_access(*, reference: str, authorized: bool) -> PolicyDecision:
    """Resolve only after runtime authorization (post-approval execution)."""
    if not authorized:
        return PolicyDecision(
            allowed=False,
            reason="Secret access requires runtime authorization after approval.",
        )
    return evaluate_secret_reference(reference)


def _first_str(knowledge: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = knowledge.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def portal_login_refs(knowledge: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        _first_str(knowledge, USERNAME_REF_KEYS),
        _first_str(knowledge, PASSWORD_REF_KEYS),
    )


def portal_login_selectors(knowledge: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        _first_str(knowledge, USERNAME_SELECTOR_KEYS),
        _first_str(knowledge, PASSWORD_SELECTOR_KEYS),
        _first_str(knowledge, SUBMIT_SELECTOR_KEYS),
    )


def auth_success_selector(knowledge: dict[str, Any]) -> str | None:
    return _first_str(knowledge, AUTH_SUCCESS_SELECTOR_KEYS)


def auth_success_url_contains(knowledge: dict[str, Any]) -> str | None:
    return _first_str(knowledge, AUTH_SUCCESS_URL_KEYS)


def workflow_secret_refs(knowledge: dict[str, Any]) -> dict[str, str]:
    raw = knowledge.get(WORKFLOW_SECRET_REFS_KEY)
    if not isinstance(raw, dict):
        return {}
    refs: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            refs[key.strip()] = value.strip()
    return refs


def redact_secret_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Copy parameters with FILL values and obvious secret fields removed."""
    redacted: dict[str, Any] = {}
    for key, value in parameters.items():
        lowered = key.lower()
        if any(token in lowered for token in ("password", "secret", "token", "credential")):
            redacted[key] = "[redacted]"
            continue
        if key == "value" and isinstance(value, str):
            redacted[key] = "[redacted]"
            continue
        redacted[key] = value
    return redacted


def scrub_secret_values(text: str, secrets: tuple[str, ...]) -> str:
    """Remove known secret values from text destined for logs or the model."""
    scrubbed = text
    for secret in secrets:
        if secret:
            scrubbed = scrubbed.replace(secret, "[redacted]")
    return scrubbed


def scrub_data(data: Any, secrets: tuple[str, ...]) -> Any:
    """Recursively scrub secret values from nested structures."""
    if not secrets:
        return data
    if isinstance(data, str):
        return scrub_secret_values(data, secrets)
    if isinstance(data, dict):
        return {key: scrub_data(value, secrets) for key, value in data.items()}
    if isinstance(data, list):
        return [scrub_data(item, secrets) for item in data]
    if isinstance(data, tuple):
        return tuple(scrub_data(item, secrets) for item in data)
    return data


def flatten_for_leak_search(data: Any) -> str:
    """Serialize mixed output for canary searches (not for production logging)."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return " ".join(
            f"{flatten_for_leak_search(key)} {flatten_for_leak_search(value)}"
            for key, value in data.items()
        )
    if isinstance(data, (list, tuple, set)):
        return " ".join(flatten_for_leak_search(item) for item in data)
    return repr(data)
