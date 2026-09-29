#!/usr/bin/env python3
"""Import legacy config/notion.yaml databases into the knowledge registry.

Usage:
    uv run python scripts/migrate_notion_yaml.py
"""

from __future__ import annotations

import sys

from wally.adapters.notion.bootstrap import bootstrap_registry
from wally.config.loader import find_project_root, load_settings
from wally.knowledge.registry import KnowledgeRegistry


def main() -> int:
    root = find_project_root()
    settings = load_settings(project_root=root)
    registry = KnowledgeRegistry(settings.knowledge_registry_database)

    if not settings.notion.legacy_databases:
        print("No legacy databases: block in config/notion.yaml.")
        return 0

    existing = len(registry.list_all())
    if existing:
        print(f"Registry already has {existing} database(s). Skipping import.")
        return 0

    count = bootstrap_registry(registry, settings.notion)
    print(f"Imported {count} database(s) into {registry.path}")
    print("You can remove the legacy databases: block from config/notion.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
