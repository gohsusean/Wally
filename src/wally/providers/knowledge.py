"""Knowledge provider protocol."""

from __future__ import annotations

from typing import Any, Protocol

from wally.config.loader import NotionDatabaseConfig
from wally.models.actions import PlannedAction
from wally.models.knowledge import KnowledgeAsset, KnowledgeRetrievalResult
from wally.providers.capability import CapabilityProvider


class KnowledgeProvider(CapabilityProvider, Protocol):
    """Abstract interface for personal knowledge retrieval and storage."""

    @property
    def name(self) -> str:
        """Capability identifier (not adapter name)."""
        ...

    def is_healthy(self) -> bool:
        """Return True if configured and reachable."""
        ...

    def refresh(self) -> None:
        """Reload configuration after registry changes."""
        ...

    def tool_definitions(self) -> list[dict[str, Any]]:
        """OpenAI-compatible tool schemas exposed to the LLM."""
        ...

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return a string result for the LLM."""
        ...

    def resolve_write_target(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> NotionDatabaseConfig | None:
        """Return the database that would be written for a write tool, or None for reads."""
        ...

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        """Return the planned action for safety classification."""
        ...

    def retrieve(
        self, query: str, *, role: str | None = None, limit: int = 10
    ) -> KnowledgeRetrievalResult:
        """Retrieve knowledge assets matching the query."""
        ...

    def get(self, asset_id: str) -> KnowledgeAsset:
        """Retrieve a single knowledge asset by ID."""
        ...

    def create(
        self,
        title: str,
        content: str,
        *,
        role: str = "general",
        database: str | None = None,
    ) -> KnowledgeAsset:
        """Create a new knowledge asset."""
        ...

    def update(
        self,
        asset_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
    ) -> KnowledgeAsset:
        """Update an existing knowledge asset."""
        ...

    def archive(self, asset_id: str) -> None:
        """Archive a knowledge asset."""
        ...
