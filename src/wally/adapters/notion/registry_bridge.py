"""Build NotionDatabaseConfig entries from the knowledge registry."""

from __future__ import annotations

from wally.config.loader import NotionDatabaseConfig, NotionPlatformConfig, NotionSchemaOverride
from wally.knowledge.registry import DatabaseRecord, KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass


def _normalize_id(value: str) -> str:
    return value.replace("-", "")


def _apply_override(
    record: DatabaseRecord,
    override: NotionSchemaOverride | None,
    defaults_type_property: str,
) -> NotionDatabaseConfig:
    classification = record.classification
    writable = classification == KnowledgeClass.OPERATIONAL
    return NotionDatabaseConfig(
        name=record.registry_key,
        id=record.database_id,
        role=(override.role if override and override.role else record.role),
        readable=True,
        writable=writable,
        knowledge_class=classification,
        title_property=(
            override.title_property
            if override and override.title_property
            else record.title_property
        ),
        content_property=(
            override.content_property
            if override and override.content_property is not None
            else record.content_property
        ),
        type_property=(
            override.type_property
            if override and override.type_property
            else record.type_property or defaults_type_property
        ),
    )


def databases_from_registry(
    registry: KnowledgeRegistry,
    notion: NotionPlatformConfig,
) -> list[NotionDatabaseConfig]:
    """Convert registry records to adapter configs, applying platform overrides."""
    configs: list[NotionDatabaseConfig] = []
    for record in registry.list_all():
        if _normalize_id(record.database_id) in notion.exclude_ids:
            continue
        override = notion.overrides.get(_normalize_id(record.database_id))
        configs.append(
            _apply_override(record, override, notion.defaults.type_property)
        )
    return configs
