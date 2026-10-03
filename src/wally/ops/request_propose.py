"""Turn a grounded ad-hoc request into one canonical proposal.

The request text is not hashed. Intent, fingerprint, and status come from the
same proposal functions Observe already uses. ChatGPT does not build the row.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from wally.models.ops import (
    Matter,
    MatterDomain,
    MatterStatus,
    ProposalStatus,
    ProposedAction,
)
from wally.models.principal import RequestProvenance
from wally.ops.propose import propose_deliver_document
from wally.ops.store import OperationsStore

_OPEN = frozenset(
    {ProposalStatus.PROPOSED, ProposalStatus.APPROVED, ProposalStatus.DEFERRED}
)


@dataclass(frozen=True)
class TrustedRecord:
    """A canonical document or recipient Wally already trusts."""

    id: str
    title: str
    kind: str


@dataclass(frozen=True)
class DeliveryGrounding:
    document: TrustedRecord
    recipient: TrustedRecord


class GroundingError(Exception):
    """The request does not map to exactly one document and one recipient."""

    def __init__(self, message: str, *, candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.candidates = candidates


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _unique(records: tuple[TrustedRecord, ...], hint: str, kind: str) -> TrustedRecord:
    key = _norm(hint)
    if not key:
        raise GroundingError(f"Name the {kind} exactly as it is titled in Wally.")
    matches = [item for item in records if item.kind == kind and _norm(item.title) == key]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        titled = tuple(item.title for item in records if item.kind == kind)
        raise GroundingError(
            f"No single trusted {kind} matches that name.",
            candidates=titled,
        )
    raise GroundingError(
        f"More than one trusted {kind} matches that name.",
        candidates=tuple(item.title for item in matches),
    )


def ground_delivery(
    records: tuple[TrustedRecord, ...],
    *,
    document_hint: str,
    recipient_hint: str,
) -> DeliveryGrounding:
    """Exact title match only. A partial or duplicate name does not write."""
    return DeliveryGrounding(
        document=_unique(records, document_hint, "document"),
        recipient=_unique(records, recipient_hint, "recipient"),
    )


def ensure_delivery_matter(
    store: OperationsStore,
    grounding: DeliveryGrounding,
    *,
    now: datetime,
) -> Matter:
    """One open Matter for this document and recipient, titled from the document."""
    fingerprint = f"deliver:{grounding.document.id}:{grounding.recipient.id}"
    existing = store.get_matter_by_fingerprint(fingerprint)
    if existing is not None:
        return existing
    stamp = now.isoformat()
    matter = Matter(
        id=f"matter_{hashlib.sha256(fingerprint.encode()).hexdigest()[:16]}",
        fingerprint=fingerprint,
        title=grounding.document.title,
        domain=MatterDomain.ADMIN,
        status=MatterStatus.OPEN,
        created_at=stamp,
        updated_at=stamp,
        summary="Trusted document ready to deliver.",
        open_reason="Trusted document and recipient",
        last_change="Request grounded in knowledge",
        knowledge_ids=(grounding.document.id, grounding.recipient.id),
        source="knowledge",
    )
    store.save_matter(matter)
    return matter


def attach_delivery(
    store: OperationsStore,
    grounding: DeliveryGrounding,
    *,
    active_matter_id: str,
    provenance: RequestProvenance,
    now: datetime,
) -> tuple[ProposedAction, str]:
    """Create the canonical matter and proposal, and choose one continuity handle.

    A handle that already points at a different matter is refused before the
    proposal is inserted.
    """
    fingerprint = f"deliver:{grounding.document.id}:{grounding.recipient.id}"
    existing_matter = store.get_matter_by_fingerprint(fingerprint)
    if active_matter_id:
        handle = store.get_active_matter(active_matter_id)
        if handle is None:
            raise GroundingError(f"Unknown active matter: {active_matter_id}")
        if existing_matter is not None and handle.matter_id not in {"", existing_matter.id}:
            raise GroundingError("That handle belongs to a different matter.")
        if existing_matter is None and handle.matter_id:
            raise GroundingError("That handle belongs to a different matter.")
    matter = ensure_delivery_matter(store, grounding, now=now)
    if active_matter_id:
        handle = store.get_active_matter(active_matter_id)
        if handle is not None and handle.matter_id not in {"", matter.id}:
            raise GroundingError("That handle belongs to a different matter.")
        chosen = active_matter_id
    else:
        same = [
            item
            for item in store.list_active_matters()
            if item.matter_id == matter.id and item.visibility.value == "active"
        ]
        if len(same) > 1:
            raise GroundingError(
                "More than one continuity handle matches this matter.",
                candidates=tuple(item.title for item in same),
            )
        chosen = same[0].id if same else ""
    proposal = persist_delivery_proposal(
        store, matter, grounding, provenance=provenance, now=now
    )
    return proposal, chosen


def persist_delivery_proposal(
    store: OperationsStore,
    matter: Matter,
    grounding: DeliveryGrounding,
    *,
    provenance: RequestProvenance,
    now: datetime,
) -> ProposedAction:
    """Insert the version, or return the open row that already has this fingerprint."""
    candidate = propose_deliver_document(
        matter,
        document_id=grounding.document.id,
        recipient_id=grounding.recipient.id,
        now=now,
    )
    if candidate is None:
        raise GroundingError("Wally could not construct a delivery proposal.")
    existing = store.get_proposal_by_fingerprint(candidate.fingerprint)
    if existing is not None:
        return existing
    open_rows = [
        item
        for item in store.list_proposals(matter_id=matter.id, intent=candidate.intent)
        if item.status in _OPEN
    ]
    candidate.request_provenance = provenance
    if not open_rows:
        store.save_proposal(candidate)
        return candidate
    store.replace_proposal(
        open_rows[0].id,
        candidate,
        status=ProposalStatus.SUPERSEDED,
        updated_at=now.isoformat(),
        status_reason="proposal content changed",
    )
    stored = store.get_proposal_by_fingerprint(candidate.fingerprint)
    if stored is None:
        raise GroundingError("Wally could not store the delivery proposal.")
    return stored
