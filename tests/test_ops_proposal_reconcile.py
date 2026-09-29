"""v0.13 Phase 2 lifecycle — transition precedence, atomicity, and history preservation."""

from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wally.exceptions import ProposalTransitionError
from wally.models.ops import (
    Matter,
    MatterDomain,
    MatterStatus,
    Observation,
    ObservationCategory,
    ProposalIntent,
    ProposalStatus,
)
from wally.ops import proposal_reconcile as reconcile_module
from wally.ops.proposal_reconcile import (
    REASON_CONTENT_CHANGED,
    REASON_EVENT_REACHED,
    REASON_EVIDENCE_INELIGIBLE,
    REASON_MATTER_CLOSED,
    ProposalReconciler,
    ProposalReconciliation,
)
from wally.ops.store import OperationsStore

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
EVENT_START = NOW + timedelta(days=1)


def _store(tmp_path: Path) -> OperationsStore:
    return OperationsStore(tmp_path / "operations.db")


def _calendar_observation(
    *,
    observation_id: str = "obs-cal-1",
    source_id: str = "evt-1",
    start: str = "",
    injection: bool = False,
) -> Observation:
    extra = {"start": start or EVENT_START.isoformat()}
    if injection:
        extra["injection_suspected"] = "true"
    return Observation(
        id=observation_id,
        fingerprint=f"calendar:{source_id}:{extra['start']}",
        source="calendar",
        source_id=source_id,
        observed_at="2026-08-16T08:00:00+00:00",
        source_timestamp=extra["start"],
        category=ObservationCategory.CALENDAR_UPCOMING,
        title="Quarterly review",
        summary="Standing agenda item.",
        trusted=False,
        authority="external_communications",
        confidence=1.0,
        extra=extra,
    )


def _invoice_observation(observation_id: str = "obs-invoice-1") -> Observation:
    return Observation(
        id=observation_id,
        fingerprint=f"gmail:{observation_id}",
        source="gmail",
        source_id="msg-1",
        observed_at="2026-08-16T08:00:00+00:00",
        source_timestamp="2026-08-16T08:00:00+00:00",
        category=ObservationCategory.INVOICE,
        title="Electricity bill",
        summary="Bill summary.",
        trusted=False,
        authority="external_communications",
        confidence=0.9,
        thread_id="thread-1",
    )


def _calendar_matter(
    *,
    matter_id: str = "matter-event",
    due_at: str = "",
    observation_ids: tuple[str, ...] = ("obs-cal-1",),
    status: MatterStatus = MatterStatus.OPEN,
) -> Matter:
    return Matter(
        id=matter_id,
        fingerprint=f"matter:event:{matter_id}",
        title="Quarterly review with the landlord",
        domain=MatterDomain.CALENDAR,
        status=status,
        created_at="2026-08-15T09:00:00+00:00",
        updated_at="2026-08-16T08:00:00+00:00",
        summary="Reconciled summary.",
        open_reason="Upcoming calendar commitment",
        last_change="Upcoming calendar event",
        due_at=due_at or EVENT_START.isoformat(),
        observation_ids=observation_ids,
        source="calendar",
    )


def _bill_matter(
    *,
    matter_id: str = "matter-bill",
    due_at: str = "2026-08-20T00:00:00+00:00",
    observation_ids: tuple[str, ...] = ("obs-invoice-1",),
    status: MatterStatus = MatterStatus.OPEN,
) -> Matter:
    return Matter(
        id=matter_id,
        fingerprint=f"matter:thread:{matter_id}",
        title="Electricity bill for August",
        domain=MatterDomain.FINANCE,
        status=status,
        created_at="2026-08-15T09:00:00+00:00",
        updated_at="2026-08-16T08:00:00+00:00",
        summary="Reconciled summary.",
        open_reason="Invoice or bill requires attention",
        last_change="Opened from invoice observation",
        due_at=due_at,
        observation_ids=observation_ids,
        thread_id="thread-1",
        source="gmail",
    )


def _seed_event(store: OperationsStore) -> None:
    store.save_observation(_calendar_observation())
    store.save_matter(_calendar_matter())


def _seed_bill(store: OperationsStore) -> None:
    store.save_observation(_invoice_observation())
    store.save_matter(_bill_matter())


def _dump(path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        conn.close()


def test_eligible_matter_with_no_history_creates_one_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)

    result = ProposalReconciler(store).reconcile(now=NOW)

    stored = store.list_proposals()
    assert len(stored) == 1
    assert stored[0].status is ProposalStatus.PROPOSED
    assert stored[0].intent is ProposalIntent.PREPARE_FOR_EVENT
    assert stored[0].matter_id == "matter-event"
    assert result.created == [stored[0].id]


