"""v0.13 Phase 2 domain model — intents, lifecycle, and the no-execution boundary."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from wally.models.actions import PlannedAction
from wally.models.ops import (
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
EVENT_START = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)

FORBIDDEN_FIELDS = {
    "tool_name",
    "tool",
    "tool_call",
    "provider",
    "action",
    "action_class",
    "parameters",
    "arguments",
    "payload",
    "command",
    "executable",
    "capability",
    "approved",
    "approval",
    "approval_token",
    "authorized",
    "authorization",
    "token",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "amount",
    "currency",
    "account",
    "account_number",
    "recipient",
    "email_address",
    "to_address",
    "url",
}


def _field_names(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def _event_proposal() -> ProposedAction:
    return ProposedAction(
        id="prop-event-1",
        fingerprint="matter-event-1:prepare_for_event",
        matter_id="matter-event-1",
        intent=ProposalIntent.PREPARE_FOR_EVENT,
        status=ProposalStatus.PROPOSED,
        provenance=ProposalProvenance.DETERMINISTIC_RULES,
        risk=ProposalRisk.LOW,
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        title="Quarterly review with the landlord",
        rationale="Event starts tomorrow and no preparation is recorded.",
        suggestion="Set aside time to prepare before this event starts.",
        expires_at=EVENT_START.isoformat(),
        observation_ids=("obs-cal-1",),
        event_id="evt-1",
    )


def _bill_proposal() -> ProposedAction:
    return ProposedAction(
        id="prop-bill-1",
        fingerprint="matter-bill-1:review_bill",
        matter_id="matter-bill-1",
        intent=ProposalIntent.REVIEW_BILL,
        status=ProposalStatus.PROPOSED,
        provenance=ProposalProvenance.DETERMINISTIC_RULES,
        risk=ProposalRisk.MEDIUM,
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        title="Electricity bill awaiting review",
        rationale="Open finance matter with a due date this week.",
        suggestion="Review this bill and decide how to handle it.",
        observation_ids=("obs-mail-1",),
        thread_id="thread-1",
    )


def test_both_approved_intents_are_representable() -> None:
    assert _event_proposal().intent is ProposalIntent.PREPARE_FOR_EVENT
    assert _bill_proposal().intent is ProposalIntent.REVIEW_BILL


def test_intents_are_the_allowlist() -> None:
    assert {intent.value for intent in ProposalIntent} == {
        "prepare_for_event",
        "review_bill",
        "deliver_document",
    }


def test_all_lifecycle_statuses_are_defined() -> None:
    assert {s.value for s in ProposalStatus} == {
        "proposed",
        "approved",
        "rejected",
        "deferred",
        "superseded",
        "invalidated",
        "expired",
        "dismissed",
    }


def test_approval_state_is_not_an_execution_token() -> None:
    """A decision status exists. It is not an execution grant or a token field."""
    assert ProposalStatus.APPROVED.value == "approved"
    names = _field_names(ProposedAction)
    assert "decision" in names
    assert "decision_fingerprint" in names
    for forbidden in ("approval_token", "authorized", "tool_name", "payload", "secret"):
        assert forbidden not in names


def test_provenance_covers_deterministic_rules_only() -> None:
    assert {p.value for p in ProposalProvenance} == {"deterministic_rules"}


def test_bill_proposal_has_no_expiry() -> None:
    assert _bill_proposal().expires_at == ""


def test_event_proposal_carries_its_event_start_expiry() -> None:
    assert _event_proposal().expires_at == EVENT_START.isoformat()


def test_model_exposes_no_executable_or_authorization_fields() -> None:
    assert _field_names(ProposedAction) & FORBIDDEN_FIELDS == set()


def test_model_stores_reference_identifiers_and_display_prose() -> None:
    fields = _field_names(ProposedAction)
    assert {"matter_id", "observation_ids", "knowledge_ids", "event_id", "thread_id"} <= fields
    assert {"title", "rationale", "suggestion"} <= fields


def test_proposal_is_structurally_separate_from_planned_action() -> None:
    assert not issubclass(ProposedAction, PlannedAction)
    assert _field_names(ProposedAction) & _field_names(PlannedAction) == set()


def test_a_proposal_cannot_be_used_where_a_planned_action_is_expected() -> None:
    proposal = _bill_proposal()
    assert not isinstance(proposal, PlannedAction)
    for required in _field_names(PlannedAction):
        assert not hasattr(proposal, required)


def test_ops_module_does_not_import_execution_types() -> None:
    import wally.models.ops as ops_module

    exported = vars(ops_module)
    for name in ("PlannedAction", "ActionClass", "ToolCall", "ToolRegistry", "ApprovalGate"):
        assert name not in exported


def test_lifecycle_transition_records_supersession_without_deleting_history() -> None:
    original = _event_proposal()
    original.status = ProposalStatus.SUPERSEDED
    original.superseded_by = "prop-event-2"
    original.status_reason = "event time changed"

    assert original.status is ProposalStatus.SUPERSEDED
    assert original.superseded_by == "prop-event-2"
