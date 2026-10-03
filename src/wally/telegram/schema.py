"""Telegram tables. Additive. Existing operations rows stay put."""

from __future__ import annotations

import sqlite3


def ensure_telegram_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id TEXT PRIMARY KEY,
            dedupe_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            source_entity_id TEXT NOT NULL DEFAULT '',
            active_matter_id TEXT NOT NULL DEFAULT '',
            correlation_id TEXT NOT NULL DEFAULT '',
            proposal_id TEXT NOT NULL DEFAULT '',
            execution_id TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            callback_nonce TEXT NOT NULL DEFAULT '',
            allowed_actions TEXT NOT NULL DEFAULT '',
            owner_user_id TEXT NOT NULL DEFAULT '',
            chat_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            locked_at TEXT NOT NULL DEFAULT '',
            retry_after TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            telegram_message_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            delivered_at TEXT NOT NULL DEFAULT '',
            dismissed_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_nonce
        ON notification_outbox (callback_nonce)
        WHERE callback_nonce != ''
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_updates (
            update_id INTEGER PRIMARY KEY,
            outcome TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_cursor (
            id TEXT PRIMARY KEY CHECK (id = 'poll'),
            next_offset INTEGER NOT NULL,
            chat_id TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_lease (
            id TEXT PRIMARY KEY CHECK (id = 'poll'),
            holder TEXT NOT NULL,
            locked_until TEXT NOT NULL
        )
        """
    )
