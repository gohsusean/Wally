"""Durable proposal lifecycle reconciliation.

Compares the pure generator's candidates against stored proposals and applies the
resulting lifecycle transitions. Matters and Observations are read-only here: this
service only ever writes rows in the proposals table.

Nothing in this module executes anything. It calls no provider, resolves no secret,
and reaches no network; the store and the deterministic generator are its only
collaborators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from wally.exceptions import ProposalTransitionError
from wally.models.ops import (
    REOPENABLE_PROPOSAL_STATUSES,
    Matter,
    MatterStatus,
    Observation,
    ProposalIntent,
    ProposalStatus,
    ProposedAction,
)
from wally.models.principal import RequestProvenance
from wally.ops.priority import parse_time
from wally.ops.propose import propose_deliver_document, propose_for_matter
from wally.ops.store import OperationsStore

# Fixed internal reasons. No Matter or Observation text is ever recorded here.
REASON_EVENT_REACHED = "event start reached"
REASON_MATTER_CLOSED = "matter no longer open"
REASON_EVIDENCE_INELIGIBLE = "supporting evidence no longer eligible"
REASON_CONTENT_CHANGED = "proposal content changed"
REASON_INTENT_CHANGED = "proposal intent changed"
REASON_DEFER_ELAPSED = "defer window elapsed"
REASON_REOPENED = "matter reopened; prior authorization cleared"

_BLOCKED_RECREATE = frozenset({ProposalStatus.REJECTED, ProposalStatus.DISMISSED})


@dataclass
class ProposalReconciliation:
    """What one pass changed. Ids only, so nothing untrusted enters the summary."""

    created: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    invalidated: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    defer_elapsed: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    decisions_voided: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.created
            or self.superseded
            or self.invalidated
            or self.expired
            or self.defer_elapsed
            or self.reopened
            or self.decisions_voided
        )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).isoformat()


class ProposalReconciler:
    def __init__(self, store: OperationsStore) -> None:
        self._store = store
        self._provenance = RequestProvenance()

    def reconcile(
        self,
        *,
        now: datetime,
        provenance: RequestProvenance | None = None,
    ) -> ProposalReconciliation:
        """Bring stored proposals in line with current evidence.

        ``provenance`` names the request that triggered this pass. New proposals
        record it; it is not part of any fingerprint and authorizes nothing.
        """
        current = _aware(now)
        self._provenance = provenance or RequestProvenance()
        result = ProposalReconciliation()
        observations = {item.id: item for item in self._store.list_observations()}
        matters = {item.id: item for item in self._store.list_matters()}

        for proposal in self._store.list_open_proposals():
            self._reconcile_active(proposal, matters, observations, current, result)

        active_matters = {proposal.matter_id for proposal in self._store.list_open_proposals()}
        for matter in matters.values():
            if matter.id in active_matters or matter.status != MatterStatus.OPEN:
                continue
            self._open_new(matter, observations, current, result)
        return result

    def _reconcile_delivery(
        self,
        proposal: ProposedAction,
        matter: Matter,
        now: datetime,
        result: ProposalReconciliation,
    ) -> None:
        """Keep a delivery proposal only when its canonical ids still hash the same."""
        if len(matter.knowledge_ids) != 2:
            self._close(
                proposal,
                ProposalStatus.INVALIDATED,
                REASON_EVIDENCE_INELIGIBLE,
                now,
                result,
            )
            return
        document_id, recipient_id = matter.knowledge_ids
        candidate = propose_deliver_document(
            matter, document_id=document_id, recipient_id=recipient_id, now=now
        )
        if candidate is not None and candidate.fingerprint == proposal.fingerprint:
            result.unchanged.append(proposal.id)
            return
        self._close(
            proposal, ProposalStatus.INVALIDATED, REASON_EVIDENCE_INELIGIBLE, now, result
        )

    def _support_for(
        self, matter: Matter, observations: dict[str, Observation]
    ) -> list[Observation]:
        """Every referenced Observation that exists.

        Missing ids are left missing rather than filled in, so the generator sees an
        incomplete set and fails closed.
        """
        return [
            observations[observation_id]
            for observation_id in matter.observation_ids
            if observation_id in observations
        ]

    def _reconcile_active(
        self,
        proposal: ProposedAction,
        matters: dict[str, Matter],
        observations: dict[str, Observation],
        now: datetime,
        result: ProposalReconciliation,
    ) -> None:
        if self._has_expired(proposal, now):
            self._close(proposal, ProposalStatus.EXPIRED, REASON_EVENT_REACHED, now, result)
            return

        matter = matters.get(proposal.matter_id)
        if matter is None or matter.status != MatterStatus.OPEN:
            self._close(proposal, ProposalStatus.INVALIDATED, REASON_MATTER_CLOSED, now, result)
            return

        if proposal.intent == ProposalIntent.DELIVER_DOCUMENT:
            self._reconcile_delivery(proposal, matter, now, result)
            return

        candidate = propose_for_matter(matter, self._support_for(matter, observations), now=now)
        if candidate is None:
            self._close(
                proposal, ProposalStatus.INVALIDATED, REASON_EVIDENCE_INELIGIBLE, now, result
            )
            return

        if candidate.fingerprint == proposal.fingerprint:
            if self._defer_has_elapsed(proposal, now):
                released = self._store.release_defer(
                    proposal.id,
                    updated_at=_iso(now),
                    status_reason=REASON_DEFER_ELAPSED,
                )
                if not released:
                    raise ProposalTransitionError(
                        f"proposal {proposal.id} was not deferred when the defer window elapsed"
                    )
                result.defer_elapsed.append(proposal.id)
                return
            result.unchanged.append(proposal.id)
            return

        terminal_status = (
            ProposalStatus.SUPERSEDED
            if candidate.intent == proposal.intent
            else ProposalStatus.INVALIDATED
        )
        reason = (
            REASON_CONTENT_CHANGED
            if terminal_status == ProposalStatus.SUPERSEDED
            else REASON_INTENT_CHANGED
        )
        if self._store.get_proposal_by_fingerprint(candidate.fingerprint) is not None:
            # This exact version is already stored as history. Close the incumbent
            # rather than resurrect a terminal row or duplicate its fingerprint.
            self._close(proposal, terminal_status, reason, now, result)
            return

        # A successor continues its predecessor's request lineage.
        candidate.request_provenance = (
            proposal.request_provenance
            if not proposal.request_provenance.is_empty()
            else self._provenance
        )
        self._store.replace_proposal(
            proposal.id,
            candidate,
            status=terminal_status,
            updated_at=_iso(now),
            status_reason=reason,
        )
        self._note_voided_decision(proposal, result)
        if terminal_status == ProposalStatus.SUPERSEDED:
            result.superseded.append(proposal.id)
        else:
            result.invalidated.append(proposal.id)
        result.created.append(candidate.id)

    def _open_new(
        self,
        matter: Matter,
        observations: dict[str, Observation],
        now: datetime,
        result: ProposalReconciliation,
    ) -> None:
        candidate = propose_for_matter(matter, self._support_for(matter, observations), now=now)
        if candidate is None:
            return
        existing = self._store.get_proposal_by_fingerprint(candidate.fingerprint)
        if existing is None:
            candidate.request_provenance = self._provenance
            self._store.save_proposal(candidate)
            result.created.append(candidate.id)
            return
        if existing.status in _BLOCKED_RECREATE:
            return
        if existing.status in REOPENABLE_PROPOSAL_STATUSES:
            reopened = self._store.reopen_proposal(
                existing.id,
                updated_at=_iso(now),
                status_reason=REASON_REOPENED,
            )
            if reopened:
                result.reopened.append(existing.id)
            return

    def _defer_has_elapsed(self, proposal: ProposedAction, now: datetime) -> bool:
        if proposal.status != ProposalStatus.DEFERRED:
            return False
        if not proposal.defer_until:
            return True
        deferred_until = parse_time(proposal.defer_until)
        return deferred_until is None or deferred_until <= now

    def _note_voided_decision(
        self, proposal: ProposedAction, result: ProposalReconciliation
    ) -> None:
        if proposal.decision and proposal.id not in result.decisions_voided:
            result.decisions_voided.append(proposal.id)

    def _has_expired(self, proposal: ProposedAction, now: datetime) -> bool:
        """Timed expiry is calendar-only, so a bill proposal never expires."""
        if proposal.intent != ProposalIntent.PREPARE_FOR_EVENT or not proposal.expires_at:
            return False
        expiry = parse_time(proposal.expires_at)
        return expiry is not None and expiry <= now

    def _close(
        self,
        proposal: ProposedAction,
        status: ProposalStatus,
        reason: str,
        now: datetime,
        result: ProposalReconciliation,
    ) -> None:
        closed = self._store.close_proposal(
            proposal.id,
            status=status,
            updated_at=_iso(now),
            status_reason=reason,
        )
        if not closed:
            # The row left the open set underneath this pass. Fail loudly rather than
            # report a transition the database never applied.
            raise ProposalTransitionError(
                f"proposal {proposal.id} was not open (proposed, approved, or deferred) "
                "when the transition was applied"
            )
        self._note_voided_decision(proposal, result)
        if status == ProposalStatus.EXPIRED:
            result.expired.append(proposal.id)
        elif status == ProposalStatus.SUPERSEDED:
            result.superseded.append(proposal.id)
        else:
            result.invalidated.append(proposal.id)
