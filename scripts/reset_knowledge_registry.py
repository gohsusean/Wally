#!/usr/bin/env python3
"""Reset the knowledge registry so all databases re-enter pending classification.

Usage:
    uv run python scripts/reset_knowledge_registry.py
"""

from __future__ import annotations

import sys

from wally.config.loader import find_project_root, load_settings


def main() -> int:
    root = find_project_root()
    settings = load_settings(project_root=root)
    path = settings.knowledge_registry_database

    if not path.is_file():
        print(f"No registry at {path}. Nothing to reset.")
        return 0

    path.unlink()
    print(f"Removed {path}")
    print("Next Wally startup will discover databases as pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
