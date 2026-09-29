"""Mock knowledge provider for tests."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from wally.config.loader import NotionDatabaseConfig
from wally.models.actions import ActionClass, PlannedAction
from wally.models.knowledge import (
    KnowledgeAsset,
    KnowledgeClass,
    KnowledgeRetrievalResult,
)


class MockKnowledgeProvider:
    name = "knowledge"

    def __init__(self) -> None:
        self._assets: dict[str, KnowledgeAsset] = {}
        self._databases = {
            "operations": NotionDatabaseConfig(
                name="operations",
                id="db-ops",
                role="general",
                readable=True,
                writable=True,
                knowledge_class=KnowledgeClass.OPERATIONAL,
            ),
            "governance": NotionDatabaseConfig(
                name="governance",
                id="db-gov",
                role="governance",
                readable=True,
                writable=False,
                knowledge_class=KnowledgeClass.GOVERNANCE,
            ),
            "pending": NotionDatabaseConfig(
                name="pending",
                id="db-pending",
                role="general",
                readable=True,
                writable=False,
                knowledge_class=KnowledgeClass.PENDING,
            ),
        }

    def refresh(self) -> None:
        return

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        mapping = {
            "knowledge_retrieve": ("retrieve", ActionClass.READ),
            "knowledge_get": ("get", ActionClass.READ),
            "knowledge_create": ("create", ActionClass.REVERSIBLE),
            "knowledge_update": ("update", ActionClass.REVERSIBLE),
            "knowledge_archive": ("archive", ActionClass.DESTRUCTIVE),
        }
        if tool_name not in mapping:
            return None
        action, action_class = mapping[tool_name]
        return PlannedAction("knowledge", action, arguments, action_class)

    def is_healthy(self) -> bool:
        return True

    def seed(
        self,
        title: str,
        content: str,
        *,
        role: str = "general",
        knowledge_class: KnowledgeClass = KnowledgeClass.OPERATIONAL,
        database: str = "operations",
    ) -> KnowledgeAsset:
        asset = KnowledgeAsset(
            id=str(uuid4()),
            title=title,
            content=content,
            database=database,
            role=role,
            knowledge_class=knowledge_class,
        )
        self._assets[asset.id] = asset
        return asset

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "knowledge_retrieve",
                "description": "Retrieve knowledge",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "type": "function",
                "name": "knowledge_archive",
                "description": "Archive knowledge",
                "parameters": {
                    "type": "object",
                    "properties": {"asset_id": {"type": "string"}},
                    "required": ["asset_id"],
                },
            },
            {
                "type": "function",
                "name": "knowledge_create",
                "description": "Create knowledge",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "database": {"type": "string"},
                    },
                    "required": ["title", "content"],
                },
            },
        ]

    def resolve_write_target(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> NotionDatabaseConfig | None:
        if tool_name == "knowledge_create":
            db_name = arguments.get("database", "operations")
            return self._databases.get(db_name)
        if tool_name in {"knowledge_update", "knowledge_archive"}:
            asset_id = arguments.get("asset_id")
            if asset_id and asset_id in self._assets:
                asset = self._assets[asset_id]
                return self._databases.get(asset.database)
        return None

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "knowledge_retrieve":
            result = self.retrieve(arguments["query"])
            return json.dumps({"assets": [{"id": a.id, "title": a.title} for a in result.assets]})
        if tool_name == "knowledge_archive":
            self.archive(arguments["asset_id"])
            return json.dumps({"status": "archived"})
        if tool_name == "knowledge_create":
            asset = self.create(
                arguments["title"],
                arguments["content"],
                database=arguments.get("database", "operations"),
            )
            return json.dumps({"id": asset.id})
        raise ValueError(tool_name)

    def retrieve(
        self, query: str, *, role: str | None = None, limit: int = 10
    ) -> KnowledgeRetrievalResult:
        q = query.lower()
        assets = [
            a for a in self._assets.values() if q in a.title.lower() or q in a.content.lower()
        ]
        return KnowledgeRetrievalResult(assets=assets[:limit], query=query)

    def get(self, asset_id: str) -> KnowledgeAsset:
        return self._assets[asset_id]

    def create(
        self,
        title: str,
        content: str,
        *,
        role: str = "general",
        database: str | None = None,
    ) -> KnowledgeAsset:
        db_name = database or "operations"
        kc = self._databases[db_name].knowledge_class
        return self.seed(title, content, role=role, knowledge_class=kc, database=db_name)

    def update(
        self,
        asset_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
    ) -> KnowledgeAsset:
        asset = self._assets[asset_id]
        if title:
            asset.title = title
        if content:
            asset.content = content
        return asset

    def archive(self, asset_id: str) -> None:
        del self._assets[asset_id]
