"""Deterministic proposal generation. Pure rules over reconciled Matters.

Turns one Matter plus its supporting Observations into at most one ProposedAction
candidate. Nothing here reads or writes the store, calls a provider, or transitions
a stored proposal: Step 4 owns comparison with persisted rows and every lifecycle
change. Inputs are never mutated.

Observations stay untrusted. Every display field is fixed template text, so no
observation or Matter body can reach the rendered proposal. Only Wally-owned
reconciled Matter fields and an allowlist of reference identifiers are used, and the
minimized Matter title is hashed for change detection without ever being persisted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from wally.models.ops import (
    Matter,
    MatterDomain,
    MatterStatus,
    Observation,
    ObservationCategory,
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)
from wally.ops.priority import parse_time
from wally.ops.text import clean_title, normalized_amount

INJECTION_FLAG = "injection_suspected"

# Fixed wording. No observation or Matter body text is ever interpolated here.
# Proposals render beneath their Matter, which already displays the subject.
PREPARE_FOR_EVENT_TITLE = "Prepare for upcoming event"
PREPARE_FOR_EVENT_RATIONALE = "Open calendar commitment with a confirmed upcoming start time."
PREPARE_FOR_EVENT_SUGGESTION = "Set aside time to prepare before this event starts."
REVIEW_BILL_TITLE = "Review open bill"
REVIEW_BILL_RATIONALE = "Open finance matter supported by a bill or a recurring obligation."
REVIEW_BILL_SUGGESTION = "Review this bill and decide how to handle it."

_CALENDAR_SUPPORT = frozenset(
    {
        ObservationCategory.CALENDAR_UPCOMING,
        ObservationCategory.CALENDAR_CHANGED,
    }
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).isoformat()


def _normalized_time(value: str) -> str:
    parsed = parse_time(value)
    return _iso(parsed) if parsed is not None else ""


def hashed_title(title: str) -> str:
    """Minimize a Matter title as a content-hash input only.

    The result is never persisted or displayed: a retitled Matter is a material
    change, and hashing the minimized form detects that without copying Matter text
    into the proposal.
    """
    return clean_title(title)


def content_hash(fields: dict[str, object]) -> str:
    """Hash the material inputs of one proposal version.

    Volatile Matter fields are excluded by construction: callers pass only the
    identity, minimized title, due or event time, and reference identifiers.
    """
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint_for(matter_id: str, intent: ProposalIntent, digest: str) -> str:
    return f"{matter_id}:{intent.value}:{digest}"


def _supporting(matter: Matter, observations: Sequence[Observation]) -> list[Observation] | None:
    """Resolve every Observation the Matter references, or None if any is absent.

    Completeness is enforced here rather than trusted to the caller: accepting a
    partial set would let an omitted record hide an injection flag that should
    suppress the proposal. Duplicates collapse by id and unlinked extras are ignored.
    """
    required = set(matter.observation_ids)
    if not required:
        return None
    resolved: dict[str, Observation] = {}
    for item in observations:
        if item.id in required:
            resolved.setdefault(item.id, item)
    if resolved.keys() != required:
        return None
    return [resolved[key] for key in sorted(resolved)]


def _injection_suspected(observations: Sequence[Observation]) -> bool:
    return any(item.extra.get(INJECTION_FLAG) == "true" for item in observations)


def _bill_amounts(evidence: Sequence[Observation]) -> list[str]:
    found = {
        normalized
        for item in evidence
        if (normalized := normalized_amount(item.extra.get("amount", "")))
    }
    return sorted(found)


def _is_bill_evidence(observation: Observation) -> bool:
    if observation.category == ObservationCategory.INVOICE:
        return True
    return observation.category == ObservationCategory.KNOWLEDGE_OBLIGATION and observation.trusted


def propose_for_matter(
    matter: Matter,
    observations: Sequence[Observation],
    *,
    now: datetime,
) -> ProposedAction | None:
    """Return one candidate proposal for this Matter, or None when ineligible.

    Fails closed: any missing, inconsistent, or injection-flagged supporting
    evidence yields None rather than a weaker proposal.
    """
    if matter.status != MatterStatus.OPEN:
        return None
    support = _supporting(matter, observations)
    if support is None:
        return None
    if _injection_suspected(support):
        return None
    if matter.domain == MatterDomain.CALENDAR:
        return _prepare_for_event(matter, support, _aware(now))
    if matter.domain == MatterDomain.FINANCE:
        return _review_bill(matter, support, _aware(now))
    return None


def _prepare_for_event(
    matter: Matter, support: Sequence[Observation], now: datetime
) -> ProposedAction | None:
    start = parse_time(matter.due_at)
    if start is None or start <= now:
        return None
    events = [
        item
        for item in support
        if item.source == "calendar" and item.category in _CALENDAR_SUPPORT and item.source_id
    ]
    if not events:
        return None
    event_ids = {item.source_id for item in events}
    if len(event_ids) != 1:
        return None
    hashed = hashed_title(matter.title)
    if not hashed:
        return None
    event_id = events[0].source_id
    observation_ids = tuple(item.id for item in events)
    digest = content_hash(
        {
            "intent": ProposalIntent.PREPARE_FOR_EVENT.value,
            "matter_id": matter.id,
            "title": hashed,
            "event_start": _iso(start),
            "event_id": event_id,
            "observation_ids": list(observation_ids),
        }
    )
    return ProposedAction(
        id=str(uuid4()),
        fingerprint=fingerprint_for(matter.id, ProposalIntent.PREPARE_FOR_EVENT, digest),
        matter_id=matter.id,
        intent=ProposalIntent.PREPARE_FOR_EVENT,
        status=ProposalStatus.PROPOSED,
        provenance=ProposalProvenance.DETERMINISTIC_RULES,
        risk=ProposalRisk.LOW,
        created_at=_iso(now),
        updated_at=_iso(now),
        title=PREPARE_FOR_EVENT_TITLE,
        rationale=PREPARE_FOR_EVENT_RATIONALE,
        suggestion=PREPARE_FOR_EVENT_SUGGESTION,
        confidence=matter.confidence,
        content_hash=digest,
        expires_at=_iso(start),
        observation_ids=observation_ids,
        event_id=event_id,
    )


def _review_bill(
    matter: Matter, support: Sequence[Observation], now: datetime
) -> ProposedAction | None:
    evidence = [item for item in support if _is_bill_evidence(item)]
    if not evidence:
        return None
    hashed = hashed_title(matter.title)
    if not hashed:
        return None
    observation_ids = tuple(item.id for item in evidence)
    knowledge = {item for item in matter.knowledge_ids if item}
    knowledge.update(item.related_knowledge_id for item in evidence if item.related_knowledge_id)
    knowledge_ids = tuple(sorted(knowledge))
    due_at = _normalized_time(matter.due_at)
    fields: dict[str, object] = {
        "intent": ProposalIntent.REVIEW_BILL.value,
        "matter_id": matter.id,
        "title": hashed,
        "due_at": due_at,
        "knowledge_ids": list(knowledge_ids),
        "observation_ids": list(observation_ids),
        "thread_id": matter.thread_id,
    }
    # Omitted when absent so a v0.13 bill with no structured amount keeps its fingerprint.
    amounts = _bill_amounts(evidence)
    if amounts:
        fields["amounts"] = amounts
    digest = content_hash(fields)
    return ProposedAction(
        id=str(uuid4()),
        fingerprint=fingerprint_for(matter.id, ProposalIntent.REVIEW_BILL, digest),
        matter_id=matter.id,
        intent=ProposalIntent.REVIEW_BILL,
        status=ProposalStatus.PROPOSED,
        provenance=ProposalProvenance.DETERMINISTIC_RULES,
        risk=ProposalRisk.MEDIUM,
        created_at=_iso(now),
        updated_at=_iso(now),
        title=REVIEW_BILL_TITLE,
        rationale=REVIEW_BILL_RATIONALE,
        suggestion=REVIEW_BILL_SUGGESTION,
        confidence=matter.confidence,
        content_hash=digest,
        expires_at="",
        observation_ids=observation_ids,
        knowledge_ids=knowledge_ids,
        thread_id=matter.thread_id,
    )
