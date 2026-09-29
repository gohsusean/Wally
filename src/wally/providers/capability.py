"""Shared capability provider protocol."""

from __future__ import annotations

from typing import Any, Protocol

from wally.models.actions import PlannedAction


class CapabilityProvider(Protocol):
    """A provider that exposes LLM-callable tools."""

    @property
    def name(self) -> str:
        """Capability identifier (e.g. knowledge, workflow)."""
        ...

    def is_healthy(self) -> bool:
        """Return True if configured and reachable enough to operate."""
        ...

    def tool_definitions(self) -> list[dict[str, Any]]:
        """OpenAI-compatible tool schemas."""
        ...

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call; return a string for the LLM."""
        ...

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        """Return the planned action for safety classification, if applicable."""
        ...
