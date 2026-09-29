"""Wire knowledge provider with registry discovery and sync."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wally.adapters.notion.discovery import create_notion_client, discover_databases
from wally.adapters.notion.registry_bridge import databases_from_registry
from wally.config.loader import NotionPlatformConfig, Settings
from wally.exceptions import ProviderUnavailableError
from wally.knowledge.registry import KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass

if TYPE_CHECKING:
    from wally.adapters.notion.adapter import NotionKnowledgeAdapter


def bootstrap_registry(registry: KnowledgeRegistry, notion: NotionPlatformConfig) -> int:
    """Import legacy notion.yaml databases into an empty registry. Returns count imported."""
    if registry.list_all():
        return 0
    if not notion.legacy_databases:
        return 0

    for legacy in notion.legacy_databases:
        classification = legacy.knowledge_class
        if classification == KnowledgeClass.PENDING:
            classification = KnowledgeClass.OPERATIONAL
        registry.import_approved(
            database_id=legacy.id,
            name=legacy.name,
            classification=classification,
            title_property=legacy.title_property,
            content_property=legacy.content_property,
            type_property=legacy.type_property or notion.defaults.type_property,
            role=legacy.role,
            approved_by="migration:notion_yaml",
            registry_key=legacy.name,
        )
    return len(notion.legacy_databases)


def sync_registry_from_notion(
    registry: KnowledgeRegistry,
    *,
    api_key: str,
    notion: NotionPlatformConfig,
) -> tuple[list, list]:
    """Discover Notion databases and sync into the registry."""
    client = create_notion_client(api_key)
    try:
        discovered = discover_databases(client)
    finally:
        client.close()

    return registry.sync_discovered(
        discovered,
        default_type_property=notion.defaults.type_property,
    )


def build_knowledge_stack(settings: Settings) -> tuple[KnowledgeRegistry, NotionKnowledgeAdapter]:
    from wally.adapters.notion.adapter import NotionKnowledgeAdapter

    if not settings.knowledge_enabled:
        raise ProviderUnavailableError("knowledge", "Knowledge is disabled.")
    if settings.knowledge_adapter != "notion":
        raise ProviderUnavailableError(
            "knowledge", f"Unsupported adapter: {settings.knowledge_adapter}"
        )
    if not settings.notion_api_key:
        raise ProviderUnavailableError("knowledge", "NOTION_API_KEY is not set.")

    registry = KnowledgeRegistry(settings.knowledge_registry_database)
    bootstrap_registry(registry, settings.notion)

    try:
        sync_registry_from_notion(registry, api_key=settings.notion_api_key, notion=settings.notion)
    except Exception as exc:
        if not registry.list_all():
            raise ProviderUnavailableError(
                "knowledge",
                f"Could not discover Notion databases: {exc}",
            ) from exc

    databases = databases_from_registry(registry, settings.notion)
    if not databases:
        raise ProviderUnavailableError(
            "knowledge",
            "No knowledge databases in registry. Share databases with your Notion integration.",
        )

    adapter = NotionKnowledgeAdapter(
        api_key=settings.notion_api_key,
        databases=databases,
        registry=registry,
        notion=settings.notion,
    )
    return registry, adapter
