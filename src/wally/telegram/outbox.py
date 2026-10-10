"""At-least-once Telegram delivery with best-effort duplicate suppression.

One outbox row exists per dedupe key. A row that is durably delivered is not
sent again. A row left in ``sending`` because the process died after Telegram
accepted the message, and before the message id was stored, can be retried.
That retry may show a second card. It must not record a second decision.

This is not exactly-once network delivery.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wally.ops.store import OperationsStore

OUTBOUND_SEMANTICS = (
    "at-least-once delivery with best-effort duplicate suppression"
)
SEND_LEASE = timedelta(seconds=30)
MAX_ATTEMPTS = 5
_DECISION_ACTIONS = "approve,reject,not_now"

_COLUMNS = (
    "id",
    "dedupe_key",
    "kind",
    "source_entity_id",
    "active_matter_id",
    "correlation_id",
    "proposal_id",
    "execution_id",
    "fingerprint",
    "callback_nonce",
    "allowed_actions",
    "owner_user_id",
    "chat_id",
    "status",
    "attempt_count",
    "locked_at",
    "retry_after",
    "last_error",
    "telegram_message_id",
    "created_at",
    "delivered_at",
    "dismissed_at",
)


@dataclass
class Notification:
    id: str
    dedupe_key: str
    kind: str
    source_entity_id: str
    active_matter_id: str
    correlation_id: str
    proposal_id: str
    execution_id: str
    fingerprint: str
    callback_nonce: str
    allowed_actions: str
    owner_user_id: str
    chat_id: str
    status: str
    attempt_count: int
    locked_at: str
    retry_after: str
    last_error: str
    telegram_message_id: str
    created_at: str
    delivered_at: str
    dismissed_at: str

    def allows(self, action: str) -> bool:
        return action in {part for part in self.allowed_actions.split(",") if part}


class NotificationOutbox:
    def __init__(self, store: OperationsStore) -> None:
        self._store = store

    def enqueue_decision(
        self,
        *,
        proposal_id: str,
        fingerprint: str,
        owner_user_id: str,
        chat_id: str,
        active_matter_id: str = "",
        correlation_id: str = "",
        now: datetime,
        kind: str = "decision_required",
        allowed_actions: str = _DECISION_ACTIONS,
        generation: str = "",
    ) -> Notification:
        key = f"{kind}:{proposal_id}:{fingerprint}:{generation}" if generation else (
            f"{kind}:{proposal_id}:{fingerprint}"
        )
        existing = self._by_dedupe(key)
        if existing is not None:
            if chat_id and not existing.chat_id and existing.status == "pending":
                self._set_chat(existing.id, chat_id, owner_user_id)
                return self._must(existing.id)
            return existing
        row = Notification(
            id=f"ntf_{uuid4().hex[:16]}",
            dedupe_key=key,
            kind=kind,
            source_entity_id=proposal_id,
            active_matter_id=active_matter_id,
            correlation_id=correlation_id,
            proposal_id=proposal_id,
            execution_id="",
            fingerprint=fingerprint,
            callback_nonce=secrets.token_urlsafe(9),
            allowed_actions=allowed_actions,
            owner_user_id=owner_user_id,
            chat_id=chat_id,
            status="pending",
            attempt_count=0,
            locked_at="",
            retry_after="",
            last_error="",
            telegram_message_id="",
            created_at=_iso(now),
            delivered_at="",
            dismissed_at="",
        )
        self._insert(row)
        return self._by_dedupe(key)

    def enqueue_status(
        self,
        *,
        kind: str,
        execution_id: str,
        proposal_id: str,
        fingerprint: str,
        owner_user_id: str,
        chat_id: str,
        active_matter_id: str = "",
        correlation_id: str = "",
        now: datetime,
    ) -> Notification:
        key = f"{kind}:{execution_id}:{fingerprint}"
        existing = self._by_dedupe(key)
        if existing is not None:
            if chat_id and not existing.chat_id and existing.status == "pending":
                self._set_chat(existing.id, chat_id, owner_user_id)
                return self._must(existing.id)
            return existing
        row = Notification(
            id=f"ntf_{uuid4().hex[:16]}",
            dedupe_key=key,
            kind=kind,
            source_entity_id=execution_id,
            active_matter_id=active_matter_id,
            correlation_id=correlation_id,
            proposal_id=proposal_id,
            execution_id=execution_id,
            fingerprint=fingerprint,
            callback_nonce="",
            allowed_actions="",
            owner_user_id=owner_user_id,
            chat_id=chat_id,
            status="pending",
            attempt_count=0,
            locked_at="",
            retry_after="",
            last_error="",
            telegram_message_id="",
            created_at=_iso(now),
            delivered_at="",
            dismissed_at="",
        )
        self._insert(row)
        return self._by_dedupe(key)

    def get(self, notification_id: str) -> Notification | None:
        return self._one("id", notification_id)

    def by_nonce(self, nonce: str) -> Notification | None:
        if not nonce:
            return None
        return self._one("callback_nonce", nonce)

    def latest_decision(self, chat_id: str) -> Notification | None:
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                """
                SELECT * FROM notification_outbox
                WHERE kind = 'decision_required' AND chat_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        return _notification(row) if row else None

    def recover_stale(self, now: datetime) -> int:
        cutoff = _iso(now - SEND_LEASE)
        with self._store._connect() as conn:  # noqa: SLF001
            cursor = conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending', locked_at = ''
                WHERE status = 'sending' AND locked_at != '' AND locked_at <= ?
                """,
                (cutoff,),
            )
            return cursor.rowcount

    def claim(self, now: datetime, notification_id: str = "") -> Notification | None:
        stamp = _iso(now)
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute("BEGIN IMMEDIATE")
            if notification_id:
                row = conn.execute(
                    """
                    SELECT * FROM notification_outbox
                    WHERE id = ? AND status = 'pending' AND chat_id != ''
                      AND (retry_after = '' OR retry_after <= ?)
                    """,
                    (notification_id, stamp),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM notification_outbox
                    WHERE status = 'pending' AND chat_id != ''
                      AND (retry_after = '' OR retry_after <= ?)
                    ORDER BY created_at, id
                    LIMIT 1
                    """,
                    (stamp,),
                ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            claim_id = row["id"]
            updated = conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'sending',
                    attempt_count = attempt_count + 1,
                    locked_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (stamp, claim_id),
            )
            conn.execute("COMMIT")
            if updated.rowcount != 1:
                return None
        return self._must(claim_id)

    def mark_delivered(self, notification_id: str, message_id: str, now: datetime) -> None:
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'delivered',
                    telegram_message_id = ?,
                    delivered_at = ?,
                    locked_at = '',
                    last_error = ''
                WHERE id = ? AND status = 'sending'
                """,
                (message_id, _iso(now), notification_id),
            )

    def mark_retry(self, row: Notification, error: str, now: datetime) -> None:
        attempt = row.attempt_count
        status = "failed" if attempt >= MAX_ATTEMPTS else "pending"
        delay = min(2**min(attempt, 5), 60)
        retry_after = "" if status == "failed" else _iso(now + timedelta(seconds=delay))
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = ?,
                    locked_at = '',
                    retry_after = ?,
                    last_error = ?
                WHERE id = ? AND status = 'sending'
                """,
                (status, retry_after, error[:160], row.id),
            )

    def dismiss(self, notification_id: str, now: datetime) -> None:
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'dismissed', dismissed_at = ?, locked_at = ''
                WHERE id = ? AND status != 'dismissed'
                """,
                (_iso(now), notification_id),
            )

    def count(self) -> int:
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute("SELECT COUNT(*) AS n FROM notification_outbox").fetchone()
        return int(row["n"])

    def _by_dedupe(self, key: str) -> Notification | None:
        return self._one("dedupe_key", key)

    def _one(self, column: str, value: str) -> Notification | None:
        if column not in {"id", "dedupe_key", "callback_nonce"}:
            raise ValueError(column)
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                f"SELECT * FROM notification_outbox WHERE {column} = ?",
                (value,),
            ).fetchone()
        return _notification(row) if row else None

    def _must(self, notification_id: str) -> Notification:
        found = self.get(notification_id)
        if found is None:
            raise RuntimeError("Notification disappeared.")
        return found

    def _set_chat(self, notification_id: str, chat_id: str, owner_user_id: str) -> None:
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                UPDATE notification_outbox
                SET chat_id = ?, owner_user_id = ?
                WHERE id = ? AND chat_id = ''
                """,
                (chat_id, owner_user_id, notification_id),
            )

    def _insert(self, row: Notification) -> None:
        values = tuple(getattr(row, column) for column in _COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        names = ", ".join(_COLUMNS)
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                f"INSERT OR IGNORE INTO notification_outbox ({names}) VALUES ({placeholders})",
                values,
            )


def _notification(row: sqlite3.Row) -> Notification:
    return Notification(**{column: row[column] for column in _COLUMNS})


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
