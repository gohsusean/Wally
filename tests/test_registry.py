"""Knowledge registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wally.knowledge.classification import recommend_classification
from wally.knowledge.registry import DiscoveredDatabase, KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass


@pytest.fixture
def registry(tmp_path: Path) -> KnowledgeRegistry:
    return KnowledgeRegistry(tmp_path / "registry.db")


def test_new_database_enters_pending(registry: KnowledgeRegistry) -> None:
    discovered = [
        DiscoveredDatabase(
            database_id="db-1",
            name="Tasks",
            title_property="Task Name",
            content_property="Notes",
        )
    ]
    new_pending, _ = registry.sync_discovered(discovered, default_type_property="Type")
    assert len(new_pending) == 1
    record = registry.get("db-1")
    assert record is not None
    assert record.classification == KnowledgeClass.PENDING
    assert record.approved_at is None
    assert record.recommendation is not None


def test_approve_makes_operational(registry: KnowledgeRegistry) -> None:
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
    record = registry.approve("db-1", KnowledgeClass.OPERATIONAL, approved_by="user:test")
    assert record.classification == KnowledgeClass.OPERATIONAL
    assert record.approved_by == "user:test"
    assert record.approved_at is not None


def test_approved_classification_not_reset_on_resync(registry: KnowledgeRegistry) -> None:
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
    registry.approve("db-1", KnowledgeClass.GOVERNANCE, approved_by="user:test")
    registry.sync_discovered(
        [
            DiscoveredDatabase(
                database_id="db-1",
                name="Tasks Renamed",
                title_property="Name",
                content_property="Notes",
            )
        ]
    )
    record = registry.get("db-1")
    assert record is not None
    assert record.classification == KnowledgeClass.GOVERNANCE
    assert record.name == "Tasks Renamed"


def test_import_approved(registry: KnowledgeRegistry) -> None:
    registry.import_approved(
        database_id="legacy-1",
        name="Operations",
        classification=KnowledgeClass.OPERATIONAL,
        title_property="Name",
        approved_by="migration:test",
    )
    record = registry.get("legacy-1")
    assert record is not None
    assert record.classification == KnowledgeClass.OPERATIONAL


def test_recommend_governance_for_policy_database() -> None:
    rec = recommend_classification(name="Governance Policies", description="Wally constitution")
    assert rec.classification == KnowledgeClass.GOVERNANCE
    assert rec.signals
