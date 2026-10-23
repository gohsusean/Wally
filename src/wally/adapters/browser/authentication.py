"""Observable login steps on the existing Playwright session; no payment actions.

Only trusted runtime/operator code supplies reviewed selectors. This is not an
LLM tool, browser system, retry loop or authentication-challenge solver.
"""

import time
from dataclasses import dataclass
from enum import StrEnum


class LoginState(StrEnum):
    ADVANCED = "advanced"
    CHALLENGE = "challenge_requires_owner"
    REJECTED = "input_rejected"
    MISSING = "input_missing"
    HIDDEN = "input_hidden"
    DISABLED = "input_disabled"
    READ_ONLY = "input_read_only"
    READY = "input_ready"
    STALLED = "transition_not_observed"
    ORIGIN_CHANGED = "origin_changed"
    FAILED = "interaction_failed"
    AUTHORIZATION_CHANGED = "authorization_changed"


@dataclass(frozen=True)
class LoginStep:
    input_selector: str
    next_selectors: tuple[str, ...]
    submit_selector: str = ""  # empty means the form's supported Enter key
    rejection_selector: str = ""
    challenge_selectors: tuple[str, ...] = ()
    timeout_ms: int = 15000
    next_frame_selector: str = ""  # Only the observed next state may be inside a reviewed frame.

    def __post_init__(self):
        if not self.input_selector or not self.next_selectors or not 1 <= self.timeout_ms <= 30000:
            raise ValueError("Login step needs a field, observable next state and bounded timeout.")


@dataclass(frozen=True)
class LoginResult:
    state: LoginState
    submission_attempted: bool = False
    network_errors: tuple[str, ...] = ()
    http_error_statuses: tuple[int, ...] = ()


# Browser-supplied diagnostics only. Never return a raw exception/failure string:
# unexpected strings can contain URLs, credentials or provider response text.
NETWORK_ERROR_CODES = frozenset(
    "net::" + code
    for code in (
        "ERR_HTTP2_PROTOCOL_ERROR",
        "ERR_QUIC_PROTOCOL_ERROR",
        "ERR_NAME_NOT_RESOLVED",
        "ERR_NAME_RESOLUTION_FAILED",
        "ERR_CONNECTION_REFUSED",
        "ERR_CONNECTION_RESET",
        "ERR_CONNECTION_CLOSED",
        "ERR_CONNECTION_ABORTED",
        "ERR_CONNECTION_TIMED_OUT",
        "ERR_TIMED_OUT",
        "ERR_EMPTY_RESPONSE",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_NETWORK_CHANGED",
        "ERR_ADDRESS_UNREACHABLE",
        "ERR_SSL_PROTOCOL_ERROR",
        "ERR_CERT_AUTHORITY_INVALID",
        "ERR_CERT_DATE_INVALID",
        "ERR_CERT_COMMON_NAME_INVALID",
        "ERR_PROXY_CONNECTION_FAILED",
        "ERR_TUNNEL_CONNECTION_FAILED",
        "ERR_BLOCKED_BY_CLIENT",
        "ERR_BLOCKED_BY_RESPONSE",
        "ERR_ABORTED",
        "ERR_FAILED",
    )
)


def field_state(page, selector):
    """Return shape only; never read input values, labels or authenticated text."""
    field = page.locator(selector)
    if field.count() != 1:
        return LoginState.MISSING
    if not field.is_visible():
        return LoginState.HIDDEN
    if not field.is_enabled():
        return LoginState.DISABLED
    if field.get_attribute("readonly") is not None:
        return LoginState.READ_ONLY
    return LoginState.READY


def _visible(page, selector):
    return bool(selector and page.locator(selector).is_visible())


def _observe(page, step):
    if any(_visible(page, selector) for selector in step.challenge_selectors):
        return LoginState.CHALLENGE
    if _visible(page, step.rejection_selector):
        return LoginState.REJECTED
    next_page = page.frame_locator(step.next_frame_selector) if step.next_frame_selector else page
    if any(_visible(next_page, selector) for selector in step.next_selectors):
        return LoginState.ADVANCED
    return None


def advance_login(page, step, value, *, check_origin, before_submit):
    """Fill/submit once and observe a transition; never assume a click is success.

    Exceptions may contain credentials or DOM data. Return only fixed states,
    including when a timeout happens after a potentially successful submission.
    The caller retains the session for owner-required bank challenges, then closes
    it. No state or credentials are persisted and no automatic retry is performed.
    """
    submitted = False
    try:
        try:
            check_origin()
        except Exception:
            return LoginResult(LoginState.ORIGIN_CHANGED)
        if any(_visible(page, selector) for selector in step.challenge_selectors):
            return LoginResult(LoginState.CHALLENGE)
        state = field_state(page, step.input_selector)
        if state != LoginState.READY:
            return LoginResult(state)
        try:
            before_submit()  # A credential prompt may have outlived the reviewed scope.
        except Exception:
            return LoginResult(LoginState.AUTHORIZATION_CHANGED)
        try:
            check_origin()
        except Exception:
            return LoginResult(LoginState.ORIGIN_CHANGED)
        field = page.locator(step.input_selector)
        field.fill(value, timeout=step.timeout_ms)
        if field.input_value() != value:
            return LoginResult(LoginState.REJECTED)
        try:
            before_submit()
        except Exception:
            return LoginResult(LoginState.AUTHORIZATION_CHANGED)
        try:
            check_origin()
        except Exception:
            return LoginResult(LoginState.ORIGIN_CHANGED)
        submitted = True
        try:
            if step.submit_selector:
                page.locator(step.submit_selector).click(timeout=step.timeout_ms)
            else:
                field.press("Enter", timeout=step.timeout_ms)
        except Exception:
            # A click timeout does not establish that nothing happened.
            pass
        deadline = time.monotonic() + step.timeout_ms / 1000
        while True:
            try:
                check_origin()
            except Exception:
                return LoginResult(LoginState.ORIGIN_CHANGED, submitted)
            state = _observe(page, step)
            if state:
                return LoginResult(state, submitted)
            if time.monotonic() >= deadline:
                return LoginResult(LoginState.STALLED, submitted)
            page.wait_for_timeout(100)
    except Exception:
        return LoginResult(LoginState.FAILED, submitted)
