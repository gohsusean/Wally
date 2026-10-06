"""Reviewed runtime configuration, reloaded for certification/execution checks."""

from __future__ import annotations

from pathlib import Path

import yaml

from wally.finance.models import FinanceError, Kind, ReviewProfile
from wally.finance.notion import FieldMapping, SourceMapping, notion_id


def _data(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1}
    try:
        data = yaml.safe_load(path.read_text()) or {}
        if (
            not isinstance(data, dict)
            or set(data) - {"schema_version", "sources", "profiles", "restricted_databases"}
            or data.get("schema_version") != 1
        ):
            raise ValueError
        return data
    except (ValueError, TypeError, OSError, yaml.YAMLError):
        raise FinanceError("Invalid reviewed finance configuration.") from None


def load_sources(path: Path) -> tuple[SourceMapping, ...]:
    try:
        data = _data(path)
        sources = []
        for raw in data.get("sources", []):
            raw = dict(raw)
            raw["kind"] = Kind(raw["kind"])
            raw["fields"] = {k: FieldMapping(**v) for k, v in raw["fields"].items()}
            source = SourceMapping(**raw)
            source.validate()
            sources.append(source)
        if len({s.key for s in sources}) != len(sources):
            raise ValueError
        return tuple(sources)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise FinanceError("Invalid reviewed finance source configuration.") from None


def load_profiles(path: Path) -> dict[str, ReviewProfile]:
    try:
        profiles = {}
        for raw in _data(path).get("profiles", []):
            raw = dict(raw)
            raw["allowed_origins"] = tuple(raw["allowed_origins"])
            profile = ReviewProfile(**raw)
            profile.validate()
            if profile.id in profiles:
                raise ValueError
            profiles[profile.id] = profile
        return profiles
    except (ValueError, TypeError, KeyError, AttributeError):
        raise FinanceError("Invalid reviewed portal profile configuration.") from None


def load(path: Path) -> tuple[tuple[SourceMapping, ...], dict[str, ReviewProfile]]:
    return load_sources(path), load_profiles(path)


def restricted_databases(path: Path) -> set[str]:
    try:
        return {notion_id(i) for i in _data(path).get("restricted_databases", [])} | {
            notion_id(s.database_id) for s in load_sources(path)
        }
    except (ValueError, TypeError):
        raise FinanceError("Invalid restricted finance source configuration.") from None
