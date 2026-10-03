"""Gateway envelopes and continuity handles.

Conversational items are untrusted evidence. They are not facts, approvals, or
part of a proposal fingerprint. An ActiveMatter is a cross-interface handle for
one ongoing issue. Its visibility says whether Wally should keep offering that
handle. The canonical Matter remains the state of the underlying issue.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

EVIDENCE_TEXT_MAX = 280
EVIDENCE_ITEM_MAX = 8
TITLE_MAX = 120

# Keys an external payload must not use to choose identity or authority.
FORBIDDEN_CLAIM_KEYS = frozenset(
    {
        "channel",
        "principal",
        "grant",
        "capability",
        "capabilities",
        "authentication",
    }
)


class EvidenceKind(StrEnum):
    LATEST_USER = "latest_user"
    PRIOR_USER = "prior_user"
    ASSISTANT_SUMMARY = "assistant_summary"
    EXTERNAL_REF = "external_ref"


class HandleVisibility(StrEnum):
    """Whether this continuity handle should still be offered.

    ``archived`` hides the handle. It does not resolve, pay, or close the
    canonical Matter.
    """

    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class EvidenceItem:
    """One capped, untrusted piece of conversational context."""

    kind: EvidenceKind
    text: str
    content_hash: str
    untrusted: bool = True

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "content_hash": self.content_hash,
            "untrusted": True,
        }


@dataclass(frozen=True)
class GatewayRequestRecord:
    id: str
    correlation_id: str
    active_matter_id: str
    channel: str
    principal: str
    external_session_ref: str
    external_request_ref: str
    created_at: str
    evidence: tuple[EvidenceItem, ...] = ()


@dataclass(frozen=True)
class ActiveSession:
    channel: str
    external_session_ref: str
    first_seen_at: str
    last_seen_at: str


@dataclass
class ActiveMatter:
    """A continuity handle. Not a second copy of Matter status."""

    id: str
    matter_id: str
    title: str
    visibility: HandleVisibility
    created_at: str
    updated_at: str
    correlation_ids: tuple[str, ...] = ()
    sessions: tuple[ActiveSession, ...] = field(default_factory=tuple)


def cap_text(value: object, limit: int) -> str:
    """Single-line text capped for storage. Not interpreted as a command."""
    if value is None:
        return ""
    text = _CONTROL.sub(" ", str(value))
    return " ".join(text.split())[:limit]


def evidence_hash(kind: EvidenceKind, text: str) -> str:
    payload = f"{kind.value}\n{text}".encode()
    return hashlib.sha256(payload).hexdigest()


def parse_evidence(raw: object) -> tuple[EvidenceItem, ...]:
    """Build a capped capsule. Client-supplied trust flags are ignored."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("evidence must be a list")
    if len(raw) > EVIDENCE_ITEM_MAX:
        raise ValueError(f"evidence accepts at most {EVIDENCE_ITEM_MAX} items")
    items: list[EvidenceItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each evidence item must be an object")
        kind_raw = entry.get("kind")
        try:
            kind = EvidenceKind(str(kind_raw))
        except ValueError as exc:
            raise ValueError("unknown evidence kind") from exc
        text = cap_text(entry.get("text"), EVIDENCE_TEXT_MAX)
        if not text:
            raise ValueError("evidence text is empty")
        items.append(
            EvidenceItem(kind=kind, text=text, content_hash=evidence_hash(kind, text))
        )
    return tuple(items)


def claim_keys(value: object, *, depth: int = 0) -> set[str]:
    """Names of forbidden identity fields anywhere in a payload."""
    found: set[str] = set()
    if depth > 8:
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            if name in FORBIDDEN_CLAIM_KEYS:
                found.add(name)
            found |= claim_keys(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            found |= claim_keys(item, depth=depth + 1)
    return found
