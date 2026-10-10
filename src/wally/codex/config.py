"""Strict, secret-free scoped-edit configuration. Reloaded at runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from wally.adapters.macos.confirmation import MacOSBiometricConfirmation
from wally.ops.notion_edits import EditError, EditTarget, PropertyRule


@dataclass(frozen=True)
class EditConfig:
    writes_enabled: bool = False
    targets: tuple[EditTarget, ...] = ()
    confirmer: MacOSBiometricConfirmation | None = None


def load_config(path: Path) -> EditConfig:
    if not path.exists():
        return EditConfig()
    try:
        data = yaml.safe_load(path.read_text())
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or set(data) - {"schema_version", "writes_enabled", "targets", "macos_confirmation"}
            or type(data.get("writes_enabled", False)) is not bool
        ):
            raise ValueError
        targets = []
        for raw in data.get("targets", []):
            raw = dict(raw)
            rules = []
            for rule in raw.pop("properties"):
                rule = dict(rule)
                if not isinstance(rule.get("values"), list):
                    raise ValueError
                rule["values"] = tuple(rule["values"])
                rules.append(PropertyRule(**rule))
            raw["properties"] = tuple(rules)
            raw["audit_property_ids"] = tuple(raw.get("audit_property_ids", ()))
            targets.append(EditTarget(**raw))
        if len({t.key for t in targets}) != len(targets):
            raise ValueError
        confirmer = None
        native = data.get("macos_confirmation")
        if native is not None:
            if not isinstance(native, dict) or set(native) != {
                "helper",
                "helper_sha256",
                "owner_uid",
            }:
                raise ValueError
            if type(native["owner_uid"]) is not int or native["owner_uid"] < 0:
                raise ValueError
            fingerprint = native["helper_sha256"]
            if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                raise ValueError
            int(fingerprint, 16)
            helper = Path(native["helper"])
            if not helper.is_absolute():
                raise ValueError
            confirmer = MacOSBiometricConfirmation(
                helper,
                helper_sha256=fingerprint,
                owner_uid=native["owner_uid"],
            )
        return EditConfig(data.get("writes_enabled", False), tuple(targets), confirmer)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, yaml.YAMLError):
        raise EditError("Invalid reviewed Notion edit configuration.") from None


class ConfiguredConfirmation:
    """Re-read reviewed authentication policy before invoking any native helper."""

    def __init__(self, path: Path, provider: MacOSBiometricConfirmation):
        self.path = path
        self.provider = provider

    @property
    def method(self):
        return self.provider.method

    def confirm(self, review):
        current = load_config(self.path).confirmer
        if current is None or (
            current.helper != self.provider.helper
            or current.helper_sha256 != self.provider.helper_sha256
            or current.owner_uid != self.provider.owner_uid
        ):
            return False
        return current.confirm(review)