def test_repeated_reconciliation_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    store = OperationsStore(path)
    _seed_event(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)

    before = _dump(path, "proposals")
    result = reconciler.reconcile(now=NOW + timedelta(hours=2))

    assert _dump(path, "proposals") == before
    assert result.created == []
    assert len(result.unchanged) == 1
    assert not result.changed


def test_material_event_change_supersedes_and_inserts_one_successor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    moved = EVENT_START + timedelta(hours=3)
    store.save_matter(_calendar_matter(due_at=moved.isoformat()))
    result = reconciler.reconcile(now=NOW + timedelta(hours=1))

    active = store.list_proposals(status=ProposalStatus.PROPOSED)
    assert len(active) == 1
    assert active[0].id != original.id
    assert active[0].expires_at == moved.isoformat()
    assert result.superseded == [original.id]
    assert result.created == [active[0].id]

    predecessor = store.get_proposal(original.id)
    assert predecessor is not None
    assert predecessor.status is ProposalStatus.SUPERSEDED
    assert predecessor.superseded_by == active[0].id
    assert predecessor.status_reason == REASON_CONTENT_CHANGED
    assert predecessor.created_at == original.created_at
    assert len(store.list_proposals()) == 2


def test_material_bill_change_supersedes_and_inserts_one_successor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    store.save_matter(_bill_matter(due_at="2026-09-01T00:00:00+00:00"))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    active = store.list_proposals(status=ProposalStatus.PROPOSED)
    assert len(active) == 1
    assert active[0].id != original.id
    assert active[0].expires_at == ""

    predecessor = store.get_proposal(original.id)
    assert predecessor is not None
    assert predecessor.status is ProposalStatus.SUPERSEDED
    assert predecessor.superseded_by == active[0].id


def test_resolved_matter_invalidates_its_active_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    store.save_matter(_bill_matter(status=MatterStatus.RESOLVED))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    closed = store.get_proposal(original.id)
    assert closed is not None
    assert closed.status is ProposalStatus.INVALIDATED
    assert closed.status_reason == REASON_MATTER_CLOSED
    assert store.list_proposals(status=ProposalStatus.PROPOSED) == []


def test_dismissed_matter_invalidates_its_active_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)

    store.save_matter(_bill_matter(status=MatterStatus.DISMISSED))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    assert store.list_proposals(status=ProposalStatus.PROPOSED) == []
    assert store.list_proposals(status=ProposalStatus.INVALIDATED) != []


def test_missing_supporting_evidence_invalidates_active_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    store.save_matter(_bill_matter(observation_ids=("obs-invoice-1", "obs-missing")))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    closed = store.get_proposal(original.id)
    assert closed is not None
    assert closed.status is ProposalStatus.INVALIDATED
    assert closed.status_reason == REASON_EVIDENCE_INELIGIBLE


def test_newly_flagged_evidence_invalidates_active_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    flagged = _calendar_observation(injection=True)
    store.save_observation(replace(flagged, id="obs-cal-2", fingerprint="calendar:evt-1:flagged"))
    store.save_matter(_calendar_matter(observation_ids=("obs-cal-1", "obs-cal-2")))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    closed = store.get_proposal(original.id)
    assert closed is not None
    assert closed.status is ProposalStatus.INVALIDATED
    assert closed.status_reason == REASON_EVIDENCE_INELIGIBLE
    assert store.list_proposals(status=ProposalStatus.PROPOSED) == []


