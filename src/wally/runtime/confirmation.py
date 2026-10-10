"""Runtime-owned, action-specific human confirmation (never a tool argument).

Providers are trusted composition-root dependencies. A provider must independently
show the entire review and authenticate the human; an agent's yes/boolean, bearer
session, configuration flag, or transcript is not an implementation of this API.
No ChatGPT/Codex verifier is registered by default.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class HumanReview:
    purpose: str
    principal: str
    channel: str
    correlation_id: str
    # Canonical JSON contains exact proposal identities, versions, selections,
    # old/new values and purpose. Immutable even across a provider callback.
    presentation: str
    nonce: str
    expires_at: float

    @property
    def fingerprint(self) -> str:
        return digest(self.__dict__)


class HumanConfirmationProvider(Protocol):
    method: str

    def confirm(self, review: HumanReview) -> bool:
        """Authenticate the owner and confirm this exact review synchronously."""


@dataclass(frozen=True)
class ConfirmationRecord:
    """Attribution only. Services obtain it directly from their authority."""

    id: str
    review_digest: str
    method: str
    purpose: str
    principal: str
    channel: str
    correlation_id: str
    confirmed_at: float
    expires_at: float
