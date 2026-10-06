"""Complete, provisional finance reads using the pinned modern Notion API.

This path is deliberately separate from general knowledge serialization. It never
certifies rows and never interprets titles, prose, formulas or Runtime Action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID

import httpx

from wally.finance.models import TYPES, Candidate, FinanceError, Kind, Locator, digest, parse_facts

VERSION = "2025-09-03"
SUPPORTED = {"rich_text", "checkbox", "select", "multi_select", "date", "number", "url", "relation"}


def notion_id(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise FinanceError("Malformed Notion locator.") from None


@dataclass(frozen=True)
class FieldMapping:
    property_id: str
    type: str
    optional: bool = False
    target_data_source: str = ""


@dataclass(frozen=True)
class SourceMapping:
    database_id: str
    data_source_id: str
    kind: Kind
    fields: dict[str, FieldMapping]
    constants: dict[str, Any] = field(default_factory=dict)
    role: str = "finance"

    @property
    def key(self) -> str:
        return f"notion:{notion_id(self.database_id)}:{notion_id(self.data_source_id)}"

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def validate(self) -> None:
        _ = self.key
        if (
            not isinstance(self.kind, Kind)
            or (set(self.fields) | set(self.constants))
            - TYPES[self.kind].__dataclass_fields__.keys()
        ):
            raise FinanceError("Unknown mapped financial fields.")
        relations = {"provider", "subject", "account", "redundant_property"}
        calendars = {
            "due_day": "number",
            "due_months": "multi_select",
            "active": "checkbox",
            "cycle_anchor": "date",
            "oneoff_due": "date",
            "portal_url": "url",
        }
        for name, mapping in self.fields.items():
            expected = "relation" if name in relations else calendars.get(name)
            if expected and mapping.type != expected:
                raise FinanceError("Financial field has an incompatible mapped type.")
            if not expected and mapping.type not in {"rich_text", "select"}:
                raise FinanceError("Financial identity and money require typed text.")
            if mapping.type == "relation":
                notion_id(mapping.target_data_source)
        if self.role != "finance" or self.kind in {Kind.INSTANCE}:
            raise FinanceError("Unknown finance source role or record kind.")
        if set(self.fields) & set(self.constants):
            raise FinanceError("Mapped fields conflict with constants.")
        ids = [unquote(m.property_id) for m in self.fields.values()]
        if len(set(ids)) != len(ids) or any(not i for i in ids):
            raise FinanceError("Ambiguous stable property mapping.")
        if any(m.type not in SUPPORTED for m in self.fields.values()):
            raise FinanceError("Unsupported finance property type.")


class NotionFinanceReader:
    def __init__(self, client: httpx.Client):
        self.client = client

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = self.client.request(
                method, path, headers={"Notion-Version": VERSION}, **kwargs
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            # Do not include URLs, credentials, identifiers, response bodies or exceptions.
            raise FinanceError("Finance source unavailable or malformed.") from None

    def schema(self, source: SourceMapping) -> dict:
        source.validate()
        db = self.request("GET", f"/databases/{notion_id(source.database_id)}")
        sources = db.get("data_sources")
        if (
            not isinstance(sources, list)
            or [notion_id(s.get("id")) for s in sources].count(notion_id(source.data_source_id))
            != 1
        ):
            raise FinanceError("Database/data-source relationship is ambiguous.")
        schema = self.request("GET", f"/data_sources/{notion_id(source.data_source_id)}")
        if notion_id(schema.get("id")) != notion_id(source.data_source_id) or notion_id(
            schema.get("parent", {}).get("database_id")
        ) != notion_id(source.database_id):
            raise FinanceError("Data-source parent changed.")
        return schema

    def validate_schema(self, source: SourceMapping) -> None:
        schema = self.schema(source)
        props = schema.get("properties")
        if not isinstance(props, dict):
            raise FinanceError("Missing finance schema.")
        for mapping in source.fields.values():
            matches = [
                p
                for p in props.values()
                if unquote(str(p.get("id", ""))) == unquote(mapping.property_id)
            ]
            if len(matches) != 1 or matches[0].get("type") != mapping.type:
                raise FinanceError("Stable property missing, recreated or has changed type.")
            if mapping.type == "relation" and notion_id(
                matches[0].get("relation", {}).get("data_source_id")
            ) != notion_id(mapping.target_data_source):
                raise FinanceError("Relation schema target changed.")

    def paginated(self, method: str, path: str, *, body: dict | None = None) -> list[dict]:
        cursor = None
        seen: set[str] = set()
        results: list[dict] = []
        for _ in range(10000):
            params = {"page_size": 100}
            if cursor is not None:
                params["start_cursor"] = cursor
            result = self.request(
                method,
                path,
                **(
                    {"json": {**(body or {}), **params}} if method == "POST" else {"params": params}
                ),
            )
            if (
                not isinstance(result.get("results"), list)
                or type(result.get("has_more")) is not bool
            ):
                raise FinanceError("Incomplete paginated source.")
            if result.get("request_status", {}).get("type", "complete") != "complete":
                raise FinanceError("Notion returned an incomplete query result.")
            results.extend(result["results"])
            if not result["has_more"]:
                if result.get("next_cursor"):
                    raise FinanceError("Conflicting pagination state.")
                return results
            cursor = result.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise FinanceError("Truncated or cyclic pagination.")
            seen.add(cursor)
        raise FinanceError("Finance pagination limit exceeded.")

    def read_all(self, sources: tuple[SourceMapping, ...]) -> list[Candidate]:
        if len({s.key for s in sources}) != len(sources) or len(
            {notion_id(s.data_source_id) for s in sources}
        ) != len(sources):
            raise FinanceError("Ambiguous finance sources.")
        pages: dict[str, tuple[SourceMapping, dict]] = {}
        for source in sources:
            self.validate_schema(source)
            rows = self.paginated("POST", f"/data_sources/{notion_id(source.data_source_id)}/query")
            for page in rows:
                self.check_page(page, source)
                page_id = notion_id(page.get("id"))
                if page_id in pages:
                    raise FinanceError("Duplicate source page.")
                pages[page_id] = (source, page)
        candidates = []
        for source, page in pages.values():
            raw = dict(source.constants)
            for name, mapping in source.fields.items():
                value = self.value(page, mapping)
                if mapping.type == "relation" and value:
                    if value not in pages:
                        # Even a successful GET cannot make an unenumerated/undesigned row trusted.
                        self.request("GET", f"/pages/{value}")
                        raise FinanceError(
                            "Relation target is inaccessible or outside the complete catalog."
                        )
                    target_source, target = pages[value]
                    if notion_id(target_source.data_source_id) != notion_id(
                        mapping.target_data_source
                    ):
                        raise FinanceError("Relation target outside mapped data source.")
                    self.check_page(self.request("GET", f"/pages/{value}"), target_source)
                    value = Locator(
                        "notion",
                        notion_id(target_source.database_id),
                        notion_id(target_source.data_source_id),
                        notion_id(target["id"]),
                    ).key
                raw[name] = value
            candidates.append(
                Candidate(
                    source.kind,
                    Locator(
                        "notion",
                        notion_id(source.database_id),
                        notion_id(source.data_source_id),
                        notion_id(page["id"]),
                    ),
                    parse_facts(source.kind, raw),
                    str(page.get("last_edited_time", "")),
                )
            )
        return candidates

    @staticmethod
    def check_page(page: dict, source: SourceMapping) -> None:
        if (
            page.get("object") != "page"
            or page.get("archived")
            or page.get("in_trash")
            or notion_id(page.get("parent", {}).get("data_source_id"))
            != notion_id(source.data_source_id)
        ):
            raise FinanceError("Invalid finance page or source parent.")

    def value(self, page: dict, mapping: FieldMapping) -> Any:
        props = page.get("properties", {})
        matches = [
            v
            for v in props.values()
            if unquote(str(v.get("id", ""))) == unquote(mapping.property_id)
        ]
        if len(matches) != 1 or matches[0].get("type") != mapping.type:
            raise FinanceError("Page property contract changed.")
        prop = matches[0]
        value = prop.get(mapping.type)
        if mapping.type == "relation":
            if type(prop.get("has_more")) is not bool or not isinstance(value, list):
                raise FinanceError("Incomplete relation property.")
            if prop["has_more"]:
                items = self.paginated(
                    "GET",
                    f"/pages/{notion_id(page['id'])}/properties/"
                    f"{quote(unquote(mapping.property_id), safe='')}",
                )
                if any(i.get("type") != "relation" for i in items):
                    raise FinanceError("Malformed relation hydration.")
                value = [i.get("relation") for i in items]
            ids = [notion_id(i.get("id")) for i in value]
            if len(ids) > 1 or len(ids) != len(set(ids)) or (not ids and not mapping.optional):
                raise FinanceError("Relation must have exactly one designated target.")
            return ids[0] if ids else ""
        if mapping.type == "rich_text":
            if not isinstance(value, list) or len(value) >= 25:
                raise FinanceError("Missing or potentially truncated text property.")
            if any(i.get("type") != "text" for i in value):
                raise FinanceError("Only plain finance text is supported.")
            return "".join(i.get("plain_text", i.get("text", {}).get("content", "")) for i in value)
        if mapping.type == "select":
            return value.get("name", "") if isinstance(value, dict) else ""
        if mapping.type == "multi_select":
            if not isinstance(value, list):
                raise FinanceError("Malformed multi-select.")
            try:
                return tuple(int(i["name"]) for i in value)
            except (KeyError, ValueError, TypeError):
                raise FinanceError("Months must be explicit integers.") from None
        if mapping.type == "date":
            if value is None:
                return ""
            if not isinstance(value, dict) or value.get("end") or value.get("time_zone"):
                raise FinanceError("Date ranges/timestamps require explicit supported mapping.")
            return value.get("start", "")
        if mapping.type == "number":
            # Money/customer IDs MUST use text. Numbers are only small calendar integers.
            if value is None and mapping.optional:
                return None
            if type(value) not in (int, float) or not 1 <= value <= 31 or int(value) != value:
                raise FinanceError("Invalid calendar number.")
            return int(value)
        return value if value is not None else ""