def test_event_proposal_expires_exactly_at_event_start(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    reconciler.reconcile(now=EVENT_START - timedelta(seconds=1))
    assert store.get_proposal(original.id).status is ProposalStatus.PROPOSED

    result = reconciler.reconcile(now=EVENT_START)

    expired = store.get_proposal(original.id)
    assert expired is not None
    assert expired.status is ProposalStatus.EXPIRED
    assert expired.status_reason == REASON_EVENT_REACHED
    assert result.expired == [original.id]


def test_expiry_takes_precedence_over_matter_resolution(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    store.save_matter(_calendar_matter(status=MatterStatus.RESOLVED))
    reconciler.reconcile(now=EVENT_START)

    closed = store.get_proposal(original.id)
    assert closed is not None
    assert closed.status is ProposalStatus.EXPIRED
    assert closed.status_reason == REASON_EVENT_REACHED


def test_bill_proposal_survives_long_past_its_due_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    reconciler.reconcile(now=NOW + timedelta(days=120))

    still_open = store.get_proposal(original.id)
    assert still_open is not None
    assert still_open.status is ProposalStatus.PROPOSED
    assert still_open.expires_at == ""


def test_proposal_with_missing_matter_is_invalidated(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    store = OperationsStore(path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM matters WHERE id = ?", ("matter-bill",))
    conn.commit()
    conn.close()
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    closed = store.get_proposal(original.id)
    assert closed is not None
    assert closed.status is ProposalStatus.INVALIDATED
    assert closed.status_reason == REASON_MATTER_CLOSED


def test_terminal_proposals_never_reactivate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]

    moved = EVENT_START + timedelta(hours=3)
    store.save_matter(_calendar_matter(due_at=moved.isoformat()))
    reconciler.reconcile(now=NOW + timedelta(hours=1))
    successor = store.list_proposals(status=ProposalStatus.PROPOSED)[0]

    # The event reverts to its original time, regenerating the superseded fingerprint.
    store.save_matter(_calendar_matter())
    reconciler.reconcile(now=NOW + timedelta(hours=2))

    revived = store.get_proposal(original.id)
    assert revived is not None
    assert revived.status is ProposalStatus.SUPERSEDED
    assert store.get_proposal(successor.id).status is ProposalStatus.SUPERSEDED
    assert store.list_proposals(status=ProposalStatus.PROPOSED) == []
    assert len(store.list_proposals()) == 2


def test_dismissed_proposal_is_never_altered_or_regenerated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]
    original.status = ProposalStatus.DISMISSED
    original.status_reason = "user dismissed"
    store.save_proposal(original)
    snapshot = store.get_proposal(original.id)

    reconciler.reconcile(now=NOW + timedelta(hours=1))
    reconciler.reconcile(now=NOW + timedelta(days=30))

    assert store.get_proposal(original.id) == snapshot
    assert store.list_proposals(status=ProposalStatus.PROPOSED) == []
    assert len(store.list_proposals()) == 1


def test_dismissal_does_not_block_a_materially_changed_successor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    original = store.list_proposals()[0]
    original.status = ProposalStatus.DISMISSED
    store.save_proposal(original)

    store.save_matter(_bill_matter(due_at="2026-09-01T00:00:00+00:00"))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    active = store.list_proposals(status=ProposalStatus.PROPOSED)
    assert len(active) == 1
    assert active[0].id != original.id
    assert store.get_proposal(original.id).status is ProposalStatus.DISMISSED


def test_different_matters_reconcile_independently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    _seed_bill(store)
    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)

    assert len(store.list_proposals(status=ProposalStatus.PROPOSED)) == 2

    store.save_matter(_bill_matter(status=MatterStatus.RESOLVED))
    reconciler.reconcile(now=NOW + timedelta(hours=1))

    active = store.list_proposals(status=ProposalStatus.PROPOSED)
    assert len(active) == 1
    assert active[0].matter_id == "matter-event"
    assert len(store.list_proposals(status=ProposalStatus.INVALIDATED)) == 1


def test_failed_successor_insert_rolls_back_the_predecessor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    ProposalReconciler(store).reconcile(now=NOW)
    incumbent = store.list_proposals()[0]

    # A successor whose fingerprint is already taken cannot be inserted.
    clash = replace(
        incumbent,
        id="prop-successor",
        fingerprint=incumbent.fingerprint,
        content_hash="different",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.replace_proposal(
            incumbent.id,
            clash,
            status=ProposalStatus.SUPERSEDED,
            updated_at="2026-08-16T12:00:00+00:00",
            status_reason=REASON_CONTENT_CHANGED,
        )

    rolled_back = store.get_proposal(incumbent.id)
    assert rolled_back == incumbent
    assert rolled_back.status is ProposalStatus.PROPOSED
    assert rolled_back.superseded_by == ""
    assert store.get_proposal("prop-successor") is None
    assert len(store.list_proposals()) == 1


def test_replacing_an_inactive_predecessor_fails_loudly(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    ProposalReconciler(store).reconcile(now=NOW)
    incumbent = store.list_proposals()[0]
    store.close_proposal(
        incumbent.id,
        status=ProposalStatus.EXPIRED,
        updated_at="2026-08-16T12:00:00+00:00",
        status_reason=REASON_EVENT_REACHED,
    )

    successor = replace(incumbent, id="prop-successor", fingerprint="fp-new")
    with pytest.raises(ProposalTransitionError):
        store.replace_proposal(
            incumbent.id,
            successor,
            status=ProposalStatus.SUPERSEDED,
            updated_at="2026-08-16T13:00:00+00:00",
        )

    assert store.get_proposal("prop-successor") is None
    assert store.get_proposal(incumbent.id).status is ProposalStatus.EXPIRED


def test_closing_a_terminal_proposal_changes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_event(store)
    ProposalReconciler(store).reconcile(now=NOW)
    incumbent = store.list_proposals()[0]
    assert store.close_proposal(
        incumbent.id,
        status=ProposalStatus.EXPIRED,
        updated_at="2026-08-16T12:00:00+00:00",
        status_reason=REASON_EVENT_REACHED,
    )
    snapshot = store.get_proposal(incumbent.id)

    assert not store.close_proposal(
        incumbent.id,
        status=ProposalStatus.INVALIDATED,
        updated_at="2026-08-17T12:00:00+00:00",
        status_reason=REASON_MATTER_CLOSED,
    )
    assert store.get_proposal(incumbent.id) == snapshot


def test_reconciliation_never_touches_matters_observations_or_checkpoints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operations.db"
    store = OperationsStore(path)
    _seed_event(store)
    _seed_bill(store)
    store.set_checkpoint("gmail", {"seen_ids": ["msg-1"]})
    before = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}

    reconciler = ProposalReconciler(store)
    reconciler.reconcile(now=NOW)
    reconciler.reconcile(now=EVENT_START)

    after = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}
    assert after == before
    assert store.list_proposals() != []


class _RefusingStore(OperationsStore):
    """Simulates a concurrent close: the conditional UPDATE matches zero rows."""

    def close_proposal(
        self,
        proposal_id: str,
        *,
        status: ProposalStatus,
        updated_at: str,
        status_reason: str = "",
        superseded_by: str = "",
    ) -> bool:
        return False


def test_stale_close_raises_instead_of_reporting_a_transition(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    store = OperationsStore(path)
    _seed_event(store)
    ProposalReconciler(store).reconcile(now=NOW)
    original = store.list_proposals()[0]

    with pytest.raises(ProposalTransitionError) as failure:
        ProposalReconciler(_RefusingStore(path)).reconcile(now=EVENT_START)

    message = str(failure.value)
    assert original.id in message
    assert "proposed" in message
    assert "landlord" not in message
    assert store.get_proposal(original.id).status is ProposalStatus.PROPOSED


def test_failed_close_records_no_transition_in_the_result(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    store = OperationsStore(path)
    _seed_event(store)
    ProposalReconciler(store).reconcile(now=NOW)
    original = store.list_proposals()[0]

    result = ProposalReconciliation()
    reconciler = ProposalReconciler(_RefusingStore(path))
    with pytest.raises(ProposalTransitionError):
        reconciler._close(original, ProposalStatus.EXPIRED, REASON_EVENT_REACHED, NOW, result)

    assert result.expired == []
    assert result.invalidated == []
    assert result.superseded == []
    assert result.created == []
    assert not result.changed


def test_failed_close_creates_no_successor_or_unrelated_proposal(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    store = OperationsStore(path)
    _seed_event(store)
    _seed_bill(store)
    ProposalReconciler(store).reconcile(now=NOW)
    before = _dump(path, "proposals")

    with pytest.raises(ProposalTransitionError):
        ProposalReconciler(_RefusingStore(path)).reconcile(now=EVENT_START)

    assert _dump(path, "proposals") == before
    assert len(store.list_proposals()) == 2
    assert len(store.list_proposals(status=ProposalStatus.PROPOSED)) == 2


def test_reconciler_imports_no_execution_dependencies() -> None:
    tree = ast.parse(Path(reconcile_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = (
        "wally.providers",
        "wally.adapters",
        "wally.orchestrator",
        "wally.safety",
        "wally.secrets",
        "wally.reasoning",
        "wally.session",
        "wally.models.actions",
        "wally.models.workflow",
        "wally.runtime.execution_router",
        "wally.runtime.finance_safety",
        "wally.runtime.verification_engine",
        "openai",
        "httpx",
        "requests",
        "playwright",
        "socket",
        "subprocess",
        "sqlite3",
    )
    for module in sorted(imported):
        assert not module.startswith(forbidden), module

    exported = vars(reconcile_module)
    for name in ("PlannedAction", "ActionClass", "ToolRegistry", "ApprovalGate", "SecretsProvider"):
        assert name not in exported
