"""SQLite persistence for observations, matters, proposals, executions, and checkpoints."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from wally.exceptions import ProposalTransitionError
from wally.models.gateway import (
    ActiveMatter,
    ActiveSession,
    EvidenceItem,
    EvidenceKind,
    GatewayRequestRecord,
    HandleVisibility,
)
from wally.models.ops import (
    BLOCKING_EXECUTION_STATUSES,
    OPEN_PROPOSAL_STATUSES,
    REOPENABLE_PROPOSAL_STATUSES,
    USER_DECISION_STATUSES,
    ExecutionStatus,
    Matter,
    MatterDomain,
    MatterStatus,
    Observation,
    ObservationCategory,
    ProposalExecution,
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)
from wally.models.principal import RequestProvenance

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
    ("decision_principal", "TEXT NOT NULL DEFAULT ''"),
    ("decision_correlation_id", "TEXT NOT NULL DEFAULT ''"),
    ("request_provenance", "TEXT NOT NULL DEFAULT '{}'"),
)

_EXECUTION_PROVENANCE_COLUMNS = (
    ("principal", "TEXT NOT NULL DEFAULT ''"),
    ("correlation_id", "TEXT NOT NULL DEFAULT ''"),
    ("request_provenance", "TEXT NOT NULL DEFAULT '{}'"),
    ("verification_provenance", "TEXT NOT NULL DEFAULT '{}'"),
)

# v0.14 decisions named the local terminal channel in the origin itself. They were
# made by the single local operator, so the migration attributes them to that owner.
_LEGACY_LOCAL_DECISION_ORIGINS = ("user_cli", "user_repl")
_LEGACY_LOCAL_PRINCIPAL = "owner"


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
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS notion_edit_specs (
                    proposal_id TEXT PRIMARY KEY, specification TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notion_edit_claims (
                    page_id TEXT PRIMARY KEY, execution_id TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS human_review_events (
                    id INTEGER PRIMARY KEY, event TEXT NOT NULL, details TEXT NOT NULL,
                    provenance TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS human_confirmations (
                    id TEXT PRIMARY KEY, record TEXT NOT NULL, presentation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS telegram_edit_reviews (
                    notification_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    expires_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'open',
                    action TEXT NOT NULL DEFAULT '', callback_id TEXT NOT NULL DEFAULT '',
                    authorized_at REAL, owner_user_id TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '');
            """)
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
            _attribute_legacy_decisions(conn)
            _ensure_open_proposal_index(conn)
            # v0.15: execution attempts live in their own table; nothing above changes.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    proposal_fingerprint TEXT NOT NULL,
                    matter_id TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    executor TEXT NOT NULL DEFAULT '',
                    plan_digest TEXT NOT NULL DEFAULT '',
                    preflight TEXT NOT NULL DEFAULT '',
                    authorization TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    verification TEXT NOT NULL DEFAULT '',
                    verification_method TEXT NOT NULL DEFAULT '',
                    verified_at TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    failure_category TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '{}',
                    principal TEXT NOT NULL DEFAULT '',
                    correlation_id TEXT NOT NULL DEFAULT '',
                    request_provenance TEXT NOT NULL DEFAULT '{}',
                    verification_provenance TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            _ensure_columns(conn, "executions", _EXECUTION_PROVENANCE_COLUMNS)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_executions_proposal ON executions (proposal_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_executions_correlation "
                "ON executions (correlation_id)"
            )
            # One in-flight or successful attempt per approved proposal version.
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_blocking
                ON executions (proposal_fingerprint)
                WHERE status IN ('pending', 'running', 'executed_unverified', 'verified_success')
                """
            )
            # v0.16: gateway envelopes and continuity handles. Existing rows stay put.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_requests (
                    id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    active_matter_id TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    external_session_ref TEXT NOT NULL DEFAULT '',
                    external_request_ref TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gateway_requests_correlation "
                "ON gateway_requests (correlation_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_matters (
                    id TEXT PRIMARY KEY,
                    matter_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_matter_correlations (
                    active_matter_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY (active_matter_id, correlation_id)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_requests_ingress
                ON gateway_requests (channel, external_request_ref)
                WHERE external_request_ref != ''
                """
            )
            from wally.telegram.schema import ensure_telegram_schema

            ensure_telegram_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_matter_sessions (
                    active_matter_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    external_session_ref TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (active_matter_id, channel, external_session_ref)
                )
                """
            )

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
            row = conn.execute("SELECT * FROM matters WHERE id = ?", (matter_id,)).fetchone()
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
            existing = conn.execute("SELECT id FROM matters WHERE id = ?", (matter.id,)).fetchone()
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
                        decision_fingerprint=?, decision_principal=?,
                        decision_correlation_id=?, request_provenance=?
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
        decided_by: RequestProvenance,
        decision_note: str = "",
        defer_until: str = "",
        status_reason: str,
    ) -> bool:
        """Record an explicit user decision on a proposal that is still pending.

        Authorization happens before this call, in the application service, against
        the principal authority. The store only refuses a decision with no named
        principal and channel, so an anonymous write cannot land here.

        The decision fingerprint is copied from the stored row, so a caller cannot
        attach this decision to a different proposal version. Returns True when
        exactly one pending row changed.
        """
        if status not in USER_DECISION_STATUSES:
            raise ProposalTransitionError("record_decision only accepts a user decision status")
        if not isinstance(decided_by, RequestProvenance) or not (
            decided_by.principal and decided_by.channel
        ):
            raise ProposalTransitionError("a decision needs an authenticated principal and channel")
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
                    decision_fingerprint = fingerprint, status_reason = ?,
                    decision_principal = ?, decision_correlation_id = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    updated_at,
                    status.value,
                    updated_at,
                    decided_by.channel,
                    decision_note,
                    defer_until,
                    status_reason,
                    decided_by.principal,
                    decided_by.correlation_id,
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
                    decision_note = '', defer_until = '', decision_fingerprint = '',
                    decision_principal = '', decision_correlation_id = ''
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
                    decision_note = '', defer_until = '', decision_fingerprint = '',
                    decision_principal = '', decision_correlation_id = ''
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
            raise ProposalTransitionError("close_proposal only applies a system terminal status")
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
                    f"proposal {predecessor_id} is no longer active; {cursor.rowcount} rows matched"
                )
            _insert_proposal(conn, _proposal_payload(successor))

    def insert_notion_edit(self, proposal: ProposedAction, spec: dict) -> None:
        """Insert immutable edit intent and supersede its open predecessor atomically."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE proposals SET status='superseded', superseded_by=?, updated_at=?, "
                "status_reason='new Notion edit version' WHERE matter_id=? AND intent=? "
                "AND status IN ('proposed','approved','deferred')",
                (proposal.id, proposal.updated_at, proposal.matter_id, proposal.intent.value),
            )
            _insert_proposal(conn, _proposal_payload(proposal))
            conn.execute(
                "INSERT INTO notion_edit_specs VALUES (?,?)",
                (proposal.id, json.dumps(spec, sort_keys=True)),
            )
            self._edit_event(
                conn,
                "edit_proposed",
                {
                    "proposal_id": proposal.id,
                    "fingerprint": proposal.fingerprint,
                },
                proposal.request_provenance,
            )

    def get_notion_edit(self, proposal_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT specification FROM notion_edit_specs WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def latest_notion_edit(self, material_digest: str) -> ProposedAction | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT proposal_id FROM notion_edit_specs "
                "WHERE json_extract(specification,'$.material_digest')=? "
                "ORDER BY rowid DESC LIMIT 1",
                (material_digest,),
            ).fetchone()
        return self.get_proposal(row[0]) if row else None

    @staticmethod
    def _edit_event(conn, event, details, provenance) -> None:
        conn.execute(
            "INSERT INTO human_review_events(event,details,provenance,created_at) VALUES (?,?,?,?)",
            (
                event,
                json.dumps(details, sort_keys=True),
                json.dumps(provenance.as_dict()),
                datetime.now(UTC).isoformat(),
            ),
        )

    def note_edit_event(self, event, details, provenance) -> None:
        with self._connect() as conn:
            self._edit_event(conn, event, details, provenance)

    @staticmethod
    def _confirmation(conn, confirmation, presentation) -> None:
        if time.time() >= confirmation.expires_at:
            raise ProposalTransitionError("Human confirmation expired before commit.")
        # UNIQUE id makes replay fail closed. Records are attribution, not bearer
        # credentials. Only the service's synchronous authority callback supplies one.
        conn.execute(
            "INSERT INTO human_confirmations VALUES (?,?,?)",
            (
                confirmation.id,
                json.dumps(asdict(confirmation), sort_keys=True),
                json.dumps(presentation, sort_keys=True),
            ),
        )

    def record_edit_decisions(self, selections, confirmation, presentation) -> None:
        from wally.models.principal import RequestProvenance
        from wally.ops.decisions import UserDecision, parse_defer_until

        provenance = RequestProvenance(
            principal=confirmation.principal,
            channel=confirmation.channel,
            correlation_id=confirmation.correlation_id,
        )
        statuses = {
            UserDecision.APPROVE: "approved",
            UserDecision.REJECT: "rejected",
            UserDecision.DEFER: "deferred",
        }
        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._confirmation(conn, confirmation, presentation)
            for selection in selections:
                status = statuses[selection.decision]
                until = (
                    parse_defer_until(selection.defer_until, now=now)
                    if selection.decision == UserDecision.DEFER
                    else ""
                )
                cursor = conn.execute(
                    "UPDATE proposals SET status=?, decision=?, decided_at=?, updated_at=?, "
                    "decision_origin=?, decision_principal=?, decision_correlation_id=?, "
                    "decision_fingerprint=fingerprint, defer_until=?, status_reason=? "
                    "WHERE id=? AND fingerprint=? AND status='proposed' "
                    "AND intent='edit_notion_record' AND expires_at>? "
                    "AND EXISTS (SELECT 1 FROM matters WHERE matters.id=proposals.matter_id "
                    "AND matters.status='open')",
                    (
                        status,
                        status,
                        now.isoformat(),
                        now.isoformat(),
                        confirmation.channel,
                        confirmation.principal,
                        confirmation.correlation_id,
                        until,
                        "user " + status,
                        selection.proposal_id,
                        selection.fingerprint,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProposalTransitionError("Batch changed; no decisions recorded.")
            self._edit_event(
                conn,
                "edit_decisions_recorded",
                {
                    "confirmation_id": confirmation.id,
                    "selections": presentation["selections"],
                },
                provenance,
            )

    def start_notion_edit(self, execution, page_id, confirmation, presentation) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = conn.execute(
                "SELECT * FROM proposals WHERE id=? AND fingerprint=? AND status='approved' "
                "AND decision='approved' AND decision_fingerprint=fingerprint "
                "AND expires_at>? AND EXISTS (SELECT 1 FROM matters "
                "WHERE matters.id=proposals.matter_id AND matters.status='open')",
                (
                    execution.proposal_id,
                    execution.proposal_fingerprint,
                    datetime.now(UTC).isoformat(),
                ),
            ).fetchone()
            if (
                proposal is None
                or conn.execute(
                    "SELECT 1 FROM executions WHERE proposal_id=?", (execution.proposal_id,)
                ).fetchone()
            ):
                raise ProposalTransitionError("Approval changed or execution already attempted.")
            self._confirmation(conn, confirmation, presentation)
            conn.execute("INSERT INTO notion_edit_claims VALUES (?,?)", (page_id, execution.id))
            conn.execute(
                f"INSERT INTO executions ({_EXECUTION_COLUMN_LIST}) "
                f"VALUES ({','.join('?' for _ in _execution_payload(execution))})",
                _execution_payload(execution),
            )
            self._edit_event(
                conn,
                "edit_execution_started",
                {
                    "execution_id": execution.id,
                    "confirmation_id": confirmation.id,
                    "proposal_id": execution.proposal_id,
                    "fingerprint": execution.proposal_fingerprint,
                },
                execution.request_provenance,
            )

    def finish_notion_edit(self, execution, page_id) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payload = _execution_payload(execution)
            assignments = ", ".join(f"{name} = ?" for name in _EXECUTION_COLUMNS[1:])
            cursor = conn.execute(
                f"UPDATE executions SET {assignments} WHERE id=? AND status='executed_unverified'",
                payload[1:] + (execution.id,),
            )
            if cursor.rowcount != 1:
                raise ProposalTransitionError("Execution changed during verification.")
            conn.execute(
                "DELETE FROM notion_edit_claims WHERE page_id=? AND execution_id=?",
                (page_id, execution.id),
            )
            self._edit_event(
                conn,
                "edit_verified",
                {
                    "execution_id": execution.id,
                    "evidence": execution.evidence,
                },
                execution.verification_provenance,
            )

    def insert_execution(self, execution: ProposalExecution) -> bool:
        """Insert an execution attempt.

        Returns False when another pending, running, unverified, or verified attempt
        already holds this proposal fingerprint. The unique index enforces that, so a
        concurrent second ``execute`` cannot slip past a read-then-write race.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO executions ({_EXECUTION_COLUMN_LIST}) "
                    f"VALUES ({_EXECUTION_PLACEHOLDERS})",
                    _execution_payload(execution),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def transition_execution(
        self,
        execution: ProposalExecution,
        *,
        expected: ExecutionStatus,
    ) -> bool:
        """Write ``execution`` only if the stored row still has status ``expected``."""
        payload = _execution_payload(execution)
        assignments = ", ".join(f"{name}=?" for name in _EXECUTION_COLUMNS[1:])
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    f"UPDATE executions SET {assignments} WHERE id = ? AND status = ?",
                    payload[1:] + (execution.id, expected.value),
                )
                return cursor.rowcount == 1
        except sqlite3.IntegrityError:
            return False

    def get_execution(self, execution_id: str) -> ProposalExecution | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM executions WHERE id = ?", (execution_id,)).fetchone()
        return _execution_from_row(row) if row else None

    def list_executions(self, *, proposal_id: str | None = None) -> list[ProposalExecution]:
        sql = "SELECT * FROM executions"
        params: tuple = ()
        if proposal_id:
            sql += " WHERE proposal_id = ?"
            params = (proposal_id,)
        sql += " ORDER BY created_at DESC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_execution_from_row(row) for row in rows]

    def correlated(
        self, correlation_id: str
    ) -> tuple[list[ProposedAction], list[ProposalExecution]]:
        """Proposals and executions that carry ``correlation_id`` at any stage."""
        if not correlation_id:
            return [], []
        with self._connect() as conn:
            proposal_rows = conn.execute(
                """
                SELECT * FROM proposals
                WHERE decision_correlation_id = ?
                   OR json_extract(request_provenance, '$.correlation_id') = ?
                ORDER BY created_at ASC, id ASC
                """,
                (correlation_id, correlation_id),
            ).fetchall()
            execution_rows = conn.execute(
                """
                SELECT * FROM executions
                WHERE correlation_id = ?
                   OR json_extract(verification_provenance, '$.correlation_id') = ?
                ORDER BY created_at ASC, id ASC
                """,
                (correlation_id, correlation_id),
            ).fetchall()
        return (
            [_proposal_from_row(row) for row in proposal_rows],
            [_execution_from_row(row) for row in execution_rows],
        )

    def blocking_execution(self, proposal_fingerprint: str) -> ProposalExecution | None:
        statuses = tuple(status.value for status in BLOCKING_EXECUTION_STATUSES)
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM executions "
                f"WHERE proposal_fingerprint = ? AND status IN ({placeholders})",
                (proposal_fingerprint, *statuses),
            ).fetchone()
        return _execution_from_row(row) if row else None

    def correlation_exists(self, correlation_id: str) -> bool:
        """True when this id already names a request, proposal, or execution lineage."""
        if not correlation_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM gateway_requests WHERE correlation_id = ? LIMIT 1",
                (correlation_id,),
            ).fetchone()
        if row is not None:
            return True
        proposals, executions = self.correlated(correlation_id)
        return bool(proposals or executions)

    def save_gateway_request(self, request: GatewayRequestRecord) -> None:
        evidence = [item.as_dict() for item in request.evidence]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gateway_requests (
                    id, correlation_id, active_matter_id, channel, principal,
                    external_session_ref, external_request_ref, evidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.id,
                    request.correlation_id,
                    request.active_matter_id,
                    request.channel,
                    request.principal,
                    request.external_session_ref,
                    request.external_request_ref,
                    json.dumps(evidence, sort_keys=True),
                    request.created_at,
                ),
            )

    def get_gateway_request_by_external(
        self, channel: str, external_request_ref: str
    ) -> GatewayRequestRecord | None:
        if not channel or not external_request_ref:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM gateway_requests
                WHERE channel = ? AND external_request_ref = ?
                """,
                (channel, external_request_ref),
            ).fetchone()
        return _gateway_request_from_row(row) if row else None

    def list_gateway_requests(self, *, correlation_id: str) -> list[GatewayRequestRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM gateway_requests WHERE correlation_id = ? ORDER BY created_at, id",
                (correlation_id,),
            ).fetchall()
        return [_gateway_request_from_row(row) for row in rows]

    def save_active_matter(self, handle: ActiveMatter) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO active_matters (
                    id, matter_id, title, visibility, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    handle.id,
                    handle.matter_id,
                    handle.title,
                    handle.visibility.value,
                    handle.created_at,
                    handle.updated_at,
                ),
            )

    def set_handle_visibility(
        self, active_matter_id: str, visibility: HandleVisibility, *, updated_at: str
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE active_matters SET visibility = ?, updated_at = ? WHERE id = ?",
                (visibility.value, updated_at, active_matter_id),
            )
            return cursor.rowcount == 1

    def get_active_matter(self, active_matter_id: str) -> ActiveMatter | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM active_matters WHERE id = ?", (active_matter_id,)
            ).fetchone()
            if row is None:
                return None
            correlations = conn.execute(
                """
                SELECT correlation_id FROM active_matter_correlations
                WHERE active_matter_id = ? ORDER BY linked_at, correlation_id
                """,
                (active_matter_id,),
            ).fetchall()
            sessions = conn.execute(
                """
                SELECT * FROM active_matter_sessions
                WHERE active_matter_id = ?
                ORDER BY first_seen_at, channel, external_session_ref
                """,
                (active_matter_id,),
            ).fetchall()
        return _active_matter_from_row(row, correlations, sessions)

    def list_active_matters(
        self, *, visibility: HandleVisibility | None = None
    ) -> list[ActiveMatter]:
        handles: list[ActiveMatter] = []
        with self._connect() as conn:
            sql = "SELECT id FROM active_matters"
            params: tuple = ()
            if visibility is not None:
                sql += " WHERE visibility = ?"
                params = (visibility.value,)
            sql += " ORDER BY updated_at DESC, id ASC"
            ids = [row["id"] for row in conn.execute(sql, params).fetchall()]
        for active_id in ids:
            loaded = self.get_active_matter(active_id)
            if loaded is not None:
                handles.append(loaded)
        return handles

    def link_correlation(
        self, active_matter_id: str, correlation_id: str, *, linked_at: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO active_matter_correlations (
                    active_matter_id, correlation_id, linked_at
                ) VALUES (?, ?, ?)
                """,
                (active_matter_id, correlation_id, linked_at),
            )

    def active_matter_ids_for_correlation(self, correlation_id: str) -> tuple[str, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT active_matter_id FROM active_matter_correlations
                WHERE correlation_id = ? ORDER BY linked_at, active_matter_id
                """,
                (correlation_id,),
            ).fetchall()
        return tuple(row["active_matter_id"] for row in rows)

    def touch_session(
        self,
        active_matter_id: str,
        *,
        channel: str,
        external_session_ref: str,
        seen_at: str,
    ) -> None:
        """Record a session ref. A new ref does not remove an older one."""
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT first_seen_at FROM active_matter_sessions
                WHERE active_matter_id = ? AND channel = ? AND external_session_ref = ?
                """,
                (active_matter_id, channel, external_session_ref),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO active_matter_sessions (
                        active_matter_id, channel, external_session_ref,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (active_matter_id, channel, external_session_ref, seen_at, seen_at),
                )
                return
            conn.execute(
                """
                UPDATE active_matter_sessions SET last_seen_at = ?
                WHERE active_matter_id = ? AND channel = ? AND external_session_ref = ?
                """,
                (seen_at, active_matter_id, channel, external_session_ref),
            )

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
        proposal.decision_principal,
        proposal.decision_correlation_id,
        json.dumps(proposal.request_provenance.as_dict(), sort_keys=True),
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
            decision_fingerprint, decision_principal, decision_correlation_id,
            request_provenance
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
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
        decision_principal=row["decision_principal"] or "",
        decision_correlation_id=row["decision_correlation_id"] or "",
        request_provenance=_provenance(row["request_provenance"]),
    )


def _provenance(raw: str | None) -> RequestProvenance:
    try:
        return RequestProvenance.from_dict(json.loads(raw or "{}"))
    except (TypeError, ValueError):
        return RequestProvenance()


_EXECUTION_COLUMNS = (
    "id",
    "proposal_id",
    "proposal_fingerprint",
    "matter_id",
    "intent",
    "status",
    "origin",
    "created_at",
    "updated_at",
    "executor",
    "plan_digest",
    "preflight",
    "authorization",
    "started_at",
    "finished_at",
    "verification",
    "verification_method",
    "verified_at",
    "outcome",
    "failure_category",
    "evidence",
    "principal",
    "correlation_id",
    "request_provenance",
    "verification_provenance",
)
_EXECUTION_COLUMN_LIST = ", ".join(_EXECUTION_COLUMNS)
_EXECUTION_PLACEHOLDERS = ", ".join("?" for _ in _EXECUTION_COLUMNS)


def _execution_payload(execution: ProposalExecution) -> tuple:
    return (
        execution.id,
        execution.proposal_id,
        execution.proposal_fingerprint,
        execution.matter_id,
        execution.intent.value,
        execution.status.value,
        execution.origin,
        execution.created_at,
        execution.updated_at,
        execution.executor,
        execution.plan_digest,
        execution.preflight,
        execution.authorization,
        execution.started_at,
        execution.finished_at,
        execution.verification,
        execution.verification_method,
        execution.verified_at,
        execution.outcome,
        execution.failure_category,
        json.dumps(execution.evidence, sort_keys=True),
        execution.principal,
        execution.correlation_id,
        json.dumps(execution.request_provenance.as_dict(), sort_keys=True),
        json.dumps(execution.verification_provenance.as_dict(), sort_keys=True),
    )


def _execution_from_row(row: sqlite3.Row) -> ProposalExecution:
    return ProposalExecution(
        id=row["id"],
        proposal_id=row["proposal_id"],
        proposal_fingerprint=row["proposal_fingerprint"],
        matter_id=row["matter_id"],
        intent=ProposalIntent(row["intent"]),
        status=ExecutionStatus(row["status"]),
        origin=row["origin"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        executor=row["executor"] or "",
        plan_digest=row["plan_digest"] or "",
        preflight=row["preflight"] or "",
        authorization=row["authorization"] or "",
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"] or "",
        verification=row["verification"] or "",
        verification_method=row["verification_method"] or "",
        verified_at=row["verified_at"] or "",
        outcome=row["outcome"] or "",
        failure_category=row["failure_category"] or "",
        evidence=json.loads(row["evidence"] or "{}"),
        principal=row["principal"] or "",
        correlation_id=row["correlation_id"] or "",
        request_provenance=_provenance(row["request_provenance"]),
        verification_provenance=_provenance(row["verification_provenance"]),
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
        or proposal.decision_principal
        or proposal.decision_correlation_id
    )


def _ensure_proposal_decision_columns(conn: sqlite3.Connection) -> None:
    _ensure_columns(conn, "proposals", _DECISION_COLUMNS)


def _ensure_columns(
    conn: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _attribute_legacy_decisions(conn: sqlite3.Connection) -> None:
    placeholders = ", ".join("?" for _ in _LEGACY_LOCAL_DECISION_ORIGINS)
    conn.execute(
        f"""
        UPDATE proposals SET decision_principal = ?
        WHERE decision != '' AND decision_principal = ''
          AND decision_origin IN ({placeholders})
        """,
        (_LEGACY_LOCAL_PRINCIPAL, *_LEGACY_LOCAL_DECISION_ORIGINS),
    )


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


def _gateway_request_from_row(row: sqlite3.Row) -> GatewayRequestRecord:
    raw = json.loads(row["evidence"] or "[]")
    items = tuple(
        EvidenceItem(
            kind=EvidenceKind(item["kind"]),
            text=item["text"],
            content_hash=item["content_hash"],
            untrusted=True,
        )
        for item in raw
    )
    return GatewayRequestRecord(
        id=row["id"],
        correlation_id=row["correlation_id"],
        active_matter_id=row["active_matter_id"],
        channel=row["channel"],
        principal=row["principal"],
        external_session_ref=row["external_session_ref"],
        external_request_ref=row["external_request_ref"],
        created_at=row["created_at"],
        evidence=items,
    )


def _active_matter_from_row(
    row: sqlite3.Row,
    correlations: list[sqlite3.Row],
    sessions: list[sqlite3.Row],
) -> ActiveMatter:
    return ActiveMatter(
        id=row["id"],
        matter_id=row["matter_id"],
        title=row["title"],
        visibility=HandleVisibility(row["visibility"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        correlation_ids=tuple(item["correlation_id"] for item in correlations),
        sessions=tuple(
            ActiveSession(
                channel=item["channel"],
                external_session_ref=item["external_session_ref"],
                first_seen_at=item["first_seen_at"],
                last_seen_at=item["last_seen_at"],
            )
            for item in sessions
        ),
    )
