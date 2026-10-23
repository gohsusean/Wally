"""Complete, provisional finance reads using the pinned modern Notion API.

This path is deliberately separate from general knowledge serialization. It never
certifies rows and never interprets titles, prose, formulas or Runtime Action.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID

import httpx

from wally.finance.models import (
    IDENTITY_FIELDS,
    TYPES,
    Candidate,
    FinanceError,
    Kind,
    Locator,
    ReadIssue,
    digest,
    identity_constraints,
    parse_facts,
)
from wally.finance.typical_amount import TypicalAmount

VERSION = "2025-09-03"
SUPPORTED = {"rich_text", "checkbox", "select", "multi_select", "date", "number", "url", "relation"}


class SourceUnavailable(FinanceError):
    """A failed retrieval supplies no contradictory financial facts."""


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
    personal_subjects: dict[str, str] = field(default_factory=dict)
    review_fields: dict[str, FieldMapping] = field(default_factory=dict)
    payment_review_fields: dict[str, FieldMapping] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"notion:{notion_id(self.database_id)}:{notion_id(self.data_source_id)}"

    @property
    def fingerprint(self) -> str:
        data = asdict(self)
        # Advisory metadata is bound to payment reviews, not identity certificates.
        data.pop("payment_review_fields")
        if not self.personal_subjects:
            data.pop("personal_subjects")  # Preserve existing designations.
        if not self.review_fields:
            data.pop("review_fields")
        return digest(data)

    def validate(self) -> None:
        _ = self.key
        if not isinstance(self.payment_review_fields, dict) or any(
            self.kind != Kind.DEFINITION or name != "typical_amount" or mapping.type != "number"
            for name, mapping in self.payment_review_fields.items()
        ):
            raise FinanceError("Unknown payment review-only field mapping.")
        if not isinstance(self.personal_subjects, dict):
            raise FinanceError("Personal exceptions must be an explicit page mapping.")
        if not isinstance(self.review_fields, dict) or any(
            name
            not in {
                "obligation_notes",
                "legacy_frequency",
                "statement_cycle",
                "payment_arrangement",
            }
            or mapping.type not in {"rich_text", "select"}
            for name, mapping in self.review_fields.items()
        ):
            raise FinanceError("Unknown financial review-only field mapping.")
        if self.personal_subjects:
            if (
                self.kind != Kind.ACCOUNT
                or "subject" not in self.fields
                or self.constants.get("subject_kind") != "property"
                or not isinstance(self.personal_subjects, dict)
            ):
                raise FinanceError("Personal exceptions require an explicit property relation.")
            for page, subject in self.personal_subjects.items():
                if (
                    notion_id(page) != page
                    or not isinstance(subject, str)
                    or not subject.startswith("fin_")
                ):
                    raise FinanceError("Personal exceptions need exact page and entity IDs.")
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
        ids = [
            unquote(m.property_id) for m in (
                *self.fields.values(), *self.review_fields.values(),
                *self.payment_review_fields.values(),
            )
        ]
        if len(set(ids)) != len(ids) or any(not i for i in ids):
            raise FinanceError("Ambiguous stable property mapping.")
        if any(m.type not in SUPPORTED for m in self.fields.values()):
            raise FinanceError("Unsupported finance property type.")


@dataclass(frozen=True)
class ReadSnapshot:
    candidates: list[Candidate]
    issues: list[ReadIssue]


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
            raise SourceUnavailable("Finance source unavailable or malformed.") from None

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
        for mapping in (*source.fields.values(), *source.review_fields.values()):
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

    def typical_amount(self, source: SourceMapping, page_id: str, currency: str) -> TypicalAmount:
        """Read the exact related definition by stable ID; never use its display name.

        Invalid values remain advisory. Missing/recreated properties or changed page
        identity cannot silently masquerade as an unchanged blank baseline.
        """
        source.validate()
        mapping = source.payment_review_fields.get("typical_amount")
        if mapping is None:
            return TypicalAmount("invalid", currency, digest([source.key, "unmapped"]))
        schema = self.schema(source)
        matches = [
            p for p in schema.get("properties", {}).values()
            if unquote(str(p.get("id", ""))) == unquote(mapping.property_id)
        ]
        if len(matches) != 1 or matches[0].get("type") != "number":
            raise FinanceError("Typical Amount stable property is missing or changed type.")
        page_id = notion_id(page_id)
        page = self.request("GET", f"/pages/{page_id}")
        self.check_page(page, source)
        if notion_id(page.get("id")) != page_id:
            raise FinanceError("Typical Amount definition identity changed.")
        properties = [
            p for p in page.get("properties", {}).values()
            if unquote(str(p.get("id", ""))) == unquote(mapping.property_id)
        ]
        if len(properties) != 1 or properties[0].get("type") != "number":
            raise FinanceError("Typical Amount page property contract changed.")
        prop = properties[0]
        value = prop.get("number", "invalid")
        amount = "" if value is None else str(value) if type(value) in (int, float) else "invalid"
        return TypicalAmount(
            amount, currency,
            digest([source.key, page_id, asdict(mapping), json.dumps(prop, sort_keys=True)]),
        )

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

    def read_all(
        self, sources: tuple[SourceMapping, ...], rejected: set[str] | None = None
    ) -> ReadSnapshot:
        if len({s.key for s in sources}) != len(sources) or len(
            {notion_id(s.data_source_id) for s in sources}
        ) != len(sources):
            raise FinanceError("Ambiguous finance sources.")
        issues = []
        by_source = {notion_id(s.data_source_id): s for s in sources}
        pages: dict[str, tuple[SourceMapping, dict]] = {}
        for source in sources:
            try:
                source.validate()
                allowed = {
                    Kind.ACCOUNT: {
                        "provider": {Kind.PROVIDER},
                        "subject": {Kind.PROPERTY, Kind.ENTITY},
                    },
                    Kind.DEFINITION: {
                        "account": {Kind.ACCOUNT},
                        "redundant_property": {Kind.PROPERTY},
                    },
                }.get(source.kind, {})
                for name, kinds in allowed.items():
                    if name in source.fields:
                        target = by_source.get(notion_id(source.fields[name].target_data_source))
                        if target is None or target.kind not in kinds:
                            raise FinanceError("Relation mapping targets the wrong record kind.")
                        if (
                            name == "subject"
                            and source.personal_subjects
                            and target.kind != Kind.PROPERTY
                        ):
                            raise FinanceError(
                                "Personal exceptions require a property source mapping."
                            )
                if source.key in (rejected or set()):
                    raise FinanceError("Source authority unavailable.")
                self.validate_schema(source)
                rows = self.paginated(
                    "POST", f"/data_sources/{notion_id(source.data_source_id)}/query"
                )
                complete = {}
                for page in rows:
                    self.check_page(page, source)
                    page_id = notion_id(page.get("id"))
                    if page_id in complete or page_id in pages:
                        raise FinanceError("Duplicate source page.")
                    complete[page_id] = (source, page)
                pages.update(complete)
            except (FinanceError, TypeError, AttributeError, KeyError):
                # No rows from a failed source are published. Its unknown identity
                # space blocks this kind across sources, but not independent kinds.
                issues.append(ReadIssue(source.kind, source.key, reason="source_invalid"))
        raw_rows = {}
        invalid: set[str] = set()
        unavailable_pages: set[str] = set()
        for page_id, (source, page) in pages.items():
            raw = dict(source.constants)
            if source.kind == Kind.PROPERTY and "unit" not in source.fields:
                raw.setdefault("unit", "")
            for name, mapping in source.fields.items():
                try:
                    if name == "subject" and page_id in source.personal_subjects:
                        # Only explicitly bound personal pages may have no property.
                        mapping = replace(mapping, optional=True)
                    value = self.value(page, mapping)
                    if mapping.type == "relation" and value:
                        if value not in pages:
                            raise FinanceError("Relation target outside complete catalog.")
                        target_source, _ = pages[value]
                        if notion_id(target_source.data_source_id) != notion_id(
                            mapping.target_data_source
                        ):
                            raise FinanceError("Relation target outside mapped data source.")
                        raw[name] = self.locator(target_source, value).key
                        try:
                            fetched = self.request("GET", f"/pages/{value}")
                            self.check_page(fetched, target_source)
                            if notion_id(fetched.get("id")) != value:
                                raise FinanceError("Relation response identity changed.")
                        except (FinanceError, TypeError, AttributeError, KeyError) as error:
                            invalid.add(value)
                            if isinstance(error, SourceUnavailable):
                                unavailable_pages.add(value)
                                raise
                            raise FinanceError("Relation target unavailable.") from None
                    else:
                        raw[name] = value
                except (FinanceError, TypeError, AttributeError, KeyError) as error:
                    if isinstance(error, SourceUnavailable):
                        unavailable_pages.add(page_id)
                    # Independently readable identity fields survive other failures.
                    # Complete multi-relations are invalid facts, but their full
                    # possibility set can prove disjointness from a third chain.
                    if (
                        mapping.type == "relation"
                        and name in IDENTITY_FIELDS[source.kind]
                        and name not in raw
                    ):
                        with suppress(FinanceError, TypeError, AttributeError, KeyError):
                            ids = self.value(page, mapping, allow_multiple=True)
                            if ids and all(
                                i in pages
                                and notion_id(pages[i][0].data_source_id)
                                == notion_id(mapping.target_data_source)
                                for i in ids
                            ):
                                raw[name] = tuple(self.locator(pages[i][0], i).key for i in ids)
                    invalid.add(page_id)
            if source.kind == Kind.ACCOUNT and "subject" in source.fields:
                target = by_source[notion_id(source.fields["subject"].target_data_source)]
                expected = {"property"} if target.kind == Kind.PROPERTY else {"personal", "entity"}
                personal = source.personal_subjects.get(page_id)
                if personal and raw.get("subject") == "":
                    raw["subject"] = personal
                    raw["subject_kind"] = "personal"
                elif personal:
                    # An exception cannot conceal a newly added or unreadable property.
                    raw.pop("subject", None)
                    invalid.add(page_id)
                elif (
                    not isinstance(raw.get("subject_kind"), str)
                    or raw["subject_kind"] not in expected
                ):
                    # Contradictory scope is not reliable disjointness evidence.
                    raw.pop("subject", None)
                    invalid.add(page_id)
            raw_rows[page_id] = raw
        card_accounts = {
            raw.get("account")
            for page_id, raw in raw_rows.items()
            if pages[page_id][0].kind == Kind.DEFINITION
            and raw.get("obligation_type") == "credit_card_payment"
            and isinstance(raw.get("account"), str)
        }
        candidates = []
        for page_id, (source, page) in pages.items():
            raw = raw_rows[page_id]
            locator = self.locator(source, page_id)
            try:
                # A namespace edit cannot disguise a full card number as a
                # generic customer reference and persist it before graph checks.
                if (
                    source.kind == Kind.ACCOUNT
                    and locator.key in card_accounts
                    and raw.get("namespace") != "credit_card_last4"
                ):
                    raise FinanceError("Card accounts require the safe identifier namespace.")
                facts = parse_facts(source.kind, raw)
                reviewed = {
                    name: self.value(page, mapping)
                    for name, mapping in source.review_fields.items()
                }
                if page_id in invalid:
                    raise FinanceError("Financial row unavailable.")
            except (FinanceError, TypeError, AttributeError, KeyError):
                identity_raw = dict(raw)
                for name in ("provider", "subject", "account"):
                    if name not in source.fields:
                        # Unresolved canonical constants are not locator coordinates.
                        identity_raw.pop(name, None)
                issues.append(
                    ReadIssue(
                        source.kind,
                        source.key,
                        locator,
                        identity_constraints(source.kind, identity_raw),
                        "source_unavailable" if page_id in unavailable_pages else "record_invalid",
                    )
                )
                continue
            candidates.append(
                Candidate(
                    source.kind,
                    locator,
                    facts,
                    str(page.get("last_edited_time", "")),
                    review_fingerprint=digest(reviewed) if reviewed else "",
                )
            )
        return ReadSnapshot(candidates, issues)

    @staticmethod
    def locator(source: SourceMapping, page_id: str) -> Locator:
        return Locator(
            "notion",
            notion_id(source.database_id),
            notion_id(source.data_source_id),
            notion_id(page_id),
        )

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

    def value(self, page: dict, mapping: FieldMapping, *, allow_multiple: bool = False) -> Any:
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
            if allow_multiple:
                return ids
            if len(ids) > 1 or len(ids) != len(set(ids)) or (not ids and not mapping.optional):
                raise FinanceError("Relation must have exactly one designated target.")
            return ids[0] if ids else ""
        if mapping.type == "rich_text":
            if not isinstance(value, list) or len(value) >= 25:
                raise FinanceError("Missing or potentially truncated text property.")
            if any(i.get("type") != "text" for i in value):
                raise FinanceError("Only plain finance text is supported.")
            parts = [i.get("plain_text", i.get("text", {}).get("content")) for i in value]
            if any(not isinstance(part, str) for part in parts):
                raise FinanceError("Malformed finance text property.")
            return "".join(parts)
        if mapping.type == "select":
            if value is None:
                return ""
            if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                raise FinanceError("Malformed finance select property.")
            return value["name"]
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
