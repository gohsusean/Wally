"""Principal authority: the one place that turns a channel into authorization.

Channel adapters ask the authority for a ``RequestContext``. Application services
ask the authority whether that context holds a capability. Services never compare
channel names themselves, so adding a channel is a registration here, not a change
to approval or Act & Verify.

A context authorizes only if this authority instance issued it: the principal
carries an HMAC over its subject, channel, and authentication under a per-process
key. A hand-built ``Principal(channel="cli")``, a channel named in email or model
text, or a context from another authority instance fails the check.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import uuid4

from wally.exceptions import AuthorizationError
from wally.models.principal import (
    Capability,
    Principal,
    RequestContext,
    clean_ref,
)

DEFAULT_OWNER = "owner"
LOCAL_TERMINAL = "local_terminal"


@dataclass(frozen=True)
class ChannelPolicy:
    authentication: str
    capabilities: frozenset[Capability]


# The local operator at the terminal is Sean. Both local channels may decide,
# execute, and verify. A future remote channel registers its own, narrower policy.
LOCAL_OPERATOR_CHANNELS: Mapping[str, ChannelPolicy] = {
    "cli": ChannelPolicy(LOCAL_TERMINAL, frozenset(Capability)),
    "repl": ChannelPolicy(LOCAL_TERMINAL, frozenset(Capability)),
}


def new_correlation_id() -> str:
    return f"req_{uuid4().hex[:16]}"


class PrincipalAuthority:
    def __init__(
        self,
        channels: Mapping[str, ChannelPolicy] | None = None,
        *,
        owner: str = DEFAULT_OWNER,
    ) -> None:
        self._channels = dict(LOCAL_OPERATOR_CHANNELS if channels is None else channels)
        self._owner = owner
        self._key = secrets.token_bytes(32)

    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(self._channels))

    def issue(
        self,
        channel: str,
        *,
        external_session_ref: str = "",
        external_request_ref: str = "",
        correlation_id: str = "",
    ) -> RequestContext:
        """Issue a context for a caller the channel adapter has authenticated.

        Only registered channels can be issued. The subject is the configured owner;
        Wally is single-user.
        """
        policy = self._channels.get(channel)
        if policy is None:
            raise AuthorizationError(f"Channel {channel!r} is not registered with Wally.")
        principal = Principal(
            subject=self._owner,
            channel=channel,
            authentication=policy.authentication,
            grant=self._sign(self._owner, channel, policy.authentication),
        )
        return RequestContext(
            principal=principal,
            correlation_id=clean_ref(correlation_id) or new_correlation_id(),
            external_session_ref=clean_ref(external_session_ref),
            external_request_ref=clean_ref(external_request_ref),
        )

    def check(self, context: object, capability: Capability) -> str | None:
        """Return why ``context`` may not use ``capability``, or None when it may."""
        if not isinstance(context, RequestContext) or not isinstance(
            context.principal, Principal
        ):
            return "Request is not from an authenticated Wally channel."
        principal = context.principal
        expected = self._sign(principal.subject, principal.channel, principal.authentication)
        if not principal.grant or not hmac.compare_digest(principal.grant, expected):
            return "Request is not from an authenticated Wally channel."
        policy = self._channels.get(principal.channel)
        if policy is None or policy.authentication != principal.authentication:
            return f"Channel {principal.channel!r} is not registered with Wally."
        if capability not in policy.capabilities:
            return f"Channel {principal.channel!r} may not {capability.value.replace('_', ' ')}."
        return None

    def authorize(self, context: object, capability: Capability) -> RequestContext:
        reason = self.check(context, capability)
        if reason is not None:
            raise AuthorizationError(reason)
        assert isinstance(context, RequestContext)
        return context

    def _sign(self, subject: str, channel: str, authentication: str) -> str:
        message = "\x1f".join((subject, channel, authentication)).encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()
