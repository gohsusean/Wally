"""SQLite persistence for observations, matters, proposals, and observe checkpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from wally.exceptions import ProposalTransitionError
from wally.models.ops import (
    OPEN_PROPOSAL_STATUSES,
    REOPENABLE_PROPOSAL_STATUSES,
    TRUSTED_DECISION_ORIGINS,
    USER_DECISION_STATUSES,
    Matter,
    MatterDomain,
    MatterStatus,
    Observation,
    ObservationCategory,
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)

_SYSTEM_TERMINAL_STATUSES = frozenset(
    {
        ProposalStatus.SUPERSEDED,
        ProposalStatus.INVALIDATED,
        ProposalStatus.EXPIRED,
        ProposalStatus.DISMISSED,
    }
)
_DECISION_COLUMNS = (
    ("decision", "TEXT NOT NULL DEFAULT ''"),
    ("decided_at", "TEXT NOT NULL DEFAULT ''"),
    ("decision_origin", "TEXT NOT NULL DEFAULT ''"),
    ("decision_note", "TEXT NOT NULL DEFAULT ''"),
    ("defer_until", "TEXT NOT NULL DEFAULT ''"),
    ("decision_fingerprint", "TEXT NOT NULL DEFAULT ''"),
)


class OperationsStore:
    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
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
                """
            )
            conn.execute(
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
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    source TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS proposals (
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
                    thread_id TEXT NOT NULL DEFAULT '',
                    decision TEXT NOT NULL DEFAULT '',
                    decided_at TEXT NOT NULL DEFAULT '',
                    decision_origin TEXT NOT NULL DEFAULT '',
                    decision_note TEXT NOT NULL DEFAULT '',
                    defer_until TEXT NOT NULL DEFAULT '',
                    decision_fingerprint TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_fingerprint
                ON proposals (fingerprint)
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_proposals_matter ON proposals (matter_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals (status)")
            # v0.13 databases already have the proposals table without decision columns.
            # Adding columns and widening the open-status index leaves existing rows in place.
            _ensure_proposal_decision_columns(conn)
            _ensure_open_proposal_index(conn)

    def get_observation_by_fingerprint(self, fingerprint: str) -> Observation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM observations WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return _observation_from_row(row) if row else None

    def save_observation(self, observation: Observation) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    id, fingerprint, source, source_id, observed_at, source_timestamp,
                    category, title, summary, trusted, authority, confidence,
                    thread_id, related_knowledge_id, related_matter_id, extra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.id,
                    observation.fingerprint,
                    observation.source,
                    observation.source_id,
                    observation.observed_at,
                    observation.source_timestamp,
                    observation.category.value,
                    observation.title,
                    observation.summary,
                    1 if observation.trusted else 0,
                    observation.authority,
                    observation.confidence,
                    observation.thread_id,
                    observation.related_knowledge_id,
                    observation.related_matter_id,
                    json.dumps(observation.extra),
                ),
            )

    def list_observations(self, *, since: str | None = None) -> list[Observation]:
        sql = "SELECT * FROM observations"
        params: tuple = ()
        if since:
            sql += " WHERE observed_at >= ?"
            params = (since,)
        sql += " ORDER BY observed_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_observation_from_row(row) for row in rows]

    def get_matter(self, matter_id: str) -> Matter | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM matters WHERE id = ?", (matter_id,)
            ).fetchone()
        return _matter_from_row(row) if row else None

    def get_matter_by_fingerprint(self, fingerprint: str) -> Matter | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM matters WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return _matter_from_row(row) if row else None

    def find_matters_by_thread(self, thread_id: str) -> list[Matter]:
        if not thread_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM matters WHERE thread_id = ?", (thread_id,)
            ).fetchall()
        return [_matter_from_row(row) for row in rows]

    def list_matters(self) -> list[Matter]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM matters ORDER BY updated_at DESC").fetchall()
        return [_matter_from_row(row) for row in rows]

    def save_matter(self, matter: Matter) -> None:
        payload = (
            matter.id,
            matter.fingerprint,
            matter.title,
            matter.domain.value,
            matter.status.value,
            matter.created_at,
            matter.updated_at,
            matter.summary,
            matter.open_reason,
            matter.last_change,
            matter.priority_score,
            json.dumps(list(matter.priority_reasons)),
            matter.due_at,
            matter.expected_by,
            json.dumps(list(matter.observation_ids)),
            json.dumps(list(matter.knowledge_ids)),
            matter.thread_id,
            matter.source,
            matter.resolution_evidence,
            matter.confidence,
            matter.recurrence_key,
        )
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM matters WHERE id = ?", (matter.id,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE matters SET
                        fingerprint=?, title=?, domain=?, status=?, created_at=?,
                        updated_at=?, summary=?, open_reason=?, last_change=?,
                        priority_score=?, priority_reasons=?, due_at=?, expected_by=?,
                        observation_ids=?, knowledge_ids=?, thread_id=?, source=?,
                        resolution_evidence=?, confidence=?, recurrence_key=?
                    WHERE id=?
                    """,
                    payload[1:] + (matter.id,),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO matters (
                        id, fingerprint, title, domain, status, created_at, updated_at,
                        summary, open_reason, last_change, priority_score, priority_reasons,
                        due_at, expected_by, observation_ids, knowledge_ids, thread_id,
                        source, resolution_evidence, confidence, recurrence_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )

    def get_proposal(self, proposal_id: str) -> ProposedAction | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return _proposal_from_row(row) if row else None

    def get_proposal_by_fingerprint(self, fingerprint: str) -> ProposedAction | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM proposals WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return _proposal_from_row(row) if row else None

    def list_proposals(
        self,
        *,
        matter_id: str | None = None,
        intent: ProposalIntent | None = None,
        status: ProposalStatus | None = None,
    ) -> list[ProposedAction]:
        clauses: list[str] = []
        params: list[str] = []
        if matter_id:
            clauses.append("matter_id = ?")
            params.append(matter_id)
        if intent:
            clauses.append("intent = ?")
            params.append(intent.value)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        sql = "SELECT * FROM proposals"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_proposal_from_row(row) for row in rows]

    def list_open_proposals(self) -> list[ProposedAction]:
        """Proposals that still occupy the one-live-row slot for a matter and intent."""
        statuses = tuple(status.value for status in OPEN_PROPOSAL_STATUSES)
        placeholders = ", ".join("?" for _ in statuses)
        sql = (
            "SELECT * FROM proposals "
            f"WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, id ASC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, statuses).fetchall()
        return [_proposal_from_row(row) for row in rows]

    def save_proposal(self, proposal: ProposedAction) -> None:
        """Insert a proposal version, or update the row that already carries this id.

        The unique indexes reject a second row for one fingerprint and a second open
        proposal for one (matter_id, intent); both raise sqlite3.IntegrityError and
        roll back, leaving stored history intact.

        User decisions are not written here. ``record_decision`` is the only insert
        path into approved, rejected, or deferred.
        """
        if _carries_user_decision(proposal):
            raise ProposalTransitionError(
                "user decisions are recorded only through an explicit approval command"
            )
        payload = _proposal_payload(proposal)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT status, decision FROM proposals WHERE id = ?", (proposal.id,)
            ).fetchone()
            if existing:
                if (
                    existing["status"] in {status.value for status in USER_DECISION_STATUSES}
                    or existing["decision"]
                ):
                    raise ProposalTransitionError(
                        f"proposal {proposal.id} already has a user decision; "
                        "save_proposal cannot rewrite it"
                    )
                conn.execute(
                    """
                    UPDATE proposals SET
                        fingerprint=?, matter_id=?, intent=?, status=?, provenance=?,
                        risk=?, created_at=?, updated_at=?, title=?, rationale=?,
                        suggestion=?, confidence=?, content_hash=?, expires_at=?,
                        status_reason=?, superseded_by=?, observation_ids=?,
                        knowledge_ids=?, event_id=?, thread_id=?, decision=?,
                        decided_at=?, decision_origin=?, decision_note=?, defer_until=?,
                        decision_fingerprint=?
                    WHERE id=?
                    """,
                    payload[1:] + (proposal.id,),
                )
            else:
                _insert_proposal(conn, payload)

    def record_decision(
        self,
        proposal_id: str,
        *,
        status: ProposalStatus,
        updated_at: str,
        decision_origin: str,
        decision_note: str = "",
        defer_until: str = "",
        status_reason: str,
    ) -> bool:
        """Record an explicit user decision on a proposal that is still pending.

        The decision fingerprint is copied from the stored row, so a caller cannot
        attach this decision to a different proposal version. Returns True when
        exactly one pending row changed.
        """
        if status not in USER_DECISION_STATUSES:
            raise ProposalTransitionError("record_decision only accepts a user decision status")
        if decision_origin not in TRUSTED_DECISION_ORIGINS:
            raise ProposalTransitionError("decision origin is not a trusted user command")
        if status == ProposalStatus.DEFERRED and not defer_until:
            raise ProposalTransitionError("defer requires defer_until")
        if status != ProposalStatus.DEFERRED and defer_until:
            raise ProposalTransitionError("only a defer records defer_until")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE proposals
                SET status = ?, updated_at = ?, decision = ?, decided_at = ?,
                    decision_origin = ?, decision_note = ?, defer_until = ?,
                    decision_fingerprint = fingerprint, status_reason = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    updated_at,
                    status.value,
                    updated_at,
                    decision_origin,
                    decision_note,
                    defer_until,
                    status_reason,
                    proposal_id,
                    ProposalStatus.PROPOSED.value,
                ),
            )
            return cursor.rowcount == 1

    def release_defer(
        self,
        proposal_id: str,
        *,
        updated_at: str,
        status_reason: str,
    ) -> bool:
        """Return a deferred proposal to pending once its defer window has elapsed.

        Decision fields are cleared on the row. The audit log keeps the defer itself.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE proposals
                SET status = ?, updated_at = ?, status_reason = ?,
                    decision = '', decided_at = '', decision_origin = '',
                    decision_note = '', defer_until = '', decision_fingerprint = ''
                WHERE id = ? AND status = ?
                """,
                (
                    ProposalStatus.PROPOSED.value,
                    updated_at,
                    status_reason,
                    proposal_id,
                    ProposalStatus.DEFERRED.value,
                ),
            )
            return cursor.rowcount == 1

    def reopen_proposal(
        self,
        proposal_id: str,
        *,
        updated_at: str,
        status_reason: str,
    ) -> bool:
        """Reopen a system-closed proposal without keeping any earlier decision."""
        reopenable = tuple(status.value for status in REOPENABLE_PROPOSAL_STATUSES)
        placeholders = ", ".join("?" for _ in reopenable)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE proposals
                SET status = ?, updated_at = ?, status_reason = ?, superseded_by = '',
                    decision = '', decided_at = '', decision_origin = '',
                    decision_note = '', defer_until = '', decision_fingerprint = ''
                WHERE id = ? AND status IN ({placeholders})
                """,
                (
                    ProposalStatus.PROPOSED.value,
                    updated_at,
                    status_reason,
                    proposal_id,
                    *reopenable,
                ),
            )
            return cursor.rowcount == 1

    def close_proposal(
        self,
        proposal_id: str,
        *,
        status: ProposalStatus,
        updated_at: str,
        status_reason: str = "",
        superseded_by: str = "",
    ) -> bool:
        """Move an open proposal to a system-terminal status.

        Conditional on the row still being open (proposed, approved, or deferred), so
        a terminal row is never rewritten and a concurrent close is not applied twice.
        This cannot enter a user-decision status. `created_at` is left alone;
        `updated_at` moves only because a real transition happened. Returns True when
        exactly one row changed.
        """
        if status not in _SYSTEM_TERMINAL_STATUSES:
            raise ProposalTransitionError(
                "close_proposal only applies a system terminal status"
            )
        with self._connect() as conn:
            cursor = _close_active_proposal(
                conn,
                proposal_id,
                status=status,
                updated_at=updated_at,
                status_reason=status_reason,
                superseded_by=superseded_by,
            )
            return cursor.rowcount == 1

    def replace_proposal(
        self,
        predecessor_id: str,
        successor: ProposedAction,
        *,
        status: ProposalStatus,
        updated_at: str,
        status_reason: str = "",
    ) -> None:
        """Close the incumbent and insert its replacement in one transaction.

        Both statements share a connection, so a failed successor insert rolls the
        predecessor back to its previous open status rather than leaving the matter
        with no active proposal. A predecessor that is no longer open raises instead
        of silently creating a second active row.
        """
        superseded_by = successor.id if status == ProposalStatus.SUPERSEDED else ""
        with self._connect() as conn:
            cursor = _close_active_proposal(
                conn,
                predecessor_id,
                status=status,
                updated_at=updated_at,
                status_reason=status_reason,
                superseded_by=superseded_by,
            )
            if cursor.rowcount != 1:
                raise ProposalTransitionError(
                    f"proposal {predecessor_id} is no longer active; "
                    f"{cursor.rowcount} rows matched"
                )
            _insert_proposal(conn, _proposal_payload(successor))

    def get_checkpoint(self, source: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM checkpoints WHERE source = ?", (source,)
            ).fetchone()
        if row is None:
            return {}
        return json.loads(row["payload"])

    def set_checkpoint(self, source: str, payload: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints (source, payload) VALUES (?, ?)
                ON CONFLICT(source) DO UPDATE SET payload = excluded.payload
                """,
                (source, json.dumps(payload)),
            )


def _proposal_payload(proposal: ProposedAction) -> tuple:
    return (
        proposal.id,
        proposal.fingerprint,
        proposal.matter_id,
        proposal.intent.value,
        proposal.status.value,
        proposal.provenance.value,
        proposal.risk.value,
        proposal.created_at,
        proposal.updated_at,
        proposal.title,
        proposal.rationale,
        proposal.suggestion,
        proposal.confidence,
        proposal.content_hash,
        proposal.expires_at,
        proposal.status_reason,
        proposal.superseded_by,
        json.dumps(list(proposal.observation_ids)),
        json.dumps(list(proposal.knowledge_ids)),
        proposal.event_id,
        proposal.thread_id,
        proposal.decision,
        proposal.decided_at,
        proposal.decision_origin,
        proposal.decision_note,
        proposal.defer_until,
        proposal.decision_fingerprint,
    )


def _insert_proposal(conn: sqlite3.Connection, payload: tuple) -> None:
    conn.execute(
        """
        INSERT INTO proposals (
            id, fingerprint, matter_id, intent, status, provenance, risk,
            created_at, updated_at, title, rationale, suggestion, confidence,
            content_hash, expires_at, status_reason, superseded_by,
            observation_ids, knowledge_ids, event_id, thread_id,
            decision, decided_at, decision_origin, decision_note, defer_until,
            decision_fingerprint
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """,
        payload,
    )


def _close_active_proposal(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    status: ProposalStatus,
    updated_at: str,
    status_reason: str,
    superseded_by: str,
) -> sqlite3.Cursor:
    return conn.execute(
        """
        UPDATE proposals
        SET status = ?, updated_at = ?, status_reason = ?, superseded_by = ?
        WHERE id = ? AND status IN (?, ?, ?)
        """,
        (
            status.value,
            updated_at,
            status_reason,
            superseded_by,
            proposal_id,
            *(status.value for status in OPEN_PROPOSAL_STATUSES),
        ),
    )


def _observation_from_row(row: sqlite3.Row) -> Observation:
    return Observation(
        id=row["id"],
        fingerprint=row["fingerprint"],
        source=row["source"],
        source_id=row["source_id"],
        observed_at=row["observed_at"],
        source_timestamp=row["source_timestamp"],
        category=ObservationCategory(row["category"]),
        title=row["title"],
        summary=row["summary"],
        trusted=bool(row["trusted"]),
        authority=row["authority"],
        confidence=float(row["confidence"]),
        thread_id=row["thread_id"],
        related_knowledge_id=row["related_knowledge_id"],
        related_matter_id=row["related_matter_id"],
        extra=json.loads(row["extra"] or "{}"),
    )


def _matter_from_row(row: sqlite3.Row) -> Matter:
    return Matter(
        id=row["id"],
        fingerprint=row["fingerprint"],
        title=row["title"],
        domain=MatterDomain(row["domain"]),
        status=MatterStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        summary=row["summary"],
        open_reason=row["open_reason"],
        last_change=row["last_change"],
        priority_score=int(row["priority_score"]),
        priority_reasons=tuple(json.loads(row["priority_reasons"] or "[]")),
        due_at=row["due_at"] or "",
        expected_by=row["expected_by"] or "",
        observation_ids=tuple(json.loads(row["observation_ids"] or "[]")),
        knowledge_ids=tuple(json.loads(row["knowledge_ids"] or "[]")),
        thread_id=row["thread_id"] or "",
        source=row["source"] or "",
        resolution_evidence=row["resolution_evidence"] or "",
        confidence=float(row["confidence"]),
        recurrence_key=row["recurrence_key"] or "",
    )


def _proposal_from_row(row: sqlite3.Row) -> ProposedAction:
    return ProposedAction(
        id=row["id"],
        fingerprint=row["fingerprint"],
        matter_id=row["matter_id"],
        intent=ProposalIntent(row["intent"]),
        status=ProposalStatus(row["status"]),
        provenance=ProposalProvenance(row["provenance"]),
        risk=ProposalRisk(row["risk"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        title=row["title"],
        rationale=row["rationale"],
        suggestion=row["suggestion"],
        confidence=float(row["confidence"]),
        content_hash=row["content_hash"] or "",
        expires_at=row["expires_at"] or "",
        status_reason=row["status_reason"] or "",
        superseded_by=row["superseded_by"] or "",
        observation_ids=tuple(json.loads(row["observation_ids"] or "[]")),
        knowledge_ids=tuple(json.loads(row["knowledge_ids"] or "[]")),
        event_id=row["event_id"] or "",
        thread_id=row["thread_id"] or "",
        decision=row["decision"] or "",
        decided_at=row["decided_at"] or "",
        decision_origin=row["decision_origin"] or "",
        decision_note=row["decision_note"] or "",
        defer_until=row["defer_until"] or "",
        decision_fingerprint=row["decision_fingerprint"] or "",
    )


def _carries_user_decision(proposal: ProposedAction) -> bool:
    if proposal.status in USER_DECISION_STATUSES:
        return True
    return bool(
        proposal.decision
        or proposal.decided_at
        or proposal.decision_origin
        or proposal.decision_note
        or proposal.defer_until
        or proposal.decision_fingerprint
    )


def _ensure_proposal_decision_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
    for name, declaration in _DECISION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE proposals ADD COLUMN {name} {declaration}")


def _ensure_open_proposal_index(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_proposals_active'"
    ).fetchone()
    sql = (row[0] or "") if row else ""
    if "approved" in sql and "deferred" in sql:
        return
    conn.execute("DROP INDEX IF EXISTS idx_proposals_active")
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_proposals_active
        ON proposals (matter_id, intent)
        WHERE status IN ('proposed', 'approved', 'deferred')
        """
    )
