"""Explicit user decisions on ProposedActions.

Approval, rejection, and deferral are authorization state. They do not execute,
resolve secrets, or call a provider. The only accepted origins are trusted Wally
commands. Source text and model output cannot decide a proposal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from wally.exceptions import ProposalDecisionError
from wally.models.ops import (
    TRUSTED_DECISION_ORIGINS,
    ProposalStatus,
    ProposedAction,
)
from wally.ops.priority import parse_time
from wally.ops.store import OperationsStore

NOTE_MAX = 280

_STATUS_REASON = {
    ProposalStatus.APPROVED: "user approved",
    ProposalStatus.REJECTED: "user rejected",
    ProposalStatus.DEFERRED: "user deferred",
}


class UserDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"


_STATUS_FOR_DECISION = {
    UserDecision.APPROVE: ProposalStatus.APPROVED,
    UserDecision.REJECT: ProposalStatus.REJECTED,
    UserDecision.DEFER: ProposalStatus.DEFERRED,
}


def clean_note(note: str) -> str:
    return " ".join(note.split())[:NOTE_MAX]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).isoformat()


def parse_defer_until(value: str, *, now: datetime) -> str:
    parsed = parse_time(value)
    if parsed is None:
        raise ProposalDecisionError("Defer needs a date, for example 2026-10-03.")
    if parsed <= _aware(now):
        raise ProposalDecisionError("Defer date must be in the future.")
    return _iso(parsed)


def apply_user_decision(
    store: OperationsStore,
    proposal_id: str,
    *,
    decision: UserDecision,
    origin: str,
    now: datetime,
    note: str = "",
    defer_until: str = "",
) -> ProposedAction:
    """Record one explicit user decision against the proposal version in the store.

    ``origin`` must be a trusted command. The stored fingerprint is copied onto the
    decision inside the store, so this function cannot approve a different version
    from the one that is pending.
    """
    if origin not in TRUSTED_DECISION_ORIGINS:
        raise ProposalDecisionError("Only an explicit user command can decide a proposal.")
    proposal = store.get_proposal(proposal_id)
    if proposal is None:
        raise ProposalDecisionError(f"No proposal with id {proposal_id}.")
    if proposal.status != ProposalStatus.PROPOSED:
        raise ProposalDecisionError(
            f"Proposal {proposal_id} is {proposal.status.value} and is not awaiting a decision."
        )
    status = _STATUS_FOR_DECISION[decision]
    until = ""
    if decision == UserDecision.DEFER:
        until = parse_defer_until(defer_until, now=now)
    elif defer_until:
        raise ProposalDecisionError("Only defer accepts a date.")
    recorded = store.record_decision(
        proposal_id,
        status=status,
        updated_at=_iso(now),
        decision_origin=origin,
        decision_note=clean_note(note),
        defer_until=until,
        status_reason=_STATUS_REASON[status],
    )
    if not recorded:
        raise ProposalDecisionError(
            f"Proposal {proposal_id} changed before the decision was recorded."
        )
    stored = store.get_proposal(proposal_id)
    if stored is None:
        raise ProposalDecisionError(f"No proposal with id {proposal_id}.")
    return stored
