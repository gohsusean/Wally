"""Complete Calendar API pages and safe revision/disappearance semantics."""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_ops_approvals import NOW, FixtureCommunications, _service, cli
from wally.adapters.google.adapter import GoogleCommunicationsAdapter
from wally.exceptions import ProviderUnavailableError
from wally.models.communications import CalendarEvent, CalendarEvents
from wally.models.ops import MatterStatus, ObservationCategory, ProposalStatus
from wally.ops.decisions import UserDecision


def _event():
    return CalendarEvent(
        event_id="evt-1",
        summary="Meeting",
        start=(NOW + timedelta(days=1)).isoformat(),
        end=(NOW + timedelta(days=1, hours=1)).isoformat(),
    )


def _google_item(event):
    return {
        "id": event.event_id,
        "summary": event.summary,
        "start": {"dateTime": event.start},
        "end": {"dateTime": event.end},
    }


def test_google_reads_every_page_and_only_then_attests_complete(monkeypatch):
    adapter = GoogleCommunicationsAdapter(
        client_id="test", client_secret="test", refresh_token="test"
    )
    calls = []

    def request(method, url, *, params):
        calls.append(params)
        if "pageToken" not in params:
            return {"items": [_google_item(_event())], "nextPageToken": "page-2"}
        return {"items": [_google_item(replace(_event(), event_id="evt-2"))]}

    monkeypatch.setattr(adapter, "_request", request)
    try:
        result = adapter.list_calendar_events(start=NOW.isoformat(), end=_event().end)
        assert result.complete
        assert [e.event_id for e in result] == ["evt-1", "evt-2"]
        assert calls[1]["pageToken"] == "page-2"
        assert calls[0]["timeMin"] == calls[1]["timeMin"]
    finally:
        adapter.close()


@pytest.mark.parametrize("failure", ["error", "repeated-token", "malformed"])
def test_failed_calendar_page_preserves_checkpoint_matter_and_approval(
    tmp_path, monkeypatch, failure
):
    comms = FixtureCommunications()
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)
    proposal = service.store.list_proposals()[0]
    service.decide(proposal.id, decision=UserDecision.APPROVE, context=cli(service), now=NOW)
    before = service.store.get_checkpoint("calendar")
    adapter = GoogleCommunicationsAdapter(
        client_id="test", client_secret="test", refresh_token="test"
    )

    def request(method, url, *, params):
        if "pageToken" not in params:
            return {"items": [], "nextPageToken": "next"}
        if failure == "error":
            raise ProviderUnavailableError("communications", "Page failed")
        if failure == "repeated-token":
            return {"items": [], "nextPageToken": "next"}
        return {"items": [{"id": "incomplete-event"}]}

    monkeypatch.setattr(adapter, "_request", request)
    try:
        with pytest.raises(ProviderUnavailableError):
            service._observer.observe_calendar(adapter, now=NOW + timedelta(hours=1))
        assert service.store.get_checkpoint("calendar") == before
        assert service.store.list_matters()[0].status == MatterStatus.OPEN
        assert service.store.get_proposal(proposal.id).status == ProposalStatus.APPROVED
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("end", (NOW + timedelta(days=1, hours=2)).isoformat()),
        ("description", "Bring the revised agreement"),
        ("location", "Office"),
        ("attendees", ("owner@example.com",)),
    ],
)
def test_material_event_revision_supersedes_approval_without_duplicate_matter(
    tmp_path, field, value
):
    comms = FixtureCommunications()
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)
    proposal = service.store.list_proposals()[0]
    service.decide(proposal.id, decision=UserDecision.APPROVE, context=cli(service), now=NOW)
    comms.events = [replace(_event(), **{field: value})]
    service.brief(now=NOW + timedelta(minutes=1))
    assert len(service.store.list_matters()) == 1
    assert service.store.get_proposal(proposal.id).status == ProposalStatus.SUPERSEDED
    successor = service.store.list_open_proposals()[0]
    assert successor.status == ProposalStatus.PROPOSED
    assert successor.decision == ""
    service.brief(now=NOW + timedelta(minutes=2))
    assert len(service.store.list_observations()) == 2


@pytest.mark.parametrize("complete", [False, True])
def test_only_complete_in_scope_absence_can_close_matter(tmp_path, monkeypatch, complete):
    comms = FixtureCommunications()
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)
    monkeypatch.setattr(
        comms, "list_calendar_events", lambda **kw: CalendarEvents(complete=complete)
    )
    service.brief(now=NOW + timedelta(minutes=1))
    expected = MatterStatus.RESOLVED if complete else MatterStatus.OPEN
    assert service.store.list_matters()[0].status == expected


@pytest.mark.parametrize("later,horizon", [(timedelta(days=3), 14), (timedelta(), 0)])
def test_horizon_movement_is_not_cancellation(tmp_path, later, horizon):
    comms = FixtureCommunications()
    comms.events = [_event()]
    service = _service(tmp_path, comms)
    service.brief(now=NOW)
    checkpoint = service.store.get_checkpoint("calendar")
    comms.events = []
    assert service._observer.observe_calendar(comms, now=NOW + later, horizon_days=horizon) == []
    assert service.store.get_checkpoint("calendar") == checkpoint
    assert service.store.list_matters()[0].status == MatterStatus.OPEN
    assert not any(
        o.category == ObservationCategory.CALENDAR_CANCELLED
        for o in service.store.list_observations()
    )
