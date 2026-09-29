"""Local SQLite conversation recall adapter."""

from __future__ import annotations

import json
from typing import Any

from wally.models.actions import ActionClass, PlannedAction
from wally.models.conversation import ConversationHit
from wally.runtime.authority import wrap_conversation_results
from wally.session.store import SessionStore


def _hit_payload(hit: ConversationHit) -> dict[str, str]:
    return {
        "session_id": hit.session_id,
        "role": hit.role,
        "snippet": hit.snippet,
        "content": hit.content,
        "session_created_at": hit.session_created_at,
    }


class LocalConversationAdapter:
    """Search prior sessions via SQLite FTS."""

    def __init__(self, *, store: SessionStore, default_limit: int = 10) -> None:
        self._store = store
        self._default_limit = default_limit
        self._exclude_session: str | None = None

    @property
    def name(self) -> str:
        return "conversation"

    def set_exclude_session(self, session_id: str) -> None:
        self._exclude_session = session_id

    def is_healthy(self) -> bool:
        return True

    def search(
        self, query: str, *, limit: int = 10, exclude_session: str | None = None
    ) -> list[ConversationHit]:
        return self._store.search_messages(
            query,
            limit=limit,
            exclude_session_id=exclude_session or self._exclude_session,
        )

    def recent(
        self, *, limit: int = 10, exclude_session: str | None = None
    ) -> list[ConversationHit]:
        return self._store.list_recent_messages(
            limit=limit,
            exclude_session_id=exclude_session or self._exclude_session,
        )

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        if tool_name == "conversation_search":
            return PlannedAction(
                "conversation", "search", arguments, ActionClass.READ
            )
        if tool_name == "conversation_recent":
            return PlannedAction(
                "conversation", "recent", arguments, ActionClass.READ
            )
        return None

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "conversation_recent",
                "description": (
                    "Return the most recent messages from prior Wally sessions. "
                    "Use when the user asks what you last discussed, talked about, "
                    "or worked on — without a specific topic keyword."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum messages to return (default 10)",
                        },
                    },
                },
            },
            {
                "type": "function",
                "name": "conversation_search",
                "description": (
                    "Search prior Wally conversation history by topic or keyword. "
                    "Use when the user names a subject (e.g. Bali, electricity bill). "
                    "Not for Notion knowledge."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search terms (topics, names, decisions)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 10)",
                        },
                    },
                    "required": ["query"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "conversation_recent":
            hits = self.recent(
                limit=int(arguments.get("limit", self._default_limit)),
            )
            return json.dumps(
                wrap_conversation_results([_hit_payload(hit) for hit in hits])
            )
        if tool_name == "conversation_search":
            hits = self.search(
                str(arguments["query"]),
                limit=int(arguments.get("limit", self._default_limit)),
            )
            return json.dumps(
                wrap_conversation_results([_hit_payload(hit) for hit in hits])
            )
        raise ValueError(f"Unknown tool: {tool_name}")


def create_conversation_provider(
    settings, store: SessionStore
) -> LocalConversationAdapter | None:
    if not settings.conversation_enabled:
        return None
    return LocalConversationAdapter(
        store=store,
        default_limit=settings.conversation_search_limit,
    )
