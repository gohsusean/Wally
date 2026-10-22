"""Reviewed runtime configuration, reloaded for certification/execution checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from wally.finance.models import FinanceError, Kind, ReviewProfile, digest, iso_date, text
from wally.finance.notion import FieldMapping, SourceMapping, notion_id


def _data(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1}
    try:
        data = yaml.safe_load(path.read_text()) or {}
        if (
            not isinstance(data, dict)
            or set(data)
            - {
                "schema_version",
                "sources",
                "profiles",
                "restricted_databases",
                "payment_preferences",
            }
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
            raw["review_fields"] = {
                k: FieldMapping(**v) for k, v in raw.get("review_fields", {}).items()
            }
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


@dataclass(frozen=True)
class PaymentPreferences:
    """Owner routing intent only; never an executor registration or authorization."""

    default_transfer_bank: str
    transfer_login_url: str
    first_bill_account: str
    first_bill_definition: str
    first_bill_period_start: str
    first_bill_period_end: str

    @property
    def fingerprint(self):
        return digest(asdict(self))


def load_payment_preferences(path: Path) -> PaymentPreferences | None:
    raw = _data(path).get("payment_preferences")
    if raw is None:
        return None
    try:
        if not isinstance(raw, dict) or set(raw) != set(PaymentPreferences.__dataclass_fields__):
            raise ValueError
        preferences = PaymentPreferences(**raw)
        if preferences.default_transfer_bank != "hlb" or preferences.transfer_login_url != (
            "https://s.hongleongconnect.my/"
        ):
            raise ValueError
        for field in (preferences.first_bill_account, preferences.first_bill_definition):
            if not text(field).startswith("fin_"):
                raise ValueError
        start = iso_date(preferences.first_bill_period_start)
        end = iso_date(preferences.first_bill_period_end)
        if start > end:
            raise ValueError
        return preferences
    except (ValueError, TypeError, AttributeError):
        raise FinanceError("Invalid owner payment preferences; no route may be inferred.") from None


def restricted_databases(path: Path) -> set[str]:
    try:
        return {notion_id(i) for i in _data(path).get("restricted_databases", [])} | {
            notion_id(s.database_id) for s in load_sources(path)
        }
    except (ValueError, TypeError):
        raise FinanceError("Invalid restricted finance source configuration.") from None
