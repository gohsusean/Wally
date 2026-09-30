"""v0.13 Phase 2 presentation — inline suggestions, JSON projection, and service wiring."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.mock_communications import MockCommunicationsProvider
from wally.audit.logger import AuditLogger
from wally.models.communications import CalendarEvent, EmailSummary
from wally.models.ops import (
    BriefProposal,
    OperationalBrief,
    ProposalIntent,
    ProposalStatus,
)
from wally.ops.brief import NO_ACTION_FOOTER, format_brief, generate_brief
from wally.ops.service import UNRESOLVED_OBSERVATION, ObserveBriefService
from wally.ops.store import OperationsStore

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
EVENT_START = NOW + timedelta(days=2)

PREPARE_LINE = "  ↳ Suggested: Set aside time to prepare before this event starts."
REVIEW_LINE = "  ↳ Suggested: Review this bill and decide how to handle it."


class FixtureCommunications(MockCommunicationsProvider):
    def __init__(self) -> None:
        super().__init__()
        self.inbox: list[EmailSummary] = []
        self.events: list[CalendarEvent] = []

    def search_email(
        self, *, query: str = "", unread_only: bool = False, limit: int = 10
    ) -> list[EmailSummary]:
        return list(self.inbox)[:limit]

    def get_email(self, message_id: str):
        raise AssertionError("Observe must not fetch full email bodies")

    def list_calendar_events(
        self, *, start: str, end: str, calendar_id: str | None = None
    ) -> list[CalendarEvent]:
        return list(self.events)


def _service(tmp_path: Path, comms: FixtureCommunications) -> ObserveBriefService:
    return ObserveBriefService(
        OperationsStore(tmp_path / "operations.db"),
        audit=AuditLogger(tmp_path / "audit"),
        communications=comms,
        display_timezone="UTC",
    )


def _email(**kwargs) -> EmailSummary:
    defaults = dict(
        message_id="msg-1",
        thread_id="thread-bill",
        subject="Invoice for August",
        sender="billing@example.com",
        date="Fri, 14 Aug 2026 10:00:00 +0800",
        snippet="Amount due for the August service charge.",
        labels=("INBOX",),
    )
    defaults.update(kwargs)
    return EmailSummary(**defaults)


def _event(**kwargs) -> CalendarEvent:
    defaults = dict(
        event_id="evt-1",
        summary="Flight to Buenos Aires",
        start=EVENT_START.isoformat(),
        end=(EVENT_START + timedelta(hours=2)).isoformat(),
        location="",
        description="Window seat booked.",
        attendees=(),
    )
    defaults.update(kwargs)
    return CalendarEvent(**defaults)


def _calendar_service(tmp_path: Path) -> ObserveBriefService:
    comms = FixtureCommunications()
    comms.events = [_event()]
    return _service(tmp_path, comms)


def _bill_service(tmp_path: Path) -> ObserveBriefService:
    comms = FixtureCommunications()
    comms.inbox = [_email()]
    return _service(tmp_path, comms)


def test_calendar_matter_renders_one_inline_preparation_suggestion(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)

    brief = service.brief(now=NOW)
    text = service.render(refresh=False, now=NOW)

    assert len(brief.proposals) == 1
    assert brief.proposals[0].intent is ProposalIntent.PREPARE_FOR_EVENT
    assert text.count(PREPARE_LINE) == 1
    assert "Flight to Buenos Aires" in text


def test_invoice_matter_renders_one_inline_bill_review_suggestion(tmp_path: Path) -> None:
    service = _bill_service(tmp_path)

    brief = service.brief(now=NOW)
    text = service.render(refresh=False, now=NOW)

    assert len(brief.proposals) == 1
    assert brief.proposals[0].intent is ProposalIntent.REVIEW_BILL
    assert text.count(REVIEW_LINE) == 1


def test_footer_appears_exactly_once_when_proposals_exist(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.inbox = [_email()]
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)

    text = service.render(refresh=False, now=NOW)

    assert text.count(NO_ACTION_FOOTER) == 1
    assert text.count("↳ Suggested:") == 2


def test_no_proposals_means_no_footer(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.inbox = [
        _email(
            message_id="msg-generic",
            thread_id="thread-generic",
            subject="Weekly newsletter",
            snippet="Nothing actionable here.",
        )
    ]
    service = _service(tmp_path, comms)

    brief = service.brief(now=NOW)
    text = service.render(refresh=False, now=NOW)

    assert brief.proposals == ()
    assert NO_ACTION_FOOTER not in text
    assert "↳ Suggested:" not in text


def test_empty_brief_keeps_phase_one_text(tmp_path: Path) -> None:
    service = _service(tmp_path, FixtureCommunications())

    text = service.render(now=NOW)

    assert "Nothing needs attention." in text
    assert NO_ACTION_FOOTER not in text


def test_suggestions_attach_to_the_correct_matter(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.inbox = [_email()]
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)

    lines = service.render(refresh=False, now=NOW).splitlines()

    for index, line in enumerate(lines):
        if line == PREPARE_LINE:
            owner = next(lines[i] for i in range(index, -1, -1) if lines[i].startswith("- "))
            assert "Flight to Buenos Aires" in owner
        if line == REVIEW_LINE:
            owner = next(lines[i] for i in range(index, -1, -1) if lines[i].startswith("- "))
            assert "Invoice for August" in owner


def test_multiple_proposals_for_one_matter_render_deterministically(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)
    store = service.store
    original = store.list_proposals()[0]

    second = _clone(original, proposal_id="prop-second", intent=ProposalIntent.REVIEW_BILL)
    store.save_proposal(second)

    brief = generate_brief(store, now=NOW)
    text = format_brief(brief, display_tz=UTC)

    assert [item.intent for item in brief.proposals] == [
        ProposalIntent.PREPARE_FOR_EVENT,
        ProposalIntent.REVIEW_BILL,
    ]
    assert text.count("↳ Suggested:") == 2
    assert text.count(NO_ACTION_FOOTER) == 1
    assert format_brief(generate_brief(store, now=NOW), display_tz=UTC) == text


def _clone(proposal, *, proposal_id: str, intent: ProposalIntent):
    from dataclasses import replace

    return replace(
        proposal,
        id=proposal_id,
        fingerprint=f"{proposal.fingerprint}:{proposal_id}",
        intent=intent,
    )


def test_terminal_proposals_never_render(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)
    store = service.store
    proposal = store.list_proposals()[0]
    for status in (
        ProposalStatus.SUPERSEDED,
        ProposalStatus.INVALIDATED,
        ProposalStatus.EXPIRED,
        ProposalStatus.DISMISSED,
    ):
        proposal.status = status
        store.save_proposal(proposal)

        brief = generate_brief(store, now=NOW)

        assert brief.proposals == ()
        assert "↳ Suggested:" not in format_brief(brief, display_tz=UTC)


def test_proposals_for_omitted_matters_do_not_appear(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)
    store = service.store
    matter = store.list_matters()[0]
    proposal = store.list_proposals()[0]
    assert proposal.matter_id == matter.id

    matter.status = matter.status.DISMISSED
    store.save_matter(matter)

    brief = generate_brief(store, now=NOW)
    text = format_brief(brief, display_tz=UTC)
    payload = json.loads(json.dumps(asdict(brief)))

    assert brief.proposals == ()
    assert "↳ Suggested:" not in text
    assert payload["proposals"] == []


def test_json_adds_proposals_without_changing_existing_keys(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)

    payload = json.loads(service.render(refresh=False, now=NOW, as_json=True))

    phase_one_keys = {
        "generated_at",
        "needs_attention",
        "upcoming",
        "waiting",
        "recently_resolved",
        "fyi",
        "notes",
    }
    assert phase_one_keys <= set(payload)
    assert set(payload) == phase_one_keys | {"proposals"}
    item_keys = {field.name for field in fields(OperationalBrief)}
    assert set(payload) == item_keys
    assert len(payload["proposals"]) == 1


def test_json_proposal_entries_expose_only_the_projection(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.inbox = [_email()]
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)

    raw = service.render(refresh=False, now=NOW, as_json=True)
    payload = json.loads(raw)

    approved = {field.name for field in fields(BriefProposal)}
    assert approved == {
        "proposal_id",
        "matter_id",
        "intent",
        "title",
        "rationale",
        "suggestion",
        "risk",
        "confidence",
        "expires_at",
    }
    for entry in payload["proposals"]:
        assert set(entry) == approved

    stored = service.store.list_proposals()
    for proposal in stored:
        assert proposal.fingerprint not in raw
        assert proposal.content_hash not in raw
        for observation_id in proposal.observation_ids:
            assert observation_id not in raw
    for banned in (
        "fingerprint",
        "content_hash",
        "status_reason",
        "superseded_by",
        "provenance",
        "observation_ids",
        "knowledge_ids",
        "event_id",
        "thread_id",
        "tool",
        "provider",
        "arguments",
        "parameters",
        "payload",
        "approve",
        "authorization",
        "token",
        "secret",
        "credential",
        "amount",
        "account",
        "recipient",
        "address",
    ):
        assert banned not in raw
    assert "evt-1" not in raw
    assert "thread-bill" not in raw
    assert "billing@example.com" not in raw


def test_no_refresh_still_reconciles_stored_proposals(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(refresh=False, now=NOW)
    assert service.store.list_proposals() == []

    comms_service = service
    comms_service.refresh(now=NOW)

    brief = comms_service.brief(refresh=False, now=NOW)
    assert len(brief.proposals) == 1

    expired = comms_service.brief(refresh=False, now=EVENT_START)
    assert expired.proposals == ()
    assert comms_service.store.list_proposals()[0].status is ProposalStatus.EXPIRED


def test_no_refresh_invalidates_when_matter_resolves(tmp_path: Path) -> None:
    service = _bill_service(tmp_path)
    service.brief(now=NOW)
    store = service.store
    matter = store.list_matters()[0]
    matter.status = matter.status.RESOLVED
    store.save_matter(matter)

    brief = service.brief(refresh=False, now=NOW + timedelta(hours=1))

    assert brief.proposals == ()
    assert store.list_proposals()[0].status is ProposalStatus.INVALIDATED


def test_refresh_reconciles_proposals_exactly_once(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    calls: list[datetime] = []
    inner = service._proposals

    class CountingReconciler:
        def reconcile(self, *, now: datetime, provenance=None):
            calls.append(now)
            assert service.store.list_matters(), "matters must be reconciled first"
            return inner.reconcile(now=now, provenance=provenance)

    service._proposals = CountingReconciler()

    brief = service.brief(refresh=True, now=NOW)

    assert len(calls) == 1
    assert calls[0] == NOW
    assert len(brief.proposals) == 1


def test_repeated_briefs_create_no_duplicate_and_keep_timestamps(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)
    before = service.store.list_proposals()

    service.brief(now=NOW + timedelta(hours=1))
    service.brief(refresh=False, now=NOW + timedelta(hours=2))

    after = service.store.list_proposals()
    assert after == before
    assert len(after) == 1


def test_event_proposal_disappears_at_event_start(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)

    text = service.render(refresh=False, now=EVENT_START)

    assert PREPARE_LINE not in text
    assert NO_ACTION_FOOTER not in text
    assert service.store.list_proposals()[0].status is ProposalStatus.EXPIRED


def test_overdue_open_bill_proposal_remains_visible(tmp_path: Path) -> None:
    service = _bill_service(tmp_path)
    service.brief(now=NOW)

    brief = service.brief(refresh=False, now=NOW + timedelta(days=90))

    assert len(brief.proposals) == 1
    assert brief.proposals[0].expires_at == ""
    assert service.store.list_proposals()[0].status is ProposalStatus.PROPOSED


def test_audit_records_only_real_transitions_by_id(tmp_path: Path) -> None:
    service = _calendar_service(tmp_path)
    service.brief(now=NOW)
    proposal = service.store.list_proposals()[0]

    created = _audit_events(tmp_path, "proposal_created")
    assert [entry["parameters"]["subject"] for entry in created] == [proposal.id]
    assert created[0]["parameters"]["detail"] == "proposals"

    service.brief(now=NOW + timedelta(hours=1))
    assert len(_audit_events(tmp_path, "proposal_created")) == 1

    service.brief(refresh=False, now=EVENT_START)
    expired = _audit_events(tmp_path, "proposal_expired")
    assert [entry["parameters"]["subject"] for entry in expired] == [proposal.id]

    # Scoped to proposal events: Phase 1 observation audit is out of scope here.
    proposal_blob = json.dumps(
        [
            entry
            for entry in _all_audit_events(tmp_path)
            if str(entry.get("event_type", "")).startswith("proposal_")
        ]
    )
    assert "Set aside time to prepare" not in proposal_blob
    assert "Flight to Buenos Aires" not in proposal_blob
    assert proposal.fingerprint not in proposal_blob
    assert proposal.content_hash not in proposal_blob
    assert proposal.matter_id not in proposal_blob
    for entry in json.loads(proposal_blob):
        assert set(entry["parameters"]) == {"subject", "detail"}
        assert entry["parameters"]["detail"] == "proposals"


def _audit_blob(tmp_path: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((tmp_path / "audit").rglob("*.jsonl"))
    )


def _all_audit_events(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in _audit_blob(tmp_path).splitlines() if line.strip()]


def _audit_events(tmp_path: Path, event_type: str) -> list[dict]:
    return [entry for entry in _all_audit_events(tmp_path) if entry.get("event_type") == event_type]


DISTINCTIVE = (
    "Flight to Buenos Aires",
    "Window seat booked at gate 42",
    "Invoice for August",
    "Amount due for the August service charge",
    "billing@example.com",
    "https://pay.example.com/invoice/9911",
)


def test_audit_never_records_observation_text_or_fingerprints(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.inbox = [
        _email(
            snippet="Amount due for the August service charge. Pay at "
            "https://pay.example.com/invoice/9911",
        )
    ]
    comms.events = [_event(description="Window seat booked at gate 42")]
    service = _service(tmp_path, comms)

    service.brief(now=NOW)
    # A second pass replays the same sources, exercising the deduplication path.
    service.brief(now=NOW + timedelta(hours=1))

    blob = _audit_blob(tmp_path)
    for value in DISTINCTIVE:
        assert value not in blob, value
    for observation in service.store.list_observations():
        assert observation.fingerprint not in blob
        assert observation.title not in blob
        assert observation.summary not in blob

    ingested = _audit_events(tmp_path, "observation_ingested")
    deduplicated = _audit_events(tmp_path, "observation_deduplicated")
    assert ingested and deduplicated
    known_ids = {observation.id for observation in service.store.list_observations()}
    for entry in ingested + deduplicated:
        assert entry["parameters"]["subject"] in known_ids
        assert entry["parameters"]["detail"] in {"gmail", "calendar", "knowledge", "ops"}


def test_unresolvable_deduplicated_fingerprint_logs_a_fixed_subject(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service._observer.skipped_fingerprints.append("calendar:evt-1:Flight to Buenos Aires")
    original = service._observer.observe_calendar

    def _replay(provider, **kwargs):
        result = original(provider, **kwargs)
        service._observer.skipped_fingerprints.append("calendar:ghost:Flight to Buenos Aires")
        return result

    service._observer.observe_calendar = _replay
    service.refresh(now=NOW)

    blob = _audit_blob(tmp_path)
    assert "Flight to Buenos Aires" not in blob
    events = _audit_events(tmp_path, "observation_deduplicated")
    subjects = [entry["parameters"]["subject"] for entry in events]
    assert UNRESOLVED_OBSERVATION in subjects


def test_presentation_performs_no_external_action(tmp_path: Path) -> None:
    comms = FixtureCommunications()
    comms.inbox = [_email()]
    comms.events = [_event()]
    service = _service(tmp_path, comms)

    service.brief(now=NOW)
    service.render(refresh=False, now=NOW)
    service.render(refresh=False, now=NOW, as_json=True)
    service.brief(refresh=False, now=EVENT_START)

    assert comms.sent == []
    assert comms.created_events == []
