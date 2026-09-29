"""Notion REST API adapter for KnowledgeProvider."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from wally.config.loader import NotionDatabaseConfig, NotionPlatformConfig
from wally.exceptions import ProviderUnavailableError
from wally.knowledge.registry import KnowledgeRegistry
from wally.models.actions import ActionClass, PlannedAction
from wally.models.knowledge import (
    KnowledgeAsset,
    KnowledgeAssetType,
    KnowledgeRetrievalResult,
)

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"


_KNOWLEDGE_TOOL_ACTIONS: dict[str, str] = {
    "knowledge_retrieve": "retrieve",
    "knowledge_get": "get",
    "knowledge_create": "create",
    "knowledge_update": "update",
    "knowledge_archive": "archive",
}

_KNOWLEDGE_ACTION_CLASS: dict[str, ActionClass] = {
    "retrieve": ActionClass.READ,
    "get": ActionClass.READ,
    "create": ActionClass.REVERSIBLE,
    "update": ActionClass.REVERSIBLE,
    "archive": ActionClass.DESTRUCTIVE,
}


class NotionKnowledgeAdapter:
    """Notion implementation of KnowledgeProvider via REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        databases: list[NotionDatabaseConfig],
        registry: KnowledgeRegistry | None = None,
        notion: NotionPlatformConfig | None = None,
    ) -> None:
        self._api_key = api_key
        self._registry = registry
        self._notion = notion
        self._databases = {db.name: db for db in databases}
        self._client = httpx.Client(
            base_url=NOTION_API,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    @property
    def name(self) -> str:
        return "knowledge"

    def refresh(self) -> None:
        """Reload database configs from the registry after classification changes."""
        if self._registry is None or self._notion is None:
            return
        from wally.adapters.notion.registry_bridge import databases_from_registry

        databases = databases_from_registry(self._registry, self._notion)
        self._databases = {db.name: db for db in databases}

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        action = _KNOWLEDGE_TOOL_ACTIONS.get(tool_name)
        if action is None:
            return None
        action_class = _KNOWLEDGE_ACTION_CLASS.get(action, ActionClass.READ)
        return PlannedAction("knowledge", action, arguments, action_class)

    def is_healthy(self) -> bool:
        if not self._api_key or not self._databases:
            return False
        try:
            response = self._client.get("/users/me")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def tool_definitions(self) -> list[dict[str, Any]]:
        roles = sorted({db.role for db in self._databases.values()})
        return [
            {
                "type": "function",
                "name": "knowledge_retrieve",
                "description": "Retrieve relevant knowledge assets for a query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search terms"},
                        "role": {
                            "type": "string",
                            "enum": roles,
                            "description": "Optional database role filter",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "type": "function",
                "name": "knowledge_get",
                "description": "Get a specific knowledge asset by ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string", "description": "Knowledge asset ID"},
                    },
                    "required": ["asset_id"],
                },
            },
            {
                "type": "function",
                "name": "knowledge_create",
                "description": "Create a new operational knowledge asset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "role": {"type": "string", "enum": roles},
                        "database": {
                            "type": "string",
                            "description": "Optional target database name",
                        },
                        "payment_evidence": {
                            "type": "object",
                            "description": (
                                "Required when creating a finance bill record marked paid. "
                                "Types: workflow_success, user_confirmation, verification_provider."
                            ),
                        },
                    },
                    "required": ["title", "content"],
                },
            },
            {
                "type": "function",
                "name": "knowledge_update",
                "description": "Update an existing knowledge asset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "payment_evidence": {
                            "type": "object",
                            "description": (
                                "Required when updating a finance bill to paid status. "
                                "Types: workflow_success, user_confirmation, verification_provider."
                            ),
                        },
                    },
                    "required": ["asset_id"],
                },
            },
            {
                "type": "function",
                "name": "knowledge_archive",
                "description": "Archive a knowledge asset. Requires user approval.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                    },
                    "required": ["asset_id"],
                },
            },
        ]

    def resolve_write_target(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> NotionDatabaseConfig | None:
        if tool_name == "knowledge_create":
            database = arguments.get("database")
            role = arguments.get("role", "general")
            try:
                return self._resolve_writable_database(role=role, name=database)
            except ValueError:
                return None
        if tool_name in {"knowledge_update", "knowledge_archive"}:
            asset_id = arguments.get("asset_id")
            if not asset_id:
                return None
            try:
                page = self._request("GET", f"/pages/{asset_id}")
                return self._database_for_page(page)
            except ProviderUnavailableError:
                return None
        return None

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "knowledge_retrieve": lambda: self.retrieve(
                arguments["query"], role=arguments.get("role")
            ),
            "knowledge_get": lambda: self.get(arguments["asset_id"]),
            "knowledge_create": lambda: self.create(
                arguments["title"],
                arguments["content"],
                role=arguments.get("role", "general"),
                database=arguments.get("database"),
            ),
            "knowledge_update": lambda: self.update(
                arguments["asset_id"],
                title=arguments.get("title"),
                content=arguments.get("content"),
            ),
            "knowledge_archive": lambda: self._archive_and_report(arguments["asset_id"]),
        }
        if tool_name not in handlers:
            raise ValueError(f"Unknown tool: {tool_name}")
        result = handlers[tool_name]()
        if isinstance(result, KnowledgeRetrievalResult):
            return json.dumps({"assets": [_asset_to_dict(a) for a in result.assets]})
        if isinstance(result, KnowledgeAsset):
            return json.dumps(_asset_to_dict(result))
        return json.dumps(result)

    def retrieve(
        self, query: str, *, role: str | None = None, limit: int = 10
    ) -> KnowledgeRetrievalResult:
        if not self._readable_databases(role):
            return KnowledgeRetrievalResult(assets=[], query=query)

        try:
            response = self._request(
                "POST",
                "/search",
                json={
                    "query": query,
                    "page_size": min(limit * 3, 50),
                    "filter": {"value": "page", "property": "object"},
                    "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                },
            )
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(self.name, str(exc)) from exc

        assets: list[KnowledgeAsset] = []
        allowed = {db.id.replace("-", "") for db in self._readable_databases(role)}
        for page in response.get("results", []):
            if page.get("archived"):
                continue
            parent_db = page.get("parent", {}).get("database_id", "")
            if parent_db.replace("-", "") not in allowed:
                continue
            asset = self._page_to_asset(page)
            if asset is not None:
                assets.append(asset)
            if len(assets) >= limit:
                break

        return KnowledgeRetrievalResult(assets=assets, query=query)

    def get(self, asset_id: str) -> KnowledgeAsset:
        page = self._request("GET", f"/pages/{asset_id}")
        asset = self._page_to_asset(page)
        if asset is None:
            raise ProviderUnavailableError(self.name, f"Unknown knowledge asset: {asset_id}")
        return asset

    def create(
        self,
        title: str,
        content: str,
        *,
        role: str = "general",
        database: str | None = None,
    ) -> KnowledgeAsset:
        db = self._resolve_writable_database(role=role, name=database)
        properties = self._build_properties(db, title=title, content=content)
        page = self._request(
            "POST",
            "/pages",
            json={"parent": {"database_id": db.id}, "properties": properties},
        )
        asset = self._page_to_asset(page)
        if asset is None:
            raise ProviderUnavailableError(self.name, "Failed to load created knowledge asset")
        return asset

    def update(
        self,
        asset_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
    ) -> KnowledgeAsset:
        page = self._request("GET", f"/pages/{asset_id}")
        db = self._database_for_page(page)
        if db is None:
            raise ProviderUnavailableError(self.name, f"Unknown knowledge database for {asset_id}")
        properties = self._build_properties(db, title=title, content=content, partial=True)
        updated = self._request("PATCH", f"/pages/{asset_id}", json={"properties": properties})
        asset = self._page_to_asset(updated)
        if asset is None:
            raise ProviderUnavailableError(self.name, f"Failed to load updated asset: {asset_id}")
        return asset

    def archive(self, asset_id: str) -> None:
        self._request("PATCH", f"/pages/{asset_id}", json={"archived": True})

    def _archive_and_report(self, asset_id: str) -> dict[str, str]:
        self.archive(asset_id)
        return {"status": "archived", "asset_id": asset_id}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                self.name, "Could not connect to the knowledge provider. Check your network."
            ) from exc

        if response.status_code in {401, 403}:
            raise ProviderUnavailableError(
                self.name,
                "Authentication failed. Check NOTION_API_KEY and integration access.",
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError(
                self.name, f"Knowledge provider error {response.status_code}: {response.text}"
            )
        return response.json()

    def _readable_databases(self, role: str | None = None) -> list[NotionDatabaseConfig]:
        dbs = [db for db in self._databases.values() if db.readable]
        if role:
            dbs = [db for db in dbs if db.role == role]
        return dbs

    def _resolve_writable_database(
        self, *, role: str, name: str | None
    ) -> NotionDatabaseConfig:
        if name and name in self._databases:
            db = self._databases[name]
            if not db.writable:
                raise ValueError(f"Database {name} is not writable")
            return db
        candidates = [db for db in self._databases.values() if db.writable and db.role == role]
        if not candidates:
            candidates = [db for db in self._databases.values() if db.writable]
        if not candidates:
            raise ValueError("No writable knowledge databases configured")
        return candidates[0]

    def _database_for_page(self, page: dict[str, Any]) -> NotionDatabaseConfig | None:
        parent_db = page.get("parent", {}).get("database_id", "")
        for db in self._databases.values():
            if db.id.replace("-", "") == parent_db.replace("-", ""):
                return db
        return None

    def _build_properties(
        self,
        db: NotionDatabaseConfig,
        *,
        title: str | None = None,
        content: str | None = None,
        partial: bool = False,
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        if title is not None:
            properties[db.title_property] = {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        if content is not None and db.content_property:
            properties[db.content_property] = {
                "rich_text": [{"type": "text", "text": {"content": content}}]
            }
        if not partial and not properties:
            raise ValueError("At least title or content required")
        return properties

    def _page_to_asset(self, page: dict[str, Any]) -> KnowledgeAsset | None:
        db = self._database_for_page(page)
        if db is None:
            return None
        props = page.get("properties", {})
        title = _extract_title(props.get(db.title_property, {}))
        content = ""
        if db.content_property and db.content_property in props:
            content = _extract_rich_text(props[db.content_property])
        asset_type = KnowledgeAssetType.UNSPECIFIED
        if db.type_property and db.type_property in props:
            raw_type = _extract_select(props[db.type_property])
            if raw_type:
                try:
                    asset_type = KnowledgeAssetType(raw_type.lower())
                except ValueError:
                    asset_type = KnowledgeAssetType.UNSPECIFIED
        last_edited = page.get("last_edited_time")
        return KnowledgeAsset(
            id=page["id"],
            title=title,
            content=content,
            database=db.name,
            role=db.role,
            knowledge_class=db.knowledge_class,
            asset_type=asset_type,
            url=page.get("url"),
            last_edited=datetime.fromisoformat(last_edited) if last_edited else None,
        )

    def close(self) -> None:
        self._client.close()


def _extract_title(prop: dict[str, Any]) -> str:
    for item in prop.get("title", []):
        if item.get("plain_text"):
            return item["plain_text"]
    return ""


def _extract_rich_text(prop: dict[str, Any]) -> str:
    parts = [item.get("plain_text", "") for item in prop.get("rich_text", [])]
    return "".join(parts).strip()


def _extract_select(prop: dict[str, Any]) -> str:
    select = prop.get("select")
    if select and select.get("name"):
        return select["name"]
    return ""


def _asset_to_dict(asset: KnowledgeAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "title": asset.title,
        "content": asset.content,
        "database": asset.database,
        "role": asset.role,
        "knowledge_class": asset.knowledge_class.value,
        "asset_type": asset.asset_type.value,
        "url": asset.url,
        "last_edited": asset.last_edited.isoformat() if asset.last_edited else None,
    }


def create_knowledge_provider(settings) -> NotionKnowledgeAdapter | None:
    from wally.adapters.notion.bootstrap import build_knowledge_stack

    if not settings.knowledge_enabled:
        return None
    _, adapter = build_knowledge_stack(settings)
    return adapter
