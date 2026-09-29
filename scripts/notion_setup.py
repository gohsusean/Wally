#!/usr/bin/env python3
"""List Notion databases shared with your integration.

Usage:
    Add NOTION_API_KEY to .env (or export it), then:
    uv run python scripts/notion_setup.py

Copy the suggested YAML into config/notion.yaml.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

NOTION_VERSION = "2022-06-28"

TITLE_TYPES = {"title"}
CONTENT_TYPES = {"rich_text", "text"}


def _project_root() -> Path:
    current = Path(__file__).resolve().parent.parent
    return current


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "database"


def _db_title(db: dict) -> str:
    title_parts = db.get("title", [])
    return "".join(part.get("plain_text", "") for part in title_parts) or "(untitled)"


def _property_names(properties: dict, types: set[str]) -> list[str]:
    return [name for name, meta in properties.items() if meta.get("type") in types]


def _suggest_mapping(properties: dict) -> tuple[str, str | None, str | None]:
    titles = _property_names(properties, TITLE_TYPES)
    content = _property_names(properties, CONTENT_TYPES)
    title_property = titles[0] if titles else "Name"
    content_property = content[0] if content else None
    type_property = next(
        (name for name in properties if name.lower() in {"asset type", "type", "category"}),
        None,
    )
    return title_property, content_property, type_property


def _yaml_snippet(
    key: str,
    db_id: str,
    *,
    title_property: str,
    content_property: str | None,
    type_property: str | None,
    knowledge_class: str = "operational",
    writable: bool = True,
) -> str:
    lines = [
        f"  {key}:",
        f'    id: "{db_id}"',
        "    role: general",
        f"    knowledge_class: {knowledge_class}",
        "    readable: true",
        f"    writable: {str(writable).lower()}",
        f"    title_property: {title_property}",
    ]
    if content_property:
        lines.append(f"    content_property: {content_property}")
    if type_property:
        lines.append(f"    type_property: {type_property}")
    return "\n".join(lines)


def main() -> int:
    root = _project_root()
    load_dotenv(root / ".env", override=False)

    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("Add NOTION_API_KEY to .env (or export it), then run again.", file=sys.stderr)
        return 1

    client = httpx.Client(
        base_url="https://api.notion.com/v1",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_VERSION,
        },
        timeout=30.0,
    )

    response = client.post(
        "/search",
        json={
            "filter": {"property": "object", "value": "database"},
            "page_size": 100,
        },
    )
    if response.status_code != 200:
        print(f"Notion API error: {response.status_code} {response.text}", file=sys.stderr)
        return 1

    results = response.json().get("results", [])
    if not results:
        print("No databases found.")
        print("Share databases with your Notion integration first (⋯ → Connections).")
        return 0

    print("Databases available to your integration:\n")
    snippets: list[str] = []
    used_keys: dict[str, int] = {}

    for db in results:
        title = _db_title(db)
        db_id = db["id"]
        key = _slug(title)
        if key in used_keys:
            used_keys[key] += 1
            key = f"{key}_{used_keys[key]}"
        else:
            used_keys[key] = 1

        detail = client.get(f"/databases/{db_id}")
        properties = detail.json().get("properties", {}) if detail.status_code == 200 else {}
        title_property, content_property, type_property = _suggest_mapping(properties)

        print(f"  {title}")
        print(f"    config key: {key}")
        print(f"    id: {db_id}")
        print(f"    url: {db.get('url', '')}")
        if properties:
            prop_summary = ", ".join(
                f"{name} ({meta.get('type')})" for name, meta in sorted(properties.items())
            )
            print(f"    properties: {prop_summary}")
            print(
                f"  suggested: title_property={title_property!r}"
                + (f", content_property={content_property!r}" if content_property else "")
                + (f", type_property={type_property!r}" if type_property else "")
            )
        print()

        snippets.append(
            _yaml_snippet(
                key,
                db_id,
                title_property=title_property,
                content_property=content_property,
                type_property=type_property,
            )
        )

    print("Paste into config/notion.yaml:\n")
    print("databases:")
    print("\n\n".join(snippets))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
