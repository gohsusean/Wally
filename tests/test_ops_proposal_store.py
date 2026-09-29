"""v0.13 Phase 2 persistence — additive proposals table, versioning, and history."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from wally.models.ops import (
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)
from wally.ops.store import OperationsStore

# Exactly the v0.12.1 schema: observations, matters, checkpoints, and no proposals.
LEGACY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS observations (
        id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        source_timestamp TEXT NOT NULL,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        trusted INTEGER NOT NULL,
        authority TEXT NOT NULL,
        confidence REAL NOT NULL,
        thread_id TEXT NOT NULL DEFAULT '',
        related_knowledge_id TEXT NOT NULL DEFAULT '',
        related_matter_id TEXT NOT NULL DEFAULT '',
        extra TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matters (
        id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        domain TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        summary TEXT NOT NULL,
        open_reason TEXT NOT NULL,
        last_change TEXT NOT NULL,
        priority_score INTEGER NOT NULL DEFAULT 0,
        priority_reasons TEXT NOT NULL DEFAULT '[]',
        due_at TEXT NOT NULL DEFAULT '',
        expected_by TEXT NOT NULL DEFAULT '',
        observation_ids TEXT NOT NULL DEFAULT '[]',
        knowledge_ids TEXT NOT NULL DEFAULT '[]',
        thread_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        resolution_evidence TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 1.0,
        recurrence_key TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        source TEXT PRIMARY KEY,
        payload TEXT NOT NULL
    )
    """,
)


def _proposal(
    *,
    proposal_id: str = "prop-1",
    fingerprint: str = "matter-1:prepare_for_event:hash-1",
    matter_id: str = "matter-1",
    intent: ProposalIntent = ProposalIntent.PREPARE_FOR_EVENT,
    status: ProposalStatus = ProposalStatus.PROPOSED,
    updated_at: str = "2026-08-16T09:00:00+00:00",
    expires_at: str = "2026-08-17T14:00:00+00:00",
    content_hash: str = "hash-1",
) -> ProposedAction:
    return ProposedAction(
        id=proposal_id,
        fingerprint=fingerprint,
        matter_id=matter_id,
        intent=intent,
        status=status,
        provenance=ProposalProvenance.DETERMINISTIC_RULES,
        risk=ProposalRisk.LOW,
        created_at="2026-08-16T09:00:00+00:00",
        updated_at=updated_at,
        title="Quarterly review with the landlord",
        rationale="Event starts tomorrow and no preparation is recorded.",
        suggestion="Set aside time to prepare before this event starts.",
        confidence=0.75,
        content_hash=content_hash,
        expires_at=expires_at,
        observation_ids=("obs-1", "obs-2"),
        knowledge_ids=("know-1",),
        event_id="evt-1",
        thread_id="thread-1",
    )


def _bill_proposal(*, proposal_id: str = "prop-bill-1") -> ProposedAction:
    return ProposedAction(
        id=proposal_id,
        fingerprint=f"matter-bill:review_bill:{proposal_id}",
        matter_id="matter-bill",
        intent=ProposalIntent.REVIEW_BILL,
        status=ProposalStatus.PROPOSED,
        provenance=ProposalProvenance.DETERMINISTIC_RULES,
        risk=ProposalRisk.MEDIUM,
        created_at="2026-08-16T09:00:00+00:00",
        updated_at="2026-08-16T09:00:00+00:00",
        title="Electricity bill awaiting review",
        rationale="Open finance matter with a due date this week.",
        suggestion="Review this bill and decide how to handle it.",
    )


