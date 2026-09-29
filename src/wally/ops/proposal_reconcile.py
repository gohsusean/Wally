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
    Matter,
    MatterStatus,
    Observation,
    ProposalIntent,
    ProposalStatus,
    ProposedAction,
)
from wally.ops.priority import parse_time
from wally.ops.propose import propose_for_matter
from wally.ops.store import OperationsStore

# Fixed internal reasons. No Matter or Observation text is ever recorded here.
REASON_EVENT_REACHED = "event start reached"
REASON_MATTER_CLOSED = "matter no longer open"
REASON_EVIDENCE_INELIGIBLE = "supporting evidence no longer eligible"
REASON_CONTENT_CHANGED = "proposal content changed"
REASON_INTENT_CHANGED = "proposal intent changed"


@dataclass
class ProposalReconciliation:
    """What one pass changed. Ids only, so nothing untrusted enters the summary."""

    created: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    invalidated: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.superseded or self.invalidated or self.expired)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).isoformat()


class ProposalReconciler:
    def __init__(self, store: OperationsStore) -> None:
        self._store = store

    def reconcile(self, *, now: datetime) -> ProposalReconciliation:
        current = _aware(now)
        result = ProposalReconciliation()
        observations = {item.id: item for item in self._store.list_observations()}
        matters = {item.id: item for item in self._store.list_matters()}

        for proposal in self._store.list_proposals(status=ProposalStatus.PROPOSED):
            self._reconcile_active(proposal, matters, observations, current, result)

        active_matters = {
            proposal.matter_id
            for proposal in self._store.list_proposals(status=ProposalStatus.PROPOSED)
        }
        for matter in matters.values():
            if matter.id in active_matters or matter.status != MatterStatus.OPEN:
                continue
            self._open_new(matter, observations, current, result)
        return result

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

        candidate = propose_for_matter(matter, self._support_for(matter, observations), now=now)
        if candidate is None:
            self._close(
                proposal, ProposalStatus.INVALIDATED, REASON_EVIDENCE_INELIGIBLE, now, result
            )
            return

        if candidate.fingerprint == proposal.fingerprint:
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

        self._store.replace_proposal(
            proposal.id,
            candidate,
            status=terminal_status,
            updated_at=_iso(now),
            status_reason=reason,
        )
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
        if self._store.get_proposal_by_fingerprint(candidate.fingerprint) is not None:
            return
        self._store.save_proposal(candidate)
        result.created.append(candidate.id)

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
            # The row left PROPOSED underneath this pass. Fail loudly rather than
            # report a transition the database never applied.
            raise ProposalTransitionError(
                f"proposal {proposal.id} was not {ProposalStatus.PROPOSED.value} "
                "when the transition was applied"
            )
        if status == ProposalStatus.EXPIRED:
            result.expired.append(proposal.id)
        elif status == ProposalStatus.SUPERSEDED:
            result.superseded.append(proposal.id)
        else:
            result.invalidated.append(proposal.id)
