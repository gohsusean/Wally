"""Execution eligibility for proposed actions.

Pure checks over a stored proposal. This module reads no provider, resolves no
secret, and calls no executor. ``wally.ops.act`` runs these checks, then
revalidates the Matter, before it builds anything executable.

Passing here is necessary, not sufficient: the Matter must still be open, the
current evidence must still produce the same fingerprint, and the user must
authorize the typed plan at execution time.
"""

from __future__ import annotations

from wally.models.ops import (
    ProposalIntent,
    ProposalStatus,
    ProposedAction,
)

# The code, not the proposal, decides what is executable. PREPARE_FOR_EVENT has no
# executor: preparing for a meeting is the user's work, and v0.15 writes no calendar.
SUPPORTED_EXECUTION_INTENTS = frozenset({ProposalIntent.REVIEW_BILL})

UNSUPPORTED_EXECUTION_MESSAGE = "Approved, but execution is not supported yet."

NOT_APPROVED = "not_approved"
STALE_APPROVAL = "stale_approval"
UNTRUSTED_DECISION = "untrusted_decision"
UNSUPPORTED_ACTION = "unsupported_action"
NO_TRUSTED_TARGET = "no_trusted_target"


def approval_problem(proposal: ProposedAction) -> str | None:
    """Return why this proposal's approval does not authorize execution, or None."""
    if proposal.status in {
        ProposalStatus.SUPERSEDED,
        ProposalStatus.INVALIDATED,
        ProposalStatus.EXPIRED,
    } and proposal.decision == ProposalStatus.APPROVED.value:
        return STALE_APPROVAL
    if proposal.status != ProposalStatus.APPROVED:
        return NOT_APPROVED
    if proposal.decision != ProposalStatus.APPROVED.value:
        return NOT_APPROVED
    # ``record_decision`` writes a principal only after the principal authority
    # authorized the request, and ``save_proposal`` cannot write one at all.
    if not proposal.decision_principal or not proposal.decision_origin:
        return UNTRUSTED_DECISION
    if not proposal.decision_fingerprint or proposal.decision_fingerprint != proposal.fingerprint:
        return STALE_APPROVAL
    return None


def support_problem(proposal: ProposedAction) -> str | None:
    """Return why no executor exists for this proposal, or None."""
    if proposal.intent not in SUPPORTED_EXECUTION_INTENTS:
        return UNSUPPORTED_ACTION
    if proposal.intent == ProposalIntent.REVIEW_BILL and len(proposal.knowledge_ids) != 1:
        # An email-only bill has no trusted portal. Its sender cannot supply one.
        return NO_TRUSTED_TARGET
    return None


def execution_supported(proposal: ProposedAction) -> bool:
    return support_problem(proposal) is None


def execution_allowed(proposal: ProposedAction) -> bool:
    """Whether the stored approval is current and an executor exists for it.

    Does not look at the Matter or current evidence; the executor rechecks both.
    """
    return approval_problem(proposal) is None and support_problem(proposal) is None
