"""Discover Notion databases shared with the integration."""

from __future__ import annotations

from typing import Any

import httpx

from wally.knowledge.registry import DiscoveredDatabase

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

TITLE_TYPES = {"title"}
CONTENT_TYPES = {"rich_text", "text"}


def _db_title(db: dict[str, Any]) -> str:
    title_parts = db.get("title", [])
    return "".join(part.get("plain_text", "") for part in title_parts) or "(untitled)"


def _property_names(properties: dict[str, Any], types: set[str]) -> list[str]:
    return [name for name, meta in properties.items() if meta.get("type") in types]


def _infer_schema(properties: dict[str, Any]) -> tuple[str, str | None]:
    titles = _property_names(properties, TITLE_TYPES)
    content = _property_names(properties, CONTENT_TYPES)
    title_property = titles[0] if titles else "Name"
    content_property = content[0] if content else None
    return title_property, content_property


def _extract_description(db: dict[str, Any]) -> str:
    description = db.get("description", [])
    return "".join(part.get("plain_text", "") for part in description).strip()


def discover_databases(client: httpx.Client) -> list[DiscoveredDatabase]:
    """List databases shared with the integration and infer schema."""
    response = client.post(
        "/search",
        json={
            "filter": {"property": "object", "value": "database"},
            "page_size": 100,
        },
    )
    response.raise_for_status()

    discovered: list[DiscoveredDatabase] = []
    for summary in response.json().get("results", []):
        db_id = summary["id"]
        detail = client.get(f"/databases/{db_id}")
        detail.raise_for_status()
        db = detail.json()
        properties = db.get("properties", {})
        title_property, content_property = _infer_schema(properties)
        discovered.append(
            DiscoveredDatabase(
                database_id=db_id,
                name=_db_title(db),
                title_property=title_property,
                content_property=content_property,
                description=_extract_description(db),
            )
        )
    return discovered


def create_notion_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=NOTION_API,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )
