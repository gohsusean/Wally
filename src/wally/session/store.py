"""Persist conversation sessions in SQLite with FTS recall and summaries."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from wally.models.conversation import ConversationHit, SessionSummary
from wally.models.messages import Message, Role, Session


class SessionStore:
    """Store and retrieve conversation sessions."""

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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    covers_message_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content,
                    session_id UNINDEXED,
                    role UNINDEXED,
                    message_id UNINDEXED,
                    tokenize='porter unicode61'
                )
                """
            )
            self._backfill_fts(conn)

    def _backfill_fts(self, conn: sqlite3.Connection) -> None:
        fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        if fts_count:
            return
        rows = conn.execute(
            "SELECT id, session_id, role, content FROM messages ORDER BY id"
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO messages_fts (content, session_id, role, message_id)
                VALUES (?, ?, ?, ?)
                """,
                (row["content"], row["session_id"], row["role"], row["id"]),
            )

    def create_session(self) -> Session:
        session = Session()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, created_at) VALUES (?, ?)",
                (session.id, session.created_at.isoformat()),
            )
        return session

    def get_session(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, created_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            messages = conn.execute(
                """
                SELECT role, content, created_at FROM messages
                WHERE session_id = ? ORDER BY id
                """,
                (session_id,),
            ).fetchall()
            summary_row = conn.execute(
                """
                SELECT summary, covers_message_count FROM session_summaries
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        session = Session(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        session.messages = [
            Message(
                role=Role(message["role"]),
                content=message["content"],
                created_at=datetime.fromisoformat(message["created_at"]),
            )
            for message in messages
        ]
        if summary_row is not None:
            session.summary = summary_row["summary"]
            session.summary_covers_through = int(summary_row["covers_message_count"])
        return session

    def list_sessions(self, limit: int = 20) -> list[Session]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            Session(id=row["id"], created_at=datetime.fromisoformat(row["created_at"]))
            for row in rows
        ]

    def add_message(self, session_id: str, message: Message) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    message.role.value,
                    message.content,
                    message.created_at.isoformat(),
                ),
            )
            message_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO messages_fts (content, session_id, role, message_id)
                VALUES (?, ?, ?, ?)
                """,
                (message.content, session_id, message.role.value, message_id),
            )
        return message_id

    def message_count(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["count"])

    def get_summary(self, session_id: str) -> SessionSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, summary, covers_message_count, updated_at
                FROM session_summaries WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionSummary(
            session_id=row["session_id"],
            summary=row["summary"],
            covers_message_count=int(row["covers_message_count"]),
            updated_at=row["updated_at"],
        )

    def save_summary(
        self, session_id: str, summary: str, *, covers_message_count: int
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_summaries (
                    session_id, summary, covers_message_count, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    covers_message_count = excluded.covers_message_count,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    summary,
                    covers_message_count,
                    datetime.now().isoformat(),
                ),
            )

    def search_messages(
        self,
        query: str,
        *,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[ConversationHit]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT
                messages_fts.message_id,
                messages_fts.session_id,
                messages_fts.role,
                messages.content,
                snippet(messages_fts, 0, '', '', '…', 48) AS snippet,
                sessions.created_at AS session_created_at
            FROM messages_fts
            JOIN messages ON messages.id = messages_fts.message_id
            JOIN sessions ON sessions.id = messages_fts.session_id
            WHERE messages_fts MATCH ?
        """
        params: list[object] = [fts_query]
        if exclude_session_id:
            sql += " AND messages_fts.session_id != ?"
            params.append(exclude_session_id)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            ConversationHit(
                session_id=row["session_id"],
                message_id=int(row["message_id"]),
                role=row["role"],
                content=row["content"],
                snippet=row["snippet"],
                session_created_at=row["session_created_at"],
            )
            for row in rows
        ]

    def list_recent_messages(
        self,
        *,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[ConversationHit]:
        """Return the most recent messages, optionally excluding the active session."""
        sql = """
            SELECT
                messages.id AS message_id,
                messages.session_id,
                messages.role,
                messages.content,
                messages.content AS snippet,
                sessions.created_at AS session_created_at
            FROM messages
            JOIN sessions ON sessions.id = messages.session_id
        """
        params: list[object] = []
        if exclude_session_id:
            sql += " WHERE messages.session_id != ?"
            params.append(exclude_session_id)
        sql += " ORDER BY messages.id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        hits = [
            ConversationHit(
                session_id=row["session_id"],
                message_id=int(row["message_id"]),
                role=row["role"],
                content=row["content"],
                snippet=row["snippet"][:120] if row["snippet"] else "",
                session_created_at=row["session_created_at"],
            )
            for row in rows
        ]
        hits.reverse()
        return hits

    def export_session(self, session_id: str) -> str:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        payload = {
            "id": session.id,
            "created_at": session.created_at.isoformat(),
            "summary": session.summary,
            "messages": [
                {
                    "role": message.role.value,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
                for message in session.messages
            ],
        }
        return json.dumps(payload, indent=2)

    def resume_or_create(self, session_id: str | None) -> Session:
        if session_id:
            existing = self.get_session(session_id)
            if existing is not None:
                return existing
        return self.create_session()


def _fts_query(query: str) -> str:
    """Build a safe FTS5 query from user/LLM input (OR across terms)."""
    terms = [part for part in query.replace('"', " ").split() if part.strip()]
    if not terms:
        return ""
    if len(terms) == 1:
        return f'"{terms[0]}"'
    return " OR ".join(f'"{term}"' for term in terms)
