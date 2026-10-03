"""Authenticated caller and request provenance, independent of any interface.

A ``RequestContext`` is what a channel adapter (CLI, REPL, and later others) hands
to an application service. Only ``wally.runtime.principals.PrincipalAuthority``
issues one that authorizes anything; building these dataclasses by hand, or
naming a channel in text, grants nothing.

``RequestProvenance`` is the persisted, authority-free copy: where a request came
from and how to correlate it. It never decides whether something may happen, and
it is never part of a proposal fingerprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

REF_MAX = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Capability(StrEnum):
    """What an authenticated principal on a channel may ask Wally to do."""

    SUBMIT_REQUEST = "submit_request"
    READ_CONTEXT = "read_context"
    LINK_CHANNEL = "link_channel"
    DECIDE_PROPOSAL = "decide_proposal"
    EXECUTE_PROPOSAL = "execute_proposal"
    VERIFY_EXECUTION = "verify_execution"


def clean_ref(value: Any) -> str:
    """Single-line, length-capped opaque reference. Never interpreted."""
    if value is None:
        return ""
    text = _CONTROL.sub(" ", str(value))
    return " ".join(text.split())[:REF_MAX]


@dataclass(frozen=True)
class Principal:
    """Who is calling, through which channel, and how the channel authenticated them.

    ``grant`` is an opaque value from the issuing authority. It is not persisted.
    """

    subject: str
    channel: str
    authentication: str
    grant: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class RequestProvenance:
    """Where a request came from. Audit and correlation data only."""

    channel: str = ""
    principal: str = ""
    external_session_ref: str = ""
    external_request_ref: str = ""
    correlation_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("channel", self.channel),
                ("principal", self.principal),
                ("external_session_ref", self.external_session_ref),
                ("external_request_ref", self.external_request_ref),
                ("correlation_id", self.correlation_id),
            )
            if value
        }

    @classmethod
    def from_dict(cls, data: Any) -> RequestProvenance:
        if not isinstance(data, dict):
            return cls()
        return cls(
            channel=clean_ref(data.get("channel")),
            principal=clean_ref(data.get("principal")),
            external_session_ref=clean_ref(data.get("external_session_ref")),
            external_request_ref=clean_ref(data.get("external_request_ref")),
            correlation_id=clean_ref(data.get("correlation_id")),
        )

    def is_empty(self) -> bool:
        return not self.as_dict()


@dataclass(frozen=True)
class RequestContext:
    """An authenticated request as seen by application services."""

    principal: Principal
    correlation_id: str
    external_session_ref: str = ""
    external_request_ref: str = ""

    def provenance(self) -> RequestProvenance:
        return RequestProvenance(
            channel=self.principal.channel,
            principal=self.principal.subject,
            external_session_ref=self.external_session_ref,
            external_request_ref=self.external_request_ref,
            correlation_id=self.correlation_id,
        )
