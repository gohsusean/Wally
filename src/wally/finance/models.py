"""Provider-independent, typed financial candidates and reviewed runtime profiles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from wally.runtime.secrets_safety import evaluate_secret_reference

SCHEMA_VERSION = 1


class FinanceError(ValueError):
    """A bounded diagnostic; never include source values in exceptions."""


class Kind(StrEnum):
    PROPERTY = "property"
    ENTITY = "entity"
    PROVIDER = "provider"
    ACCOUNT = "account"
    DEFINITION = "definition"
    INSTANCE = "instance"


class Scope(StrEnum):
    IDENTITY = "identity"
    PORTAL_REVIEW = "portal_review"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def text(value: object, *, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise FinanceError("Missing or malformed text field.")
    result = value.strip()
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise FinanceError("Control characters are not allowed.")
    return result


def money(value: object, currency: str) -> str:
    # Exact decimal strings only: floats/bools and locale/currency-prefix guesses fail closed.
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", value):
        raise FinanceError("Money must be an exact nonnegative decimal string.")
    precision = {"MYR": 2, "USD": 2, "EUR": 2, "GBP": 2, "SGD": 2, "JPY": 0}
    if currency not in precision:
        raise FinanceError("Unsupported currency precision.")
    try:
        amount = Decimal(value)
        places = max(0, -amount.as_tuple().exponent)
        if not amount.is_finite() or places > precision[currency]:
            raise FinanceError("Invalid money precision.")
        return format(amount.quantize(Decimal(1).scaleb(-precision[currency])), "f")
    except InvalidOperation:
        raise FinanceError("Invalid money.") from None


def iso_date(value: object, *, optional: bool = False) -> str:
    raw = text(value, optional=optional)
    if not raw:
        return ""
    try:
        if date.fromisoformat(raw).isoformat() != raw:
            raise ValueError
    except ValueError:
        raise FinanceError("Expected an ISO calendar date.") from None
    return raw


def choice(value: object, allowed: set[str]) -> str:
    raw = text(value)
    if raw not in allowed:
        raise FinanceError("Unknown controlled value.")
    return raw


@dataclass(frozen=True)
class Property:
    address: str = field(repr=False)
    unit: str = field(default="", repr=False)


@dataclass(frozen=True)
class Entity:
    entity_key: str


@dataclass(frozen=True)
class Provider:
    issuer: str
    portal_url: str = ""
    review_profile: str = ""


@dataclass(frozen=True)
class Account:
    provider: str
    subject: str
    subject_kind: str
    namespace: str
    identifier: str = field(repr=False)
    responsibility: str = "unconfirmed"
    username_ref: str = field(default="", repr=False)
    password_ref: str = field(default="", repr=False)


@dataclass(frozen=True)
class Definition:
    account: str
    charge_key: str
    obligation_type: str
    currency: str
    amount_policy: str
    frequency: str
    evidence_mode: str
    due_mode: str
    active: bool
    fixed_amount: str = ""
    cycle_anchor: str = ""
    due_day: int | None = None
    due_months: tuple[int, ...] = ()
    month_end_rule: str = ""
    oneoff_due: str = ""
    timezone: str = "Asia/Kuala_Lumpur"
    redundant_property: str = ""


@dataclass(frozen=True)
class Evidence:
    source: str
    reference: str = field(repr=False)
    content_hash: str
    kind: str

    @classmethod
    def parse(cls, raw: object) -> Evidence:
        if not isinstance(raw, dict) or set(raw) != {"source", "reference", "content_hash", "kind"}:
            raise FinanceError("Evidence requires source, reference, content_hash and kind.")
        kind = choice(
            raw["kind"],
            {
                "invoice",
                "statement",
                "contract",
                "account_document",
                "property_document",
                "issuer_document",
            },
        )
        sha = text(raw["content_hash"])
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise FinanceError("Evidence requires a SHA-256 content fingerprint.")
        return cls(text(raw["source"]), text(raw["reference"]), sha, kind)


@dataclass(frozen=True)
class Instance:
    definition: str
    occurrence: str
    stage: str
    currency: str
    amount: str = ""
    due_date: str = ""
    period_start: str = ""
    period_end: str = ""
    invoice_reference: str = field(default="", repr=False)
    replaces_reference: str = field(default="", repr=False)
    amount_basis: str = ""
    evidence: tuple[Evidence, ...] = ()


Facts = Property | Entity | Provider | Account | Definition | Instance
TYPES = {
    Kind.PROPERTY: Property,
    Kind.ENTITY: Entity,
    Kind.PROVIDER: Provider,
    Kind.ACCOUNT: Account,
    Kind.DEFINITION: Definition,
    Kind.INSTANCE: Instance,
}


def parse_facts(kind: Kind, raw: dict) -> Facts:
    """Reject unknown fields. Candidate parsing never certifies its result."""
    expected = TYPES[kind].__dataclass_fields__
    if not isinstance(raw, dict) or set(raw) - expected.keys():
        raise FinanceError("Unknown financial fields.")
    try:
        obj = TYPES[kind](**raw)
    except TypeError:
        raise FinanceError("Required financial fields are missing.") from None
    data = asdict(obj)
    if kind == Kind.PROPERTY:
        data = {"address": text(obj.address), "unit": text(obj.unit, optional=True)}
    elif kind == Kind.ENTITY:
        data = {"entity_key": text(obj.entity_key)}
    elif kind == Kind.PROVIDER:
        data = {
            "issuer": text(obj.issuer),
            "portal_url": text(obj.portal_url, optional=True),
            "review_profile": text(obj.review_profile, optional=True),
        }
        if data["portal_url"]:
            origin(data["portal_url"])
            if urlsplit(data["portal_url"]).query:
                raise FinanceError("Portal configuration cannot contain query credentials.")
    elif kind == Kind.ACCOUNT:
        for name in ("provider", "subject", "namespace", "identifier"):
            data[name] = text(data[name])
        data["subject_kind"] = choice(obj.subject_kind, {"property", "personal", "entity"})
        data["responsibility"] = choice(
            obj.responsibility, {"owner", "administration_only", "other", "unconfirmed"}
        )
        for name in ("username_ref", "password_ref"):
            data[name] = text(data[name], optional=True)
            if data[name] and (
                not evaluate_secret_reference(data[name]).allowed
                or "?" in data[name]
                or "#" in data[name]
            ):
                raise FinanceError("Invalid credential locator.")
    elif kind == Kind.DEFINITION:
        for name in ("account", "charge_key", "obligation_type"):
            data[name] = text(data[name])
        data["currency"] = choice(obj.currency, {"MYR", "USD", "EUR", "GBP", "SGD", "JPY"})
        data["amount_policy"] = choice(obj.amount_policy, {"source_defined", "fixed_contract"})
        data["frequency"] = choice(
            obj.frequency,
            {
                "once",
                "monthly",
                "quarterly",
                "semiannual",
                "annual",
                "irregular",
                "statement_driven",
            },
        )
        data["evidence_mode"] = choice(
            obj.evidence_mode,
            {"issued_bill", "statement", "contract_occurrence", "manual_document"},
        )
        data["due_mode"] = choice(
            obj.due_mode, {"from_document", "calendar_pattern", "oneoff_date"}
        )
        if type(obj.active) is not bool:
            raise FinanceError("Active must be a boolean.")
        if obj.amount_policy == "fixed_contract":
            data["fixed_amount"] = money(obj.fixed_amount, obj.currency)
        elif obj.fixed_amount:
            raise FinanceError("A source-defined amount cannot contain a fixed amount.")
        data["cycle_anchor"] = iso_date(obj.cycle_anchor, optional=True)
        data["oneoff_due"] = iso_date(obj.oneoff_due, optional=True)
        data["redundant_property"] = text(obj.redundant_property, optional=True)
        if obj.due_day is not None and (type(obj.due_day) is not int or not 1 <= obj.due_day <= 31):
            raise FinanceError("Invalid due day.")
        if (
            not isinstance(obj.due_months, (tuple, list))
            or any(type(m) is not int or not 1 <= m <= 12 for m in obj.due_months)
            or len(set(obj.due_months)) != len(obj.due_months)
        ):
            raise FinanceError("Invalid due months.")
        data["due_months"] = tuple(sorted(obj.due_months))
        data["month_end_rule"] = text(obj.month_end_rule, optional=True)
        if data["month_end_rule"] not in {"", "last_valid_day", "requires_review"}:
            raise FinanceError("Unsupported month-end handling.")
        if obj.due_mode != "calendar_pattern" and (
            obj.due_day is not None or obj.due_months or obj.cycle_anchor or obj.month_end_rule
        ):
            raise FinanceError("Unused calendar facts are ambiguous.")
        if obj.due_mode != "oneoff_date" and obj.oneoff_due:
            raise FinanceError("Unused one-off due date is ambiguous.")
        if obj.due_mode == "calendar_pattern":
            if obj.due_day is None or not obj.cycle_anchor:
                raise FinanceError("Calendar patterns need a due day and cycle anchor.")
            counts = {"quarterly": 4, "semiannual": 2, "annual": 1}
            if obj.frequency not in {"monthly", *counts}:
                raise FinanceError("Unsupported calendar cadence.")
            if obj.frequency in counts and len(obj.due_months) != counts[obj.frequency]:
                raise FinanceError("Cadence and due months conflict.")
            if obj.due_day > 28 and obj.month_end_rule not in {"last_valid_day", "requires_review"}:
                raise FinanceError("Month-end handling is required.")
        if obj.due_mode == "oneoff_date" and (obj.frequency != "once" or not obj.oneoff_due):
            raise FinanceError("One-off due mode needs a one-off date.")
        try:
            ZoneInfo(text(obj.timezone))
        except (ValueError, KeyError):
            raise FinanceError("Invalid billing timezone.") from None
    else:
        data["definition"] = text(obj.definition)
        data["occurrence"] = text(obj.occurrence)
        data["stage"] = choice(obj.stage, {"expected", "issued"})
        data["currency"] = choice(obj.currency, {"MYR", "USD", "EUR", "GBP", "SGD", "JPY"})
        for name in ("due_date", "period_start", "period_end"):
            data[name] = iso_date(data[name], optional=True)
        if bool(obj.period_start) != bool(obj.period_end) or (
            obj.period_start and obj.period_start > obj.period_end
        ):
            raise FinanceError("Invalid billing period.")
        for name in ("invoice_reference", "replaces_reference"):
            data[name] = text(data[name], optional=True)
        if obj.stage == "expected":
            if obj.amount or obj.invoice_reference or obj.replaces_reference or obj.amount_basis:
                raise FinanceError("Expected occurrences cannot carry payable facts.")
        else:
            data["amount"] = money(obj.amount, obj.currency)
            data["amount_basis"] = choice(
                obj.amount_basis,
                {"invoice_total", "statement_balance", "minimum_due", "contract_charge"},
            )
        if not isinstance(obj.evidence, (list, tuple)):
            raise FinanceError("Evidence must be an explicit collection.")
        data["evidence"] = tuple(
            Evidence.parse(e) if not isinstance(e, Evidence) else Evidence.parse(asdict(e))
            for e in obj.evidence
        )
        if obj.stage == "issued" and (
            not data["evidence"]
            or any(e.kind not in {"invoice", "statement", "contract"} for e in data["evidence"])
        ):
            raise FinanceError("Issued occurrences require primary obligation evidence.")
    return TYPES[kind](**data)


@dataclass(frozen=True)
class Locator:
    system: str
    database: str
    data_source: str
    page: str

    @property
    def key(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class Candidate:
    kind: Kind
    locator: Locator
    facts: Facts = field(repr=False)
    source_revision: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "kind": self.kind,
                "locator": asdict(self.locator),
                "facts": asdict(self.facts),
                "schema": self.schema_version,
            }
        )


def dependencies(facts: Facts) -> tuple[str, ...]:
    if isinstance(facts, Account):
        return (facts.provider, facts.subject)
    if isinstance(facts, Definition):
        return (facts.account,)
    if isinstance(facts, Instance):
        return (facts.definition,)
    return ()


def semantic_key(kind: Kind, facts: Facts) -> str:
    if isinstance(facts, Property):
        values = (facts.address, facts.unit)
    elif isinstance(facts, Entity):
        values = (facts.entity_key,)
    elif isinstance(facts, Provider):
        values = (facts.issuer,)
    elif isinstance(facts, Account):
        values = (facts.provider, facts.subject, facts.namespace, facts.identifier)
    elif isinstance(facts, Definition):
        values = (facts.account, facts.charge_key)
    else:
        values = (facts.definition, facts.occurrence)
    return digest((kind, values))


def origin(url: str) -> str:
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        raise FinanceError("Malformed portal URL.") from None
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.fragment
        or port not in (None, 443)
    ):
        raise FinanceError("Portal must use a credential-free HTTPS origin.")
    return f"https://{p.hostname.lower()}"


@dataclass(frozen=True)
class ReviewProfile:
    id: str
    version: str
    allowed_origins: tuple[str, ...]
    login_username_selector: str
    login_password_selector: str
    login_submit_selector: str = ""
    auth_success_selector: str = ""
    auth_success_url_contains: str = ""
    intent: str = "review_bill"

    def validate(self) -> None:
        for v in (
            self.id,
            self.version,
            self.login_username_selector,
            self.login_password_selector,
        ):
            text(v)
        for v in (
            self.login_submit_selector,
            self.auth_success_selector,
            self.auth_success_url_contains,
        ):
            text(v, optional=True)
        if not isinstance(self.allowed_origins, tuple):
            raise FinanceError("Profile origins must be explicit.")
        if self.intent != "review_bill" or not self.allowed_origins:
            raise FinanceError("Only a bounded review profile is supported.")
        if any(origin(o) != o for o in self.allowed_origins):
            raise FinanceError("Profiles require exact HTTPS origins.")
        if not self.auth_success_selector and not self.auth_success_url_contains:
            raise FinanceError("Authentication verification is required.")

    @property
    def fingerprint(self) -> str:
        self.validate()
        return digest(asdict(self))
