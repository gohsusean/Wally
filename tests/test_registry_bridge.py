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


def test_authoritative_finance_role_cannot_be_downgraded_by_override(tmp_path):
    registry = KnowledgeRegistry(tmp_path / "registry.db")
    registry.import_approved(
        database_id="db-1",
        name="Accounts",
        classification=KnowledgeClass.OPERATIONAL,
        title_property="Name",
        role="finance",
        approved_by="owner",
    )
    notion = NotionPlatformConfig(
        defaults=NotionDefaults(),
        exclude_ids=(),
        overrides={"db1": NotionSchemaOverride(role="general")},
        legacy_databases=(),
    )
    configs = databases_from_registry(registry, notion)
    assert configs[0].role == "finance"


def test_cached_adapter_write_target_reads_current_finance_designation(tmp_path):
    from wally.adapters.notion.adapter import NotionKnowledgeAdapter
    from wally.config.loader import NotionDatabaseConfig
    from wally.runtime.finance_safety import evaluate_bill_paid_write_policy

    registry = KnowledgeRegistry(tmp_path / "registry.db")
    registry.import_approved(
        database_id="db-1",
        name="Accounts",
        classification=KnowledgeClass.OPERATIONAL,
        title_property="Name",
        role="general",
        approved_by="owner",
    )
    adapter = NotionKnowledgeAdapter(
        api_key="fixture-not-live",
        registry=registry,
        databases=[
            NotionDatabaseConfig(
                name="accounts", id="db-1", role="general", readable=True, writable=True
            )
        ],
    )
    registry.set_finance_role("db-1")  # Changed by another running owner process.
    adapter._request = lambda *a, **kw: {"id": "page-1", "parent": {"database_id": "db-1"}}
    try:
        target = adapter.resolve_write_target("knowledge_update", {"asset_id": "page-1"})
        assert target.role == "finance"
        policy = evaluate_bill_paid_write_policy(
            adapter,
            "knowledge_update",
            {"asset_id": "page-1", "title": "harmless edit"},
            finance_bills_role="finance",
        )
        assert not policy.allowed
        registry.approve("db-1", KnowledgeClass.GOVERNANCE, approved_by="owner")
        assert not adapter.resolve_write_target("knowledge_update", {"asset_id": "page-1"}).writable
    finally:
        adapter.close()
