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
import time
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
from wally.runtime.confirmation import (
    ConfirmationRecord,
    HumanConfirmationProvider,
    HumanReview,
    canonical,
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
        human_confirmers: Mapping[str, HumanConfirmationProvider] | None = None,
    ) -> None:
        self._channels = dict(LOCAL_OPERATOR_CHANNELS if channels is None else channels)
        self._owner = owner
        self._key = secrets.token_bytes(32)
        self._human_confirmers = dict(human_confirmers or {})
        self._confirmations: dict[str, ConfirmationRecord] = {}

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
        if not isinstance(context, RequestContext) or not isinstance(context.principal, Principal):
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

    def confirm_human(
        self,
        context: RequestContext,
        capability: Capability,
        *,
        presentation: object,
        lifetime: float = 300,
    ) -> ConfirmationRecord:
        """Only trusted runtime code calls the registered human verifier.

        There is deliberately no accept_confirmation(bool/token/meta) endpoint.
        A capability authenticates a requester; this fresh interaction authenticates
        the human's exact decision. Each call creates a new bounded review.
        """
        self.authorize(context, capability)
        provider = self._human_confirmers.get(context.principal.channel)
        if provider is None or not provider.method:
            raise AuthorizationError("No trustworthy human confirmation provider for this channel.")
        if not 0 < lifetime <= 300:
            raise AuthorizationError("Invalid confirmation lifetime.")
        review = HumanReview(
            capability.value,
            context.principal.subject,
            context.principal.channel,
            context.correlation_id,
            canonical(presentation),
            secrets.token_hex(24),
            time.time() + lifetime,
        )
        try:
            confirmed = provider.confirm(review)
        except Exception:
            confirmed = False
        self.authorize(context, capability)
        if confirmed is not True or time.time() >= review.expires_at:
            raise AuthorizationError("Human confirmation denied, unavailable, or expired.")
        record = ConfirmationRecord(
            review.nonce,
            review.fingerprint,
            provider.method,
            review.purpose,
            review.principal,
            review.channel,
            review.correlation_id,
            time.time(),
            review.expires_at,
        )
        self._confirmations[record.id] = record
        return record

    def consume_confirmation(
        self,
        record: ConfirmationRecord,
        context: RequestContext,
        capability: Capability,
        *,
        presentation: object,
    ) -> None:
        """Consume only this authority's fresh confirmation of this exact request.

        Persisted ConfirmationRecord fields have attribution only. Neither a copied
        record, a foreign authority record, nor a replay can authorize another call.
        """
        self.authorize(context, capability)
        review = HumanReview(
            capability.value,
            context.principal.subject,
            context.principal.channel,
            context.correlation_id,
            canonical(presentation),
            record.id,
            record.expires_at,
        )
        issued = self._confirmations.pop(record.id, None)
        if (
            issued is not record
            or time.time() >= record.expires_at
            or (record.review_digest != review.fingerprint)
        ):
            raise AuthorizationError(
                "Human confirmation is foreign, stale, replayed, or out of scope."
            )

    def _sign(self, subject: str, channel: str, authentication: str) -> str:
        message = "\x1f".join((subject, channel, authentication)).encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()
