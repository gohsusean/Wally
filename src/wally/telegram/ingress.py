"""Durable Telegram ingress. An update id changes Wally state at most once.

The cursor advances only after the update is stored as processed, ignored,
or cleanly rejected. A crash before that store leaves the offset in place,
so Telegram redelivers the update and the handler runs again. Handlers must
be idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wally.ops.store import OperationsStore

# Longer than one Bot API long poll, so a restart can wait out a dead holder.
LEASE = timedelta(seconds=90)


class TelegramIngress:
    def __init__(self, store: OperationsStore) -> None:
        self._store = store

    def seen(self, update_id: int) -> bool:
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT 1 FROM telegram_updates WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return row is not None

    def record(self, update_id: int, outcome: str, now: datetime) -> None:
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                INSERT OR IGNORE INTO telegram_updates (update_id, outcome, created_at)
                VALUES (?, ?, ?)
                """,
                (update_id, outcome, _iso(now)),
            )

    def advance(self, update_id: int) -> None:
        """Move the long-poll offset only after ``record`` has stored this id."""
        if not self.seen(update_id):
            return
        nxt = update_id + 1
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT next_offset FROM telegram_cursor WHERE id = 'poll'"
            ).fetchone()
            current = int(row["next_offset"]) if row else 0
            if nxt <= current:
                return
            conn.execute(
                """
                INSERT INTO telegram_cursor (id, next_offset, chat_id)
                VALUES ('poll', ?, '')
                ON CONFLICT(id) DO UPDATE SET
                    next_offset = MAX(telegram_cursor.next_offset, excluded.next_offset)
                """,
                (nxt,),
            )

    def offset(self) -> int:
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT next_offset FROM telegram_cursor WHERE id = 'poll'"
            ).fetchone()
        return int(row["next_offset"]) if row else 0

    def remember_chat(self, chat_id: str) -> None:
        if not chat_id:
            return
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                INSERT INTO telegram_cursor (id, next_offset, chat_id)
                VALUES ('poll', 0, ?)
                ON CONFLICT(id) DO UPDATE SET chat_id = excluded.chat_id
                """,
                (chat_id,),
            )

    def chat_id(self) -> str:
        with self._store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT chat_id FROM telegram_cursor WHERE id = 'poll'"
            ).fetchone()
        return str(row["chat_id"]) if row else ""

    def try_acquire(self, holder: str, now: datetime) -> bool:
        until = _iso(now + LEASE)
        stamp = _iso(now)
        with self._store._connect() as conn:  # noqa: SLF001
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT holder, locked_until FROM telegram_lease WHERE id = 'poll'"
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO telegram_lease (id, holder, locked_until)
                    VALUES ('poll', ?, ?)
                    """,
                    (holder, until),
                )
                conn.execute("COMMIT")
                return True
            if row["holder"] != holder and row["locked_until"] > stamp:
                conn.execute("COMMIT")
                return False
            conn.execute(
                """
                UPDATE telegram_lease SET holder = ?, locked_until = ? WHERE id = 'poll'
                """,
                (holder, until),
            )
            conn.execute("COMMIT")
            return True

    def renew(self, holder: str, now: datetime) -> bool:
        """An expired/lost holder cannot revive its lease from a delayed operation."""
        with self._store._connect() as conn:  # noqa: SLF001
            result = conn.execute(
                "UPDATE telegram_lease SET locked_until=? WHERE id='poll' AND holder=? "
                "AND locked_until>?", (_iso(now + LEASE), holder, _iso(now)),
            )
            return result.rowcount == 1

    def owns(self, holder: str, now: datetime) -> bool:
        with self._store._connect() as conn:  # noqa: SLF001
            return conn.execute(
                "SELECT 1 FROM telegram_lease WHERE id='poll' AND holder=? AND locked_until>?",
                (holder, _iso(now)),
            ).fetchone() is not None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
