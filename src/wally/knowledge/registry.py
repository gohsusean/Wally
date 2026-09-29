"""SQLite registry for approved Notion database classifications."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from wally.knowledge.classification import ClassificationRecommendation, recommend_classification
from wally.models.knowledge import KnowledgeClass


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "database"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DatabaseRecord:
    database_id: str
    registry_key: str
    name: str
    classification: KnowledgeClass
    title_property: str
    content_property: str | None
    type_property: str | None
    role: str
    first_seen: datetime
    approved_by: str | None
    approved_at: datetime | None
    recommendation: ClassificationRecommendation | None

    @property
    def is_approved(self) -> bool:
        return self.classification != KnowledgeClass.PENDING


@dataclass(frozen=True)
class DiscoveredDatabase:
    database_id: str
    name: str
    title_property: str
    content_property: str | None
    description: str = ""


class KnowledgeRegistry:
    """Authoritative store for database classifications used by runtime policy."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_databases (
                    database_id TEXT PRIMARY KEY,
                    registry_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    title_property TEXT NOT NULL,
                    content_property TEXT,
                    type_property TEXT,
                    role TEXT NOT NULL DEFAULT 'general',
                    first_seen TEXT NOT NULL,
                    approved_by TEXT,
                    approved_at TEXT,
                    recommendation TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_databases_key
                    ON knowledge_databases(registry_key);
                CREATE INDEX IF NOT EXISTS idx_knowledge_databases_classification
                    ON knowledge_databases(classification);
                """
            )

    def list_all(self) -> list[DatabaseRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_databases ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_pending(self) -> list[DatabaseRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_databases WHERE classification = ? ORDER BY name",
                (KnowledgeClass.PENDING.value,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, database_id: str) -> DatabaseRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_databases WHERE database_id = ?",
                (database_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def find_by_key_or_id(self, key_or_id: str) -> DatabaseRecord | None:
        normalized = key_or_id.replace("-", "")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM knowledge_databases
                WHERE registry_key = ?
                   OR database_id = ?
                   OR REPLACE(database_id, '-', '') = ?
                """,
                (key_or_id, key_or_id, normalized),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def sync_discovered(
        self,
        databases: list[DiscoveredDatabase],
        *,
        default_type_property: str | None = None,
    ) -> tuple[list[DatabaseRecord], list[DatabaseRecord]]:
        """Upsert discovered databases. Returns (new_pending, updated_pending)."""
        new_pending: list[DatabaseRecord] = []
        updated_pending: list[DatabaseRecord] = []
        used_keys = {record.registry_key for record in self.list_all()}

        for discovered in databases:
            existing = self.get(discovered.database_id)
            registry_key = _slug(discovered.name)
            if registry_key in used_keys and (
                existing is None or existing.registry_key != registry_key
            ):
                suffix = 2
                candidate = f"{registry_key}_{suffix}"
                while candidate in used_keys:
                    suffix += 1
                    candidate = f"{registry_key}_{suffix}"
                registry_key = candidate

            if existing is None:
                recommendation = recommend_classification(
                    name=discovered.name,
                    description=discovered.description,
                )
                record = self._insert_pending(
                    discovered=discovered,
                    registry_key=registry_key,
                    type_property=default_type_property,
                    recommendation=recommendation,
                )
                used_keys.add(registry_key)
                new_pending.append(record)
                continue

            used_keys.add(existing.registry_key)
            if existing.classification == KnowledgeClass.PENDING:
                recommendation = recommend_classification(
                    name=discovered.name,
                    description=discovered.description,
                )
                record = self._update_pending_metadata(
                    existing,
                    discovered=discovered,
                    recommendation=recommendation,
                    type_property=default_type_property or existing.type_property,
                )
                updated_pending.append(record)
            else:
                self._update_approved_metadata(existing, discovered, default_type_property)

        return new_pending, updated_pending

    def approve(
        self,
        key_or_id: str,
        classification: KnowledgeClass,
        *,
        approved_by: str,
    ) -> DatabaseRecord:
        if classification == KnowledgeClass.PENDING:
            raise ValueError("Cannot approve to pending classification")

        record = self.find_by_key_or_id(key_or_id)
        if record is None:
            raise ValueError(f"Database not found: {key_or_id}")

        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_databases
                SET classification = ?, approved_by = ?, approved_at = ?
                WHERE database_id = ?
                """,
                (classification.value, approved_by, now, record.database_id),
            )
        updated = self.get(record.database_id)
        if updated is None:
            raise RuntimeError("Failed to load database after approval")
        return updated

    def import_approved(
        self,
        *,
        database_id: str,
        name: str,
        classification: KnowledgeClass,
        title_property: str,
        content_property: str | None = None,
        type_property: str | None = None,
        role: str = "general",
        approved_by: str,
        registry_key: str | None = None,
    ) -> DatabaseRecord:
        """Insert a pre-approved record (migration from legacy notion.yaml)."""
        if classification == KnowledgeClass.PENDING:
            raise ValueError("Use sync_discovered for pending databases")

        key = registry_key or _slug(name)
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_databases (
                    database_id, registry_key, name, classification,
                    title_property, content_property, type_property, role,
                    first_seen, approved_by, approved_at, recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(database_id) DO UPDATE SET
                    registry_key = excluded.registry_key,
                    name = excluded.name,
                    classification = excluded.classification,
                    title_property = excluded.title_property,
                    content_property = excluded.content_property,
                    type_property = excluded.type_property,
                    role = excluded.role,
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at
                """,
                (
                    database_id,
                    key,
                    name,
                    classification.value,
                    title_property,
                    content_property,
                    type_property,
                    role,
                    now,
                    approved_by,
                    now,
                ),
            )
        record = self.get(database_id)
        if record is None:
            raise RuntimeError("Failed to load database after import")
        return record

    def _insert_pending(
        self,
        *,
        discovered: DiscoveredDatabase,
        registry_key: str,
        type_property: str | None,
        recommendation: ClassificationRecommendation,
    ) -> DatabaseRecord:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_databases (
                    database_id, registry_key, name, classification,
                    title_property, content_property, type_property, role,
                    first_seen, approved_by, approved_at, recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    discovered.database_id,
                    registry_key,
                    discovered.name,
                    KnowledgeClass.PENDING.value,
                    discovered.title_property,
                    discovered.content_property,
                    type_property,
                    "general",
                    now,
                    recommendation.to_json(),
                ),
            )
        record = self.get(discovered.database_id)
        if record is None:
            raise RuntimeError("Failed to load database after insert")
        return record

    def _update_pending_metadata(
        self,
        existing: DatabaseRecord,
        *,
        discovered: DiscoveredDatabase,
        recommendation: ClassificationRecommendation,
        type_property: str | None,
    ) -> DatabaseRecord:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_databases
                SET name = ?, title_property = ?, content_property = ?,
                    type_property = ?, recommendation = ?
                WHERE database_id = ?
                """,
                (
                    discovered.name,
                    discovered.title_property,
                    discovered.content_property,
                    type_property,
                    recommendation.to_json(),
                    existing.database_id,
                ),
            )
        record = self.get(existing.database_id)
        if record is None:
            raise RuntimeError("Failed to load database after update")
        return record

    def _update_approved_metadata(
        self,
        existing: DatabaseRecord,
        discovered: DiscoveredDatabase,
        default_type_property: str | None,
    ) -> None:
        type_property = existing.type_property or default_type_property
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_databases
                SET name = ?, title_property = ?, content_property = ?, type_property = ?
                WHERE database_id = ?
                """,
                (
                    discovered.name,
                    discovered.title_property,
                    discovered.content_property,
                    type_property,
                    existing.database_id,
                ),
            )

    def _row_to_record(self, row: sqlite3.Row) -> DatabaseRecord:
        return DatabaseRecord(
            database_id=row["database_id"],
            registry_key=row["registry_key"],
            name=row["name"],
            classification=KnowledgeClass(row["classification"]),
            title_property=row["title_property"],
            content_property=row["content_property"],
            type_property=row["type_property"],
            role=row["role"],
            first_seen=datetime.fromisoformat(row["first_seen"]),
            approved_by=row["approved_by"],
            approved_at=(
                datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None
            ),
            recommendation=ClassificationRecommendation.from_json(row["recommendation"]),
        )
