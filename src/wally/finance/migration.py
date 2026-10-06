"""Explicit, additive-only schema migration. No row writes, renames or auto-certification."""

from __future__ import annotations

from wally.finance.models import FinanceError, digest
from wally.finance.notion import SourceMapping
from wally.finance.service import FinanceService
from wally.models.principal import RequestContext

# Fields unavailable or ambiguous in the existing catalog get separate typed columns.
# Do not reinterpret TypicalAmount, AccountNumber(number), titles or next/last payment dates.
COLUMNS = {
    "property": {},
    "entity": {"Wally Entity Key": "rich_text"},
    "provider": {
        "Wally Issuer Key": "rich_text",
        "Wally Review Profile": "rich_text",
    },
    "account": {
        "Wally Identifier Namespace": "rich_text",
        "Wally Subject Kind": "select",
        "Wally Responsibility": "select",
        "Wally Username Ref": "rich_text",
        "Wally Password Ref": "rich_text",
    },
    "definition": {
        "Wally Charge Key": "rich_text",
        "Wally Obligation Type": "rich_text",
        "Wally Currency": "select",
        "Wally Amount Policy": "select",
        "Wally Fixed Amount": "rich_text",
        "Wally Frequency": "select",
        "Wally Evidence Mode": "select",
        "Wally Due Mode": "select",
        "Wally Cycle Anchor": "date",
        "Wally Due Months": "multi_select",
        "Wally Month End Rule": "select",
        "Wally Oneoff Due": "date",
        "Wally Timezone": "rich_text",
    },
}


def preview(service: FinanceService, source: SourceMapping) -> dict:
    if service.reader is None:
        raise FinanceError("Notion finance reader unavailable.")
    schema = service.reader.schema(source)
    existing = schema.get("properties", {})
    desired = COLUMNS[source.kind.value]
    if any(name in existing and existing[name].get("type") != typ for name, typ in desired.items()):
        raise FinanceError("Migration refuses to replace or reinterpret an existing column.")
    additions = {
        name: {"type": typ, typ: {}} for name, typ in desired.items() if name not in existing
    }
    return {
        "properties": [
            {
                "name": name,
                "property_id": prop.get("id"),
                "type": prop.get("type"),
                "relation_target": prop.get("relation", {}).get("data_source_id", ""),
            }
            for name, prop in existing.items()
        ],
        "data_source_id": source.data_source_id,
        "additions": additions,
        "schema_fingerprint": digest(schema),
        "mapping_fingerprint": source.fingerprint,
    }


def apply(service: FinanceService, source: SourceMapping, *, context: RequestContext) -> dict:
    ctx = service._owner(context)
    record = service.registry.find_by_key_or_id(source.database_id)
    from wally.models.knowledge import KnowledgeClass

    if (
        record is None
        or record.classification != KnowledgeClass.OPERATIONAL
        or not record.approved_by
    ):
        raise FinanceError("Migration requires an approved operational source.")
    plan = preview(service, source)
    if not plan["additions"]:
        return plan
    service._confirm(
        f"Add {len(plan['additions'])} typed columns to {source.kind.value} source; "
        "preserve all rows and IDs",
        digest(plan),
    )
    if (
        preview(service, source) != plan
        or service.registry.find_by_key_or_id(source.database_id) != record
        or [s.fingerprint for s in service.sources() if s.key == source.key] != [source.fingerprint]
    ):
        raise FinanceError("Schema or mapping changed during migration confirmation.")
    service.reader.request(
        "PATCH", f"/data_sources/{source.data_source_id}", json={"properties": plan["additions"]}
    )
    service.store.audit(
        source.key, "schema_added", {"plan": digest(plan), "provenance": ctx.provenance().as_dict()}
    )
    # IDs returned by Notion must be deliberately pinned in the reviewed config.
    return preview(service, source)
