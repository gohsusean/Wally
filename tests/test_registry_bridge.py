"""Registry bridge tests."""

from __future__ import annotations

from pathlib import Path

from wally.adapters.notion.registry_bridge import databases_from_registry
from wally.config.loader import NotionDefaults, NotionPlatformConfig, NotionSchemaOverride
from wally.knowledge.registry import DiscoveredDatabase, KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass


def test_pending_database_is_readable_not_writable(tmp_path: Path) -> None:
    registry = KnowledgeRegistry(tmp_path / "registry.db")
    registry.sync_discovered(
        [
            DiscoveredDatabase(
                database_id="db-1",
                name="New DB",
                title_property="Name",
                content_property="Notes",
            )
        ]
    )
    notion = NotionPlatformConfig(
        defaults=NotionDefaults(),
        exclude_ids=(),
        overrides={},
        legacy_databases=(),
    )
    configs = databases_from_registry(registry, notion)
    assert len(configs) == 1
    assert configs[0].readable is True
    assert configs[0].writable is False
    assert configs[0].knowledge_class == KnowledgeClass.PENDING


def test_operational_database_is_writable(tmp_path: Path) -> None:
    registry = KnowledgeRegistry(tmp_path / "registry.db")
    registry.sync_discovered(
        [
            DiscoveredDatabase(
                database_id="db-1",
                name="Tasks",
                title_property="Name",
                content_property="Notes",
            )
        ]
    )
    registry.approve("db-1", KnowledgeClass.OPERATIONAL, approved_by="user:test")
    notion = NotionPlatformConfig(
        defaults=NotionDefaults(),
        exclude_ids=(),
        overrides={
            "db1": NotionSchemaOverride(title_property="Task Name"),
        },
        legacy_databases=(),
    )
    configs = databases_from_registry(registry, notion)
    assert configs[0].writable is True
    assert configs[0].title_property == "Task Name"
