"""Narrow Notion PATCH adapter with semantic preconditions and independent reads.

No secrets are accepted as edit data. A write client factory is invoked only by
write(), after runtime authorization. No request or retry middleware is installed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from urllib.parse import quote, unquote

import httpx

from wally.finance.models import Kind
from wally.finance.models import digest as finance_digest
from wally.finance.notion import VERSION, NotionFinanceReader, notion_id
from wally.models.knowledge import KnowledgeClass
from wally.ops.notion_contracts import EDIT_VALUES, TEXT_FIELDS, edit_type
from wally.ops.notion_edits import EditError, EditNotDispatched, EditTarget, RecordSnapshot
from wally.runtime.confirmation import digest

_SYSTEM_PROPERTIES = frozenset({"created_time", "last_edited_time", "created_by", "last_edited_by"})
_SIMPLE_PROPERTIES = frozenset({"checkbox", "number", "url", "email", "phone_number", "date"})


class NotionEditBackend:
    def __init__(
        self,
        reader: NotionFinanceReader,
        registry,
        *,
        finance,
        write_client: Callable[[], httpx.Client],
    ):
        self.reader = reader
        self.registry = registry
        self.finance = finance
        self.write_client = write_client

    def _gate(self, target: EditTarget) -> str:
        record = self.registry.find_by_key_or_id(target.database_id)
        if (
            record is None
            or record.classification != KnowledgeClass.OPERATIONAL
            or not record.approved_by
        ):
            raise EditError("Target database is not approved operational knowledge.")
        ids = [unquote(r.property_id) for r in target.properties]
        if not ids or len(ids) != len(set(ids)) or set(ids) & set(target.audit_property_ids):
            raise EditError("Ambiguous edit property policy.")
        for rule in target.properties:
            if (
                rule.field not in EDIT_VALUES
                or not rule.values
                or not set(rule.values) <= EDIT_VALUES[rule.field]
                or type(rule.allow_missing) is not bool
            ):
                raise EditError("Property is outside the metadata-edit capability.")
        finance_sources = [
            s
            for s in self.finance.sources()
            if notion_id(s.database_id) == notion_id(target.database_id)
        ]
        if (
            record.role == "finance"
            or finance_sources
            or target.finance_id
            or target.finance_issue_digest
        ):
            sources = [
                s
                for s in finance_sources
                if notion_id(s.data_source_id) == notion_id(target.data_source_id)
            ]
            if len(sources) != 1 or bool(target.finance_id) == bool(target.finance_issue_digest):
                raise EditError("A designated canonical financial target is required.")
            source = sources[0]
            self.finance._source_gate(source)
            for rule in target.properties:
                mapping = source.fields.get(rule.field)
                if (
                    mapping is None
                    or mapping.type != edit_type(rule.field)
                    or unquote(mapping.property_id) != rule.property_id
                ):
                    raise EditError("Edit does not match the canonical financial property.")
            if set(target.audit_property_ids) & {
                unquote(f.property_id) for f in source.fields.values()
            }:
                raise EditError("Business fields cannot be excluded as audit metadata.")
            if target.finance_issue_digest:
                if not target.evidence_digest:
                    raise EditError("Financial initialization requires reviewed evidence.")
                # An incomplete locator is attribution, never certified identity.
                # Pin the existing diagnostic and designated mapping; do not mint
                # provisional/certified objects or skip complete-inventory gates.
                issues = self.finance.store.read_issues()
                if source.kind not in {Kind.ACCOUNT, Kind.DEFINITION} or any(
                    issue.source == source.key and issue.locator is None for issue in issues
                ):
                    raise EditError("Incomplete financial source is unavailable.")
                matches = [
                    issue
                    for issue in issues
                    if issue.source == source.key
                    and issue.kind == source.kind
                    and issue.locator is not None
                    and issue.locator.system == "notion"
                    and notion_id(issue.locator.page) == target.page_id
                    and notion_id(issue.locator.database) == target.database_id
                    and notion_id(issue.locator.data_source) == target.data_source_id
                    and issue.reason == "record_invalid"
                ]
                if (
                    len(matches) != 1
                    or finance_digest(asdict(matches[0])) != target.finance_issue_digest
                    or any(
                        r.candidate.locator.key == matches[0].locator.key
                        for r in self.finance.store.records()
                    )
                ):
                    raise EditError("Incomplete financial target changed or became canonical.")
                return finance_digest(
                    ("incomplete", source.fingerprint, target.finance_issue_digest)
                )
            current = self.finance.store.get(target.finance_id)
            locator = current.candidate.locator
            if (
                locator.system != "notion"
                or notion_id(locator.page) != target.page_id
                or notion_id(locator.database) != target.database_id
                or notion_id(locator.data_source) != target.data_source_id
            ):
                raise EditError("Canonical financial target mismatch.")
            return finance_digest((current.id, current.version, current.candidate.fingerprint))
        return ""

    def read(self, target: EditTarget) -> RecordSnapshot:
        if self.reader is None:
            raise EditError("Notion reader unavailable; restart the reviewed runtime after setup.")
        version = self._gate(target)
        page_id = notion_id(target.page_id)
        source_id = notion_id(target.data_source_id)
        schema = self.reader.request("GET", f"/data_sources/{source_id}")
        if notion_id(schema.get("id")) != source_id or notion_id(
            schema.get("parent", {}).get("database_id")
        ) != notion_id(target.database_id):
            raise EditError("Notion data source moved or changed.")
        schema_properties = _properties(schema)
        page = self.reader.request("GET", f"/pages/{page_id}")
        if (
            page.get("object") != "page"
            or notion_id(page.get("id")) != page_id
            or notion_id(page.get("parent", {}).get("data_source_id")) != source_id
            or page.get("archived")
            or page.get("in_trash")
            or page.get("is_archived")
        ):
            raise EditError("Notion target moved, archived, or changed.")
        properties = _properties(page)
        if set(properties) != set(schema_properties):
            raise EditError("Incomplete Notion property read.")
        if set(target.audit_property_ids) - properties.keys():
            raise EditError("Audit property contract changed.")
        for key in target.audit_property_ids:
            audit = schema_properties[key]
            if audit.get("type") != "rich_text" or audit.get("name") != "Audit History":
                raise EditError("Only the registered Audit History text field may be excluded.")
        values, hashes = {}, {}
        for key, prop in properties.items():
            kind = prop.get("type")
            if kind != schema_properties[key].get("type"):
                raise EditError("Notion property type changed.")
            if kind in _SYSTEM_PROPERTIES or key in target.audit_property_ids:
                continue
            value = _semantic(prop)
            hashes[key] = digest([kind, value])
        for rule in target.properties:
            prop = properties.get(rule.property_id, {})
            if prop.get("type") != edit_type(rule.field):
                raise EditError("Editable metadata property type changed.")
            current = _editable(prop, rule.field)
            # Never persist unknown/raw values, which might contain confidential text.
            if current is None:
                if not rule.allow_missing:
                    raise EditError("Missing metadata requires explicit initialization policy.")
            elif current not in EDIT_VALUES[rule.field]:
                raise EditError("Current metadata needs separate review.")
            if rule.field not in TEXT_FIELDS:
                options = schema_properties[rule.property_id].get("select", {}).get("options", [])
                names = [option.get("name") for option in options]
                if len(names) != len(set(names)) or not set(rule.values) <= set(names):
                    raise EditError("Approved replacement options are missing or ambiguous.")
            values[rule.property_id] = current
            hashes[rule.property_id] = digest([edit_type(rule.field), current])
        return RecordSnapshot(values, hashes, digest(schema_properties), version)

    def catalog_snapshot(self, target, snapshot):
        if target.finance_issue_digest:
            self._gate(target)
            return snapshot  # No certified projection exists for this locator.
        if not target.finance_id:
            return snapshot
        facts = asdict(self.finance.store.get(target.finance_id).candidate.facts)
        values, hashes = dict(snapshot.values), dict(snapshot.hashes)
        for rule in target.properties:
            if rule.field in facts and facts[rule.field] in EDIT_VALUES[rule.field]:
                values[rule.property_id] = facts[rule.field]
                hashes[rule.property_id] = digest([edit_type(rule.field), facts[rule.field]])
        return RecordSnapshot(values, hashes, snapshot.schema_digest, snapshot.finance_version)

    def reconcile_certification(self, target, changed_ids, *, snapshot, baseline_finance_version):
        if target.finance_issue_digest:
            self._gate(target)
            return False  # No canonical record/certificate exists; never create one.
        if not target.finance_id:
            return False
        current_version = self._gate(target)
        source = next(
            s
            for s in self.finance.sources()
            if notion_id(s.data_source_id) == notion_id(target.data_source_id)
        )
        affected = bool(
            set(changed_ids) & {unquote(mapping.property_id) for mapping in source.fields.values()}
        )
        if affected:
            current = self.finance.store.get(target.finance_id)
            if current_version != baseline_finance_version:
                # A newer catalog version may already have been reconciled/certified
                # through the existing owner workflow. Do not revoke it for old drift.
                facts = asdict(current.candidate.facts)
                mapped_changes = set(changed_ids) & {
                    unquote(mapping.property_id) for mapping in source.fields.values()
                }
                reviewed_enums = {rule.property_id for rule in target.properties}
                enum_drift = any(
                    snapshot.values[rule.property_id] != facts.get(rule.field)
                    for rule in target.properties
                )
                if mapped_changes <= reviewed_enums and not enum_drift:
                    return True  # Prior assertion was affected; current certificate is separate.
            if not current.invalid:
                self.finance.store.invalidate(target.finance_id, "external_notion_change_detected")
        return affected

    def invalidate_certification(self, target: EditTarget) -> None:
        self._gate(target)
        if target.finance_id:
            self.finance.store.invalidate(target.finance_id, "approved_notion_edit_started")
            # Dependency checks fail closed through the existing FinanceService;
            # old certificates/enablement cannot become valid after a value reversion.

    def write(
        self,
        target: EditTarget,
        replacements: dict[str, str],
        expected: RecordSnapshot,
        revalidate: Callable[[], None],
    ) -> None:
        dispatched = False
        # The factory owns credential resolution. Never log HTTP bodies or exceptions.
        try:
            self._gate(target)
            rules = {r.property_id: r for r in target.properties}
            if not replacements or set(replacements) - rules.keys():
                raise EditError("Write exceeds registered property scope.")
            if any(value not in rules[key].values for key, value in replacements.items()):
                raise EditError("Write exceeds registered replacement scope.")
            payload = {
                key: (
                    {"rich_text": [{"type": "text", "text": {"content": value}}]}
                    if rules[key].field in TEXT_FIELDS
                    else {"select": {"name": value}}
                )
                for key, value in replacements.items()
            }
            with self.write_client() as client:
                revalidate()
                if asdict(self.read(target)) != asdict(expected):
                    raise EditError("Source changed during credential acquisition.")
                revalidate()
                dispatched = True
                response = client.patch(
                    f"https://api.notion.com/v1/pages/{quote(notion_id(target.page_id), safe='')}",
                    headers={"Notion-Version": VERSION},
                    json={"properties": payload},
                    follow_redirects=False,
                )
                response.raise_for_status()
        except Exception:
            if not dispatched:
                raise EditNotDispatched(
                    "Notion PATCH was not dispatched; prewrite checks failed."
                ) from None
            raise EditError("Notion write outcome is uncertain.") from None


def _properties(document: dict) -> dict:
    raw = document.get("properties")
    if not isinstance(raw, dict):
        raise EditError("Missing property set.")
    result = {}
    for name, prop in raw.items():
        if not isinstance(prop, dict) or not isinstance(prop.get("id"), str):
            raise EditError("Invalid property identity.")
        key = unquote(prop["id"])
        if not key or key in result:
            raise EditError("Ambiguous property identity.")
        result[key] = {**prop, "name": name}
    return result


def _semantic(prop: dict):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in {"select", "status"}:
        if value is None:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise EditError("Malformed select property.")
        return value["name"]
    if kind in _SIMPLE_PROPERTIES:
        return value
    if kind in {"rich_text", "title", "multi_select", "relation", "people"}:
        # Full property pagination/hydration is deliberately not guessed here.
        # Fail closed when the page response might have truncated data.
        if not isinstance(value, list) or len(value) >= 25 or prop.get("has_more"):
            raise EditError("Complete property hydration required before editing.")
        if kind == "relation":
            return sorted(notion_id(v.get("id")) for v in value)
        if kind == "multi_select":
            return sorted(v["name"] for v in value)
        return value
    raise EditError("Unsupported protected property; extend semantic verification before editing.")


def _editable(prop: dict, field: str):
    value = _semantic(prop)
    if field not in TEXT_FIELDS:
        return value
    if not value:
        return None  # Only an actual empty rich-text list represents missing text.
    if len(value) != 1 or value[0].get("type") != "text":
        raise EditError("Taxonomy text needs separate review.")
    span = value[0]
    if (
        span.get("href")
        or span.get("text", {}).get("link")
        or any(v for k, v in span.get("annotations", {}).items() if k != "color")
        or span.get("annotations", {}).get("color", "default") != "default"
    ):
        raise EditError("Formatted taxonomy text needs separate review.")
    result = span.get("text", {}).get("content")
    if not isinstance(result, str) or not result:
        raise EditError("Malformed taxonomy text.")
    return result