def _legacy_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        for statement in LEGACY_SCHEMA:
            conn.execute(statement)
        conn.execute(
            """
            INSERT INTO observations (
                id, fingerprint, source, source_id, observed_at, source_timestamp,
                category, title, summary, trusted, authority, confidence,
                thread_id, related_knowledge_id, related_matter_id, extra
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "obs-legacy",
                "fp-obs-legacy",
                "gmail",
                "msg-1",
                "2026-08-10T09:00:00+00:00",
                "2026-08-10T08:00:00+00:00",
                "email_received",
                "Legacy mail",
                "Legacy summary",
                0,
                "untrusted",
                0.9,
                "thread-legacy",
                "",
                "matter-legacy",
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO matters (
                id, fingerprint, title, domain, status, created_at, updated_at,
                summary, open_reason, last_change, priority_score, priority_reasons,
                due_at, expected_by, observation_ids, knowledge_ids, thread_id,
                source, resolution_evidence, confidence, recurrence_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "matter-legacy",
                "fp-matter-legacy",
                "Legacy matter",
                "finance",
                "open",
                "2026-08-10T09:00:00+00:00",
                "2026-08-11T09:00:00+00:00",
                "Legacy matter summary",
                "Awaiting payment",
                "Observed",
                40,
                json.dumps(["due soon"]),
                "2026-08-20T00:00:00+00:00",
                "",
                json.dumps(["obs-legacy"]),
                "[]",
                "thread-legacy",
                "gmail",
                "",
                1.0,
                "",
            ),
        )
        conn.execute(
            "INSERT INTO checkpoints (source, payload) VALUES (?, ?)",
            ("gmail", json.dumps({"last_seen": "2026-08-11T09:00:00+00:00"})),
        )
        conn.commit()
    finally:
        conn.close()


def _table_names(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def _index_names(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'proposals'"
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def _dump(path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        conn.close()
    return [tuple(row) for row in rows]


def test_fresh_database_creates_proposals_table_and_indexes(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    OperationsStore(path)

    assert "proposals" in _table_names(path)
    assert {
        "idx_proposals_fingerprint",
        "idx_proposals_matter",
        "idx_proposals_status",
        "idx_proposals_active",
    } <= _index_names(path)


def test_opening_legacy_database_adds_proposals_without_touching_existing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "operations.db"
    _legacy_database(path)
    before = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}
    assert "proposals" not in _table_names(path)

    OperationsStore(path)

    assert "proposals" in _table_names(path)
    after = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}
    assert after == before


def test_legacy_data_requires_no_reset(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    _legacy_database(path)

    store = OperationsStore(path)

    matter = store.get_matter("matter-legacy")
    assert matter is not None
    assert matter.title == "Legacy matter"
    assert matter.priority_reasons == ("due soon",)
    assert len(store.list_observations()) == 1
    assert store.get_checkpoint("gmail") == {"last_seen": "2026-08-11T09:00:00+00:00"}

    before = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}
    store.save_proposal(_proposal(matter_id="matter-legacy"))

    assert [p.id for p in store.list_proposals(matter_id="matter-legacy")] == ["prop-1"]
    after = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}
    assert after == before


def test_every_field_round_trips(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    original = _proposal()
    original.status_reason = "generated from calendar matter"
    original.superseded_by = ""

    store.save_proposal(original)
    loaded = store.get_proposal("prop-1")

    assert loaded == original
    assert loaded is not None
    assert loaded.intent is ProposalIntent.PREPARE_FOR_EVENT
    assert loaded.status is ProposalStatus.PROPOSED
    assert loaded.provenance is ProposalProvenance.DETERMINISTIC_RULES
    assert loaded.risk is ProposalRisk.LOW
    assert loaded.confidence == 0.75
    assert loaded.content_hash == "hash-1"
    assert loaded.expires_at == "2026-08-17T14:00:00+00:00"
    assert loaded.observation_ids == ("obs-1", "obs-2")
    assert loaded.knowledge_ids == ("know-1",)
    assert loaded.status_reason == "generated from calendar matter"


def test_supersession_reference_round_trips(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    original = _proposal()
    original.status = ProposalStatus.SUPERSEDED
    original.superseded_by = "prop-2"
    original.status_reason = "event time changed"

    store.save_proposal(original)
    loaded = store.get_proposal("prop-1")

    assert loaded is not None
    assert loaded.status is ProposalStatus.SUPERSEDED
    assert loaded.superseded_by == "prop-2"
    assert loaded.status_reason == "event time changed"


def test_bill_proposal_with_empty_expiry_round_trips(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    store.save_proposal(_bill_proposal())

    loaded = store.get_proposal("prop-bill-1")

    assert loaded is not None
    assert loaded.expires_at == ""
    assert loaded.intent is ProposalIntent.REVIEW_BILL
    assert loaded.observation_ids == ()
    assert loaded.knowledge_ids == ()


def test_saving_same_id_updates_lifecycle_fields_without_duplicating(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    proposal = _proposal()
    store.save_proposal(proposal)

    proposal.status = ProposalStatus.INVALIDATED
    proposal.status_reason = "matter resolved"
    proposal.updated_at = "2026-08-16T18:00:00+00:00"
    store.save_proposal(proposal)

    stored = store.list_proposals()
    assert len(stored) == 1
    assert stored[0].status is ProposalStatus.INVALIDATED
    assert stored[0].status_reason == "matter resolved"
    assert stored[0].updated_at == "2026-08-16T18:00:00+00:00"


def test_duplicate_version_fingerprint_is_rejected_without_losing_history(
    tmp_path: Path,
) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    store.save_proposal(_proposal())

    clash = _proposal(proposal_id="prop-2", matter_id="matter-2")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_proposal(clash)

    stored = store.list_proposals()
    assert len(stored) == 1
    assert stored[0].id == "prop-1"
    assert stored[0].title == "Quarterly review with the landlord"


def test_two_active_proposals_for_one_matter_and_intent_are_prohibited(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    store.save_proposal(_proposal())

    rival = _proposal(
        proposal_id="prop-2",
        fingerprint="matter-1:prepare_for_event:hash-2",
        content_hash="hash-2",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.save_proposal(rival)

    active = store.list_proposals(status=ProposalStatus.PROPOSED)
    assert [p.id for p in active] == ["prop-1"]


def test_successor_can_be_stored_once_predecessor_is_superseded(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    original = _proposal()
    store.save_proposal(original)

    original.status = ProposalStatus.SUPERSEDED
    original.superseded_by = "prop-2"
    store.save_proposal(original)
    successor = _proposal(
        proposal_id="prop-2",
        fingerprint="matter-1:prepare_for_event:hash-2",
        content_hash="hash-2",
        updated_at="2026-08-16T18:00:00+00:00",
    )
    store.save_proposal(successor)

    history = store.list_proposals(matter_id="matter-1", intent=ProposalIntent.PREPARE_FOR_EVENT)
    assert [p.id for p in history] == ["prop-2", "prop-1"]
    assert [p.status for p in history] == [
        ProposalStatus.PROPOSED,
        ProposalStatus.SUPERSEDED,
    ]


def test_historical_rows_remain_queryable(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    for index, status in enumerate(
        (
            ProposalStatus.SUPERSEDED,
            ProposalStatus.INVALIDATED,
            ProposalStatus.EXPIRED,
            ProposalStatus.DISMISSED,
        )
    ):
        store.save_proposal(
            _proposal(
                proposal_id=f"prop-{index}",
                fingerprint=f"matter-1:prepare_for_event:hash-{index}",
                content_hash=f"hash-{index}",
                status=status,
            )
        )
    store.save_proposal(
        _proposal(
            proposal_id="prop-live",
            fingerprint="matter-1:prepare_for_event:hash-live",
            content_hash="hash-live",
        )
    )

    assert len(store.list_proposals(matter_id="matter-1")) == 5
    for status in (
        ProposalStatus.SUPERSEDED,
        ProposalStatus.INVALIDATED,
        ProposalStatus.EXPIRED,
        ProposalStatus.DISMISSED,
    ):
        assert len(store.list_proposals(status=status)) == 1


def test_filters_and_ordering_are_deterministic(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    store.save_proposal(
        _proposal(
            proposal_id="prop-old",
            fingerprint="fp-old",
            matter_id="matter-a",
            updated_at="2026-08-14T09:00:00+00:00",
        )
    )
    store.save_proposal(
        _proposal(
            proposal_id="prop-b",
            fingerprint="fp-b",
            matter_id="matter-b",
            updated_at="2026-08-16T09:00:00+00:00",
        )
    )
    store.save_proposal(
        _proposal(
            proposal_id="prop-a",
            fingerprint="fp-a",
            matter_id="matter-c",
            updated_at="2026-08-16T09:00:00+00:00",
        )
    )
    store.save_proposal(_bill_proposal())

    ordered = [p.id for p in store.list_proposals()]
    assert ordered == ["prop-a", "prop-b", "prop-bill-1", "prop-old"]
    assert ordered == [p.id for p in store.list_proposals()]

    assert [p.id for p in store.list_proposals(matter_id="matter-b")] == ["prop-b"]
    assert [p.id for p in store.list_proposals(intent=ProposalIntent.REVIEW_BILL)] == [
        "prop-bill-1"
    ]
    assert len(store.list_proposals(status=ProposalStatus.PROPOSED)) == 4
    assert store.list_proposals(matter_id="matter-b", intent=ProposalIntent.REVIEW_BILL) == []


def test_lookup_by_fingerprint_returns_the_stored_version(tmp_path: Path) -> None:
    store = OperationsStore(tmp_path / "operations.db")
    store.save_proposal(_proposal())

    found = store.get_proposal_by_fingerprint("matter-1:prepare_for_event:hash-1")

    assert found is not None
    assert found.id == "prop-1"
    assert store.get_proposal_by_fingerprint("absent") is None
    assert store.get_proposal("absent") is None


V013_PROPOSALS = """
CREATE TABLE proposals (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    matter_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    provenance TEXT NOT NULL,
    risk TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    content_hash TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    status_reason TEXT NOT NULL DEFAULT '',
    superseded_by TEXT NOT NULL DEFAULT '',
    observation_ids TEXT NOT NULL DEFAULT '[]',
    knowledge_ids TEXT NOT NULL DEFAULT '[]',
    event_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT ''
)
"""


def _v013_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        for statement in LEGACY_SCHEMA:
            conn.execute(statement)
        conn.execute(V013_PROPOSALS)
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_proposals_fingerprint ON proposals (fingerprint)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_proposals_active
            ON proposals (matter_id, intent)
            WHERE status = 'proposed'
            """
        )
        conn.execute(
            """
            INSERT INTO proposals (
                id, fingerprint, matter_id, intent, status, provenance, risk,
                created_at, updated_at, title, rationale, suggestion, confidence,
                content_hash, expires_at, status_reason, superseded_by,
                observation_ids, knowledge_ids, event_id, thread_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prop-v013",
                "matter-legacy:review_bill:hash-v013",
                "matter-legacy",
                "review_bill",
                "proposed",
                "deterministic_rules",
                "medium",
                "2026-08-10T09:00:00+00:00",
                "2026-08-11T09:00:00+00:00",
                "Review open bill",
                "Open finance matter supported by a bill or a recurring obligation.",
                "Review this bill and decide how to handle it.",
                1.0,
                "hash-v013",
                "",
                "",
                "",
                "[]",
                "[]",
                "",
                "thread-legacy",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_v013_proposals_gain_decision_columns_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "operations.db"
    _v013_database(path)
    before = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}

    store = OperationsStore(path)
    loaded = store.get_proposal("prop-v013")

    assert loaded is not None
    assert loaded.status is ProposalStatus.PROPOSED
    assert loaded.fingerprint == "matter-legacy:review_bill:hash-v013"
    assert loaded.decision == ""
    assert loaded.decided_at == ""
    assert loaded.decision_origin == ""
    assert loaded.decision_note == ""
    assert loaded.defer_until == ""
    assert loaded.decision_fingerprint == ""
    assert loaded.title == "Review open bill"
    after = {table: _dump(path, table) for table in ("observations", "matters", "checkpoints")}
    assert after == before

    recorded = store.record_decision(
        "prop-v013",
        status=ProposalStatus.APPROVED,
        updated_at="2026-08-12T09:00:00+00:00",
        decision_origin="user_cli",
        status_reason="user approved",
    )
    assert recorded
    decided = store.get_proposal("prop-v013")
    assert decided is not None
    assert decided.status is ProposalStatus.APPROVED
    assert decided.decision_fingerprint == decided.fingerprint


def test_store_does_not_depend_on_execution_types() -> None:
    import wally.ops.store as store_module

    exported = vars(store_module)
    for name in (
        "PlannedAction",
        "ActionClass",
        "ToolCall",
        "ToolRegistry",
        "ApprovalGate",
        "SecretsProvider",
    ):
        assert name not in exported
