"""D03 typed ingestion, owner certification, instances and operational trust boundaries."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from tests.finance_fixture import ATTEST, PROOF, Approve, catalog_fixture, revise
from tests.test_ops_act import PORTAL_URL, ScriptedBrowser, _harness, _nothing_ran
from tests.test_ops_approvals import _email
from wally.exceptions import AuthorizationError
from wally.finance import migration
from wally.finance.models import (
    Candidate,
    FinanceError,
    Kind,
    Locator,
    Scope,
    money,
    parse_facts,
)
from wally.finance.notion import FieldMapping, NotionFinanceReader, SourceMapping
from wally.finance.service import FinanceService
from wally.finance.store import FinanceStore
from wally.knowledge.registry import KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass
from wally.models.ops import ExecutionStatus, MatterStatus
from wally.models.principal import Capability, Principal, RequestContext
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.runtime.principals import ChannelPolicy, PrincipalAuthority

NOW = datetime(2026, 8, 16, tzinfo=UTC)


def uid(i):
    return str(UUID(int=i))


class NotionFixture:
    def __init__(self):
        facts = {
            Kind.PROPERTY: {"address": "Fixture property", "unit": "001"},
            Kind.PROVIDER: {
                "issuer": "acme-power",
                "portal_url": PORTAL_URL,
                "review_profile": "utility",
            },
            Kind.ACCOUNT: {
                "provider": uid(202),
                "subject": uid(201),
                "subject_kind": "property",
                "namespace": "electricity-customer",
                "identifier": "000123456789",
                "responsibility": "owner",
                "username_ref": "op://Personal/AcmePower/username",
                "password_ref": "op://Personal/AcmePower/password",
            },
            Kind.DEFINITION: {
                "account": uid(203),
                "charge_key": "electricity",
                "obligation_type": "utility",
                "currency": "MYR",
                "amount_policy": "source_defined",
                "frequency": "monthly",
                "evidence_mode": "issued_bill",
                "due_mode": "from_document",
                "active": True,
            },
        }
        self.schemas = {}
        self.rows = {}
        self.sources = []
        self.page_source = {}
        self.calls = []
        self.inaccessible = set()
        self.bad_cursor = False
        self.relation_items = None
        for n, (kind, raw) in enumerate(facts.items(), 1):
            fields = {}
            properties = {}
            schema = {}
            for key, value in raw.items():
                typ = "rich_text"
                target = ""
                if key in {"provider", "subject", "account"}:
                    typ = "relation"
                    target = uid(int(UUID(value)) - 100)
                elif key == "active":
                    typ = "checkbox"
                elif key == "portal_url":
                    typ = "url"
                fields[key] = FieldMapping(f"id-{key}", typ, target_data_source=target)
                schema[key] = {"id": f"id-{key}", "type": typ}
                if typ == "relation":
                    schema[key][typ] = {"data_source_id": target}
                    val = [{"id": value}]
                elif typ == "rich_text":
                    val = [{"type": "text", "plain_text": value, "text": {"content": value}}]
                else:
                    val = value
                properties[key] = {"id": f"id-{key}", "type": typ, typ: val}
                if typ == "relation":
                    properties[key]["has_more"] = False
            source = SourceMapping(uid(n), uid(100 + n), kind, fields)
            self.sources.append(source)
            self.schemas[source.data_source_id] = {
                "id": source.data_source_id,
                "parent": {"database_id": source.database_id},
                "properties": schema,
            }
            page = {
                "object": "page",
                "id": uid(200 + n),
                "archived": False,
                "parent": {"data_source_id": source.data_source_id},
                "properties": properties,
                "last_edited_time": "2026-08-01T00:00:00Z",
            }
            self.rows[source.data_source_id] = [page]
            self.page_source[page["id"]] = source.data_source_id
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1", transport=httpx.MockTransport(self.handle)
        )
        self.reader = NotionFinanceReader(self.client)

    def handle(self, request):
        self.calls.append((request.method, request.url.path, request.headers["Notion-Version"]))
        path = request.url.path.removeprefix("/v1/").split("/")
        if path[0] == "databases":
            source = next(s for s in self.sources if s.database_id == path[1])
            data = {"id": path[1], "data_sources": [{"id": source.data_source_id}]}
        elif path[0] == "data_sources":
            if len(path) == 2:
                data = self.schemas[path[1]]
                if request.method == "PATCH":
                    for name, prop in json.loads(request.content)["properties"].items():
                        data["properties"][name] = {**prop, "id": "new-" + name}
            else:
                body = json.loads(request.content)
                rows = self.rows[path[1]]
                offset = int(body.get("start_cursor", "0"))
                more = offset + 1 < len(rows)
                data = {
                    "results": rows[offset : offset + 1],
                    "has_more": more,
                    "next_cursor": None if not more or self.bad_cursor else str(offset + 1),
                }
        elif path[0] == "pages":
            if path[1] in self.inaccessible:
                return httpx.Response(403, json={"message": "sensitive response must not escape"})
            if len(path) > 2:
                data = self.relation_items or {
                    "results": [],
                    "has_more": False,
                    "next_cursor": None,
                }
            else:
                data = next(p for p in self.rows[self.page_source[path[1]]] if p["id"] == path[1])
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=data)

    def catalog(
        self,
        tmp_path,
        *,
        designated=True,
        classification=KnowledgeClass.OPERATIONAL,
        role="finance",
    ):
        registry = KnowledgeRegistry(tmp_path / "registry.db")
        store = FinanceStore(tmp_path / "finance.db")
        for source in self.sources:
            if classification != KnowledgeClass.PENDING:
                registry.import_approved(
                    database_id=source.database_id,
                    name=source.kind.value,
                    classification=classification,
                    title_property="Name",
                    role=role,
                    approved_by="fixture",
                )
            if designated:
                store.designate(source.key, source.fingerprint, {"principal": "fixture"})
        return FinanceService(
            store,
            authority=PrincipalAuthority(),
            approval=Approve(),
            registry=registry,
            reader=self.reader,
            sources=lambda: tuple(self.sources),
        )

    def page(self, kind):
        source = next(s for s in self.sources if s.kind == kind)
        return self.rows[source.data_source_id][0]

    def set_text(self, kind, key, value):
        self.page(kind)["properties"][key]["rich_text"][0]["plain_text"] = value


def test_complete_notion_path_provisional_and_leading_zeros(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    records = catalog.refresh()
    account = next(r for r in records if r.candidate.kind == Kind.ACCOUNT)
    assert account.candidate.facts.identifier == "000123456789"
    assert account.id.startswith("fin_") and account.id != account.candidate.locator.page
    assert all(row["state"] == "draft" for row in catalog.status())
    assert all(version == "2025-09-03" for _, _, version in fixture.calls)
    with pytest.raises(FinanceError):
        catalog.binding(account.id)


@pytest.mark.parametrize(
    "classification,role,designated",
    [
        (KnowledgeClass.GOVERNANCE, "finance", True),
        (KnowledgeClass.PENDING, "finance", True),
        (KnowledgeClass.OPERATIONAL, "general", True),
        (KnowledgeClass.OPERATIONAL, "finance", False),
    ],
)
def test_all_source_trust_gates_fail_closed(tmp_path, classification, role, designated):
    fixture = NotionFixture()
    catalog = fixture.catalog(
        tmp_path, classification=classification, role=role, designated=designated
    )
    assert catalog.refresh() == []
    assert all(row["state"] == "needs_attention" for row in catalog.status())
    assert catalog.tracked_instances() == []
    assert not fixture.calls


def test_property_rename_is_not_material_but_recreation_invalidates(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    prop = next(r for r in catalog.refresh() if r.candidate.kind == Kind.PROPERTY)
    catalog.certify(
        prop.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    source = fixture.sources[0]
    schema = fixture.schemas[source.data_source_id]["properties"]
    schema["Renamed"] = schema.pop("address")
    page = fixture.page(Kind.PROPERTY)
    page["properties"]["Renamed"] = page["properties"].pop("address")
    catalog.refresh()
    assert catalog.store.get(prop.id).version == 1
    catalog.binding(prop.id)
    schema["Renamed"]["id"] = "recreated-id"
    catalog.refresh()
    assert catalog.store.get(prop.id).invalid
    with pytest.raises(FinanceError):
        catalog.binding(prop.id)
    schema["Renamed"]["id"] = "id-address"
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(prop.id)


@pytest.mark.parametrize(
    "failure",
    [
        "schema_type",
        "page_type",
        "relation_partial",
        "relation_multi",
        "inaccessible",
        "wrong_target",
        "unknown_kind",
    ],
)
def test_malformed_or_incomplete_graph_is_rejected(tmp_path, failure):
    fixture = NotionFixture()
    source = fixture.sources[2]
    relation = fixture.page(Kind.ACCOUNT)["properties"]["provider"]
    if failure == "schema_type":
        fixture.schemas[source.data_source_id]["properties"]["identifier"]["type"] = "number"
    elif failure == "page_type":
        fixture.page(Kind.ACCOUNT)["properties"]["identifier"]["type"] = "number"
    elif failure == "relation_partial":
        relation["has_more"] = True
        fixture.relation_items = {"results": [], "has_more": True, "next_cursor": None}
    elif failure == "relation_multi":
        relation["relation"].append({"id": uid(201)})
    elif failure == "inaccessible":
        fixture.inaccessible.add(uid(202))
    elif failure == "wrong_target":
        relation["relation"] = [{"id": uid(201)}]
    else:
        fixture.sources[2] = replace(source, role="general")
    catalog = fixture.catalog(tmp_path)
    records = catalog.refresh()
    assert not any(r.candidate.kind in {Kind.ACCOUNT, Kind.DEFINITION} for r in records)
    assert catalog.tracked_instances() == []
    assert catalog.store.read_issues()


def test_query_pagination_and_duplicate_identity(tmp_path):
    fixture = NotionFixture()
    page = copy.deepcopy(fixture.page(Kind.PROPERTY))
    page["id"] = uid(299)
    fixture.rows[fixture.sources[0].data_source_id].append(page)
    fixture.page_source[page["id"]] = fixture.sources[0].data_source_id
    catalog = fixture.catalog(tmp_path)
    records = catalog.refresh()
    assert len(records) == 5
    for record in records:
        if record.candidate.kind == Kind.PROPERTY:
            with pytest.raises(FinanceError, match="Duplicate"):
                catalog.certify(
                    record.id,
                    Scope.IDENTITY,
                    evidence=PROOF,
                    attestations=ATTEST,
                    context=catalog.authority.issue("cli"),
                )
    fixture.bad_cursor = True
    catalog.refresh()
    assert all(
        catalog.store.get(r.id).invalid for r in records if r.candidate.kind == Kind.PROPERTY
    )
    assert any(i.reason == "source_invalid" for i in catalog.store.read_issues())


def test_relation_hydration_uses_full_property_items(tmp_path):
    fixture = NotionFixture()
    relation = fixture.page(Kind.ACCOUNT)["properties"]["provider"]
    relation["has_more"] = True
    relation["relation"] = []
    fixture.relation_items = {
        "results": [{"type": "relation", "relation": {"id": uid(202)}}],
        "has_more": False,
        "next_cursor": None,
    }
    catalog = fixture.catalog(tmp_path)
    assert len(catalog.refresh()) == 4
    assert any("/properties/" in path for _, path, _ in fixture.calls)


@pytest.mark.parametrize(
    "value,currency",
    [
        (1.1, "MYR"),
        (True, "MYR"),
        ("1.001", "MYR"),
        ("1,000.00", "MYR"),
        ("-1.00", "MYR"),
        ("NaN", "MYR"),
        ("1.00", "JPY"),
        ("01.00", "MYR"),
    ],
)
def test_decimal_money_never_guesses(value, currency):
    with pytest.raises(FinanceError):
        money(value, currency)


def test_owner_authority_is_narrow_even_if_remote_registered_with_capability(tmp_path):
    authority = PrincipalAuthority(
        {
            "cli": ChannelPolicy("local_terminal", frozenset(Capability)),
            "telegram": ChannelPolicy("bot_auth", frozenset(Capability)),
            "chatgpt": ChannelPolicy("local_terminal", frozenset(Capability)),
        }
    )
    catalog, chain, _ = catalog_fixture(tmp_path, authority)
    for channel in ("telegram", "chatgpt"):
        with pytest.raises(AuthorizationError):
            catalog.certify(
                chain["property"],
                Scope.IDENTITY,
                evidence=PROOF,
                attestations=ATTEST,
                context=authority.issue(channel),
            )
    forged = RequestContext(Principal("owner", "cli", "local_terminal"), "forged")
    with pytest.raises(AuthorizationError):
        catalog.register_local(Kind.ENTITY, {"entity_key": "owner"}, context=forged)


def test_candidate_checkbox_and_payload_boolean_cannot_certify(tmp_path):
    fixture = NotionFixture()
    fixture.page(Kind.PROPERTY)["properties"]["Verified"] = {
        "id": "verified",
        "type": "checkbox",
        "checkbox": True,
    }
    catalog = fixture.catalog(tmp_path)
    record = catalog.refresh()[0]
    with pytest.raises(FinanceError):
        catalog.binding(record.id)
    with pytest.raises(FinanceError):
        parse_facts(Kind.PROPERTY, {"address": "x", "verified": True})


def test_uncertified_dependencies_and_redundant_property_block(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    definition = next(r for r in catalog.refresh() if r.candidate.kind == Kind.DEFINITION)
    with pytest.raises(FinanceError):
        catalog.certify(
            definition.id,
            Scope.IDENTITY,
            evidence=PROOF,
            attestations=ATTEST,
            context=catalog.authority.issue("cli"),
        )
    local, chain, _ = catalog_fixture(tmp_path / "local", PrincipalAuthority())
    with pytest.raises(FinanceError, match="Redundant"):
        revise(local, chain["definition"], redundant_property=chain["provider"])


def test_material_edit_revert_and_dependency_recertification_never_resurrect(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    original = catalog.store.get(chain["account"])
    revise(catalog, original.id, identifier="000999")
    revise(catalog, original.id, identifier=original.candidate.facts.identifier)
    assert catalog.store.get(original.id).version == 3
    with pytest.raises(FinanceError):
        catalog.binding(chain["definition"])
    catalog.certify(
        original.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    with pytest.raises(FinanceError):
        catalog.binding(chain["definition"])


def test_certification_candidate_drift_after_prompt_rejected(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    prop = next(r for r in catalog.refresh() if r.candidate.kind == Kind.PROPERTY)
    catalog.approval.on_prompt = lambda: fixture.set_text(Kind.PROPERTY, "unit", "002")
    with pytest.raises(FinanceError, match="changed"):
        catalog.certify(
            prop.id,
            Scope.IDENTITY,
            evidence=PROOF,
            attestations=ATTEST,
            context=catalog.authority.issue("cli"),
        )
    assert catalog.store.certificate(prop.id, Scope.IDENTITY) is None


def test_scope_separation_profile_drift_and_auth_only_acceptance(tmp_path):
    harness = _harness(tmp_path)
    catalog = harness.service._finance_catalog
    definition = harness.asset_id
    # Acceptance cannot be inferred from a certificate/Notion checkbox/claimed human outcome.
    with catalog.store.connect() as c:
        c.execute("DELETE FROM finance_auth_acceptance")
    report = harness.execute()
    assert report.blocked
    _nothing_ran(harness)
    report = harness.act.execute(harness.proposal().id, context=harness.ctx(), acceptance=True)
    assert report.execution.status == ExecutionStatus.VERIFIED_SUCCESS
    binding = catalog.binding(definition, Scope.PORTAL_REVIEW)
    assert catalog.store.accepted(definition, binding)
    catalog.enable(definition, Scope.PORTAL_REVIEW, context=harness.ctx())
    profiles = catalog.profiles()
    profiles["utility"] = replace(profiles["utility"], version="2")
    with pytest.raises(FinanceError):
        catalog.review_plan(definition)
    catalog.binding(definition)  # tracking still works


def test_profile_drift_during_execution_prompt_resolves_no_credentials(tmp_path):
    harness = _harness(tmp_path)
    catalog = harness.service._finance_catalog
    profiles = catalog.profiles()
    harness.approval.on_prompt = lambda: profiles.update(
        utility=replace(profiles["utility"], login_password_selector="#changed")
    )
    report = harness.execute()
    assert report.execution.status == ExecutionStatus.PREFLIGHT_FAILED
    _nothing_ran(harness)


@pytest.mark.parametrize(
    "reference",
    [
        "plaintext-secret",
        "op://vault/item/password?token=secret",
        "keychain://one",
        "op://vault/item/password\n",
    ],
)
def test_malformed_secret_locators_rejected(reference):
    with pytest.raises(FinanceError):
        parse_facts(
            Kind.ACCOUNT,
            {
                "provider": "a",
                "subject": "b",
                "subject_kind": "property",
                "namespace": "x",
                "identifier": "001",
                "password_ref": reference,
            },
        )


def test_instance_reissue_variable_amount_same_thread_and_oneoff(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    instance = catalog.store.get(chain["instance"])
    corrected = revise(
        catalog,
        instance.id,
        amount="123.45",
        invoice_reference="INV001-R",
        replaces_reference="INV001",
    )
    assert corrected.id == instance.id and corrected.version == 2
    with pytest.raises(FinanceError):
        catalog.binding(corrected.id)
    catalog.certify(
        corrected.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    facts = asdict(corrected.candidate.facts)
    with pytest.raises(FinanceError):
        catalog.register_local(
            Kind.INSTANCE,
            {**facts, "occurrence": "different"},
            context=catalog.authority.issue("cli"),
        )
    different = catalog.register_local(
        Kind.INSTANCE,
        {**facts, "occurrence": "2026-09", "invoice_reference": "INV002", "replaces_reference": ""},
        context=catalog.authority.issue("cli"),
    )
    assert different.id != instance.id  # equal amounts do not merge
    # Email threads are evidence transport only: each explicit owner occurrence keeps its ID.
    with pytest.raises(FinanceError):
        parse_facts(Kind.INSTANCE, {**facts, "email_thread": "same-thread"})
    oneoff = catalog.register_local(
        Kind.DEFINITION,
        {
            **asdict(catalog.store.get(chain["definition"]).candidate.facts),
            "charge_key": "oneoff",
            "frequency": "once",
            "due_mode": "oneoff_date",
            "oneoff_due": "2026-10-10",
        },
        context=catalog.authority.issue("cli"),
    )
    catalog.certify(
        oneoff.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    catalog.enable(oneoff.id, Scope.IDENTITY, context=catalog.authority.issue("cli"))
    record = catalog.register_local(
        Kind.INSTANCE,
        {
            **facts,
            "definition": oneoff.id,
            "occurrence": "oneoff:notice-1",
            "invoice_reference": "ONEOFF1",
            "replaces_reference": "",
        },
        context=catalog.authority.issue("cli"),
    )
    assert record.candidate.facts.definition == oneoff.id


def test_expected_occurrence_has_no_amount_and_fixed_contract_is_exact(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    facts = {
        "definition": chain["definition"],
        "occurrence": "2026-10",
        "stage": "expected",
        "currency": "MYR",
    }
    expected = catalog.register_local(Kind.INSTANCE, facts, context=catalog.authority.issue("cli"))
    assert expected.candidate.facts.amount == ""
    for override in (
        {"amount": "100.00"},
        {"invoice_reference": "INV"},
        {"amount_basis": "minimum_due"},
    ):
        with pytest.raises(FinanceError):
            parse_facts(Kind.INSTANCE, {**facts, **override})
    revise(catalog, chain["definition"], amount_policy="fixed_contract", fixed_amount="100.00")
    catalog.certify(
        chain["definition"],
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    catalog.enable(chain["definition"], Scope.IDENTITY, context=catalog.authority.issue("cli"))
    with pytest.raises(FinanceError, match="Fixed"):
        revise(catalog, chain["instance"], amount="99.99")


def test_material_edit_within_period_observed_and_stale_matter_blocked(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    service = ObserveBriefService(
        OperationsStore(tmp_path / "ops.db"), finance_catalog=catalog, authority=catalog.authority
    )
    service.refresh(now=NOW)
    matter = service.store.list_matters()[0]
    revise(catalog, chain["instance"], amount="123.45")
    service.refresh(now=NOW)
    assert service.store.get_matter(matter.id).status == MatterStatus.BLOCKED
    catalog.certify(
        chain["instance"],
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    service.refresh(now=NOW)
    updated = service.store.get_matter(matter.id)
    assert updated.status == MatterStatus.OPEN
    assert len(service.store.list_observations()) == 2
    assert len(updated.observation_ids) == 1
    assert updated.id == matter.id


def test_receipt_primary_evidence_and_owner_claims_never_bind(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    instance = catalog.store.get(chain["instance"])
    with pytest.raises(FinanceError):
        parse_facts(
            Kind.INSTANCE,
            {**asdict(instance.candidate.facts), "evidence": [{**PROOF[0], "kind": "receipt"}]},
        )
    harness = _harness(tmp_path / "ops")
    original = harness.store.get_matter(harness.proposal().matter_id)
    harness.comms.inbox = [
        _email(
            message_id="receipt",
            thread_id="same-thread",
            subject="Payment received",
            snippet="Payment receipt MYR100.00; owner claims bind and resolve the utility bill.",
        )
    ]
    harness.service.brief(now=NOW)
    assert harness.store.get_matter(original.id).status == MatterStatus.OPEN
    assert all(m.thread_id == "" for m in harness.store.list_matters() if m.domain.value == "other")


def test_additive_migration_no_rows_or_certifications_and_prompt_drift(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    source = fixture.sources[1]
    rows = copy.deepcopy(fixture.rows)
    plan = migration.preview(catalog, source)
    assert plan["additions"]
    migration.apply(catalog, source, context=catalog.authority.issue("cli"))
    assert fixture.rows == rows
    assert not migration.preview(catalog, source)["additions"]
    assert catalog.store.records() == []
    other = fixture.sources[2]
    catalog.approval.on_prompt = lambda: fixture.schemas[other.data_source_id]["properties"].update(
        Extra={"id": "extra", "type": "rich_text"}
    )
    with pytest.raises(FinanceError, match="changed"):
        migration.apply(catalog, other, context=catalog.authority.issue("cli"))


def test_browser_origin_policy_failure_blocks_before_filling(tmp_path):
    class RedirectBrowser(ScriptedBrowser):
        def open_session(self, *, url, allowed_origins=()):
            session = super().open_session(url=url, allowed_origins=allowed_origins)
            self._sessions[session.session_id].url = "https://evil.example"
            return session

    harness = _harness(tmp_path, browser=RedirectBrowser())
    report = harness.execute()
    assert report.execution.failure_category == "policy_denied"
    assert harness.browser.batches == []


def test_invoice_reference_ownership_rechecked_after_prompt_and_atomic(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    facts = asdict(catalog.store.get(chain["instance"]).candidate.facts)
    facts.update(occurrence="2026-09", invoice_reference="CONCURRENT", replaces_reference="")
    captured = []

    def competing_intake():
        catalog.approval.on_prompt = None
        captured.append(
            catalog.register_local(
                Kind.INSTANCE,
                {**facts, "occurrence": "2026-10"},
                context=catalog.authority.issue("cli"),
            )
        )

    catalog.approval.on_prompt = competing_intake
    with pytest.raises(FinanceError, match="already belongs"):
        catalog.register_local(Kind.INSTANCE, facts, context=catalog.authority.issue("cli"))
    assert len(captured) == 1
    # The persistence boundary itself also rejects another locator, under a write lock.
    candidate = Candidate(
        Kind.INSTANCE, Locator("local_owner", "", "", "other"), parse_facts(Kind.INSTANCE, facts)
    )
    with pytest.raises(FinanceError):
        catalog.store.register(candidate)
    with catalog.store.connect() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM finance_reference_owners WHERE instance_id=?",
                (captured[0].id,),
            ).fetchone()[0]
            == 1
        )


def test_restricted_notion_url_slug_and_hot_reload_never_expose_customer_id():
    from wally.adapters.notion.adapter import NotionKnowledgeAdapter, _asset_to_dict
    from wally.config.loader import NotionDatabaseConfig

    db = uid(123)
    adapter = NotionKnowledgeAdapter(
        api_key="fixture-not-a-live-key",
        databases=[
            NotionDatabaseConfig(
                name="accounts",
                id=db,
                role="general",
                readable=True,
                writable=True,
                knowledge_class=KnowledgeClass.OPERATIONAL,
                title_property="Name",
                content_property="Notes",
            )
        ],
    )
    restrictions = set()
    adapter.restrict_finance_sources(lambda: restrictions)
    page = {
        "id": uid(456),
        "parent": {"database_id": db},
        "url": "https://www.notion.so/Customer-000123456789-" + uid(456),
        "properties": {
            "Name": {"title": [{"plain_text": "Customer 000123456789"}]},
            "Notes": {"rich_text": [{"plain_text": "000123456789"}]},
        },
    }
    restrictions.add(db)  # config/source added while process is already running
    try:
        asset = adapter._page_to_asset(page)
        assert asset.url is None
        assert "000123456789" not in json.dumps(_asset_to_dict(asset))
    finally:
        adapter.close()


def test_plain_recording_browser_cannot_mint_auth_acceptance(tmp_path):
    from wally.adapters.browser.recording import RecordingBrowserAdapter

    harness = _harness(tmp_path, browser=RecordingBrowserAdapter())
    catalog = harness.service._finance_catalog
    with catalog.store.connect() as c:
        c.execute("DELETE FROM finance_auth_acceptance")
    report = harness.act.execute(harness.proposal().id, context=harness.ctx(), acceptance=True)
    assert report.execution.failure_category == "live_auth_required"
    assert not catalog.store.accepted(
        harness.asset_id, catalog.binding(harness.asset_id, Scope.PORTAL_REVIEW)
    )
    assert harness.secrets.resolve_calls == []
    assert harness.browser.opened_urls == []


def test_playwright_policy_installed_before_navigation_blocks_websockets_and_redirects():
    from types import SimpleNamespace

    from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter

    calls = []

    class FakeContext:
        def route(self, pattern, callback):
            calls.append("http-policy")
            self.callback = callback

        def route_web_socket(self, pattern, callback):
            calls.append("websocket-policy")
            self.ws_callback = callback

    class FakePage:
        def __init__(self):
            self.context = FakeContext()
            self.url = "about:blank"
            self._wally_restricted = False

        def goto(self, url, **kwargs):
            calls.append("navigate")
            route = SimpleNamespace(
                request=SimpleNamespace(url="https://evil.example/redirect"),
                continue_=lambda: calls.append("continued"),
                abort=lambda: calls.append("blocked"),
            )
            self.context.callback(route)
            self.url = url

        def close(self):
            calls.append("closed")

    page = FakePage()
    adapter = PlaywrightBrowserAdapter()
    adapter._browser = SimpleNamespace(new_page=lambda **kw: (calls.append(kw), page)[1])
    session = adapter.open_session(url=PORTAL_URL, allowed_origins=("https://portal.acme.example",))
    assert calls[0] == {"service_workers": "block"}
    assert calls[1:5] == ["http-policy", "websocket-policy", "navigate", "blocked"]
    page.context.ws_callback(SimpleNamespace(close=lambda: calls.append("websocket-closed")))
    assert calls[-1] == "websocket-closed"
    page.url = "https://evil.example/final"
    with pytest.raises(FinanceError):
        adapter.restrict_origins(session.session_id, ("https://portal.acme.example",))


def test_missing_profile_reverting_does_not_restore_portal_certificate(tmp_path):
    catalog, chain, profiles = catalog_fixture(tmp_path, PrincipalAuthority())
    profile = profiles.pop("utility")
    with pytest.raises(FinanceError):
        catalog.review_plan(chain["definition"])
    profiles["utility"] = profile
    with pytest.raises(FinanceError):
        catalog.review_plan(chain["definition"])
    catalog.binding(chain["definition"])  # identity unaffected


def test_same_thread_primary_evidence_does_not_merge_owner_validated_occurrences(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    first = catalog.store.get(chain["instance"])
    facts = asdict(first.candidate.facts)
    facts.update(
        occurrence="2026-09",
        invoice_reference="INV002",
        evidence=[
            {
                **PROOF[0],
                "source": "primary_document",
                "reference": "email:shared-thread:invoice-2",
                "kind": "invoice",
            }
        ],
    )
    second = catalog.register_local(Kind.INSTANCE, facts, context=catalog.authority.issue("cli"))
    catalog.certify(
        second.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    service = ObserveBriefService(
        OperationsStore(tmp_path / "ops.db"), finance_catalog=catalog, authority=catalog.authority
    )
    service.refresh(now=NOW)
    assert len(service.store.list_matters()) == 2
    assert all(m.thread_id == "" for m in service.store.list_matters())
    assert {m.fingerprint for m in service.store.list_matters()} == {
        f"matter:finance-instance:{first.id}",
        f"matter:finance-instance:{second.id}",
    }


def test_observed_source_gate_loss_cannot_revert_into_old_authority(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    prop = next(r for r in catalog.refresh() if r.candidate.kind == Kind.PROPERTY)
    ctx = catalog.authority.issue("cli")
    catalog.certify(prop.id, Scope.IDENTITY, evidence=PROOF, attestations=ATTEST, context=ctx)
    catalog.registry.approve(
        prop.candidate.locator.database, KnowledgeClass.GOVERNANCE, approved_by="owner"
    )
    with pytest.raises(FinanceError):
        catalog.binding(prop.id)
    catalog.registry.approve(
        prop.candidate.locator.database, KnowledgeClass.OPERATIONAL, approved_by="owner"
    )
    with pytest.raises(FinanceError):
        catalog.binding(prop.id)


def test_source_mapping_drift_during_designation_confirmation_is_rejected(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path, designated=False)
    source = fixture.sources[0]
    catalog.approval.on_prompt = lambda: source.constants.update(unit="changed")
    with pytest.raises(FinanceError):
        catalog.designate(source.key, context=catalog.authority.issue("cli"))
    assert not catalog.store.designated(source.key, source.fingerprint)


def test_owner_review_summary_masks_identifiers_and_secret_values(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    output = json.dumps(catalog.status()) + "".join(catalog.approval.prompts)
    assert "000123456789" not in output
    assert "…6789" in output or "\\u20266789" in output
    assert "canary-user-7f3a91" not in output
    assert "canary-pass-9c1e44" not in output


def test_early_catalog_failure_permanently_invalidates_local_portal_scope(tmp_path):
    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    original = catalog.sources

    def unavailable():
        raise FinanceError("Invalid reviewed configuration")

    catalog.sources = unavailable
    with pytest.raises(FinanceError):
        catalog.review_plan(chain["definition"])
    catalog.sources = original
    with pytest.raises(FinanceError):
        catalog.review_plan(chain["definition"])
    catalog.binding(chain["definition"])


def test_malformed_profile_config_preserves_source_identity_scope(tmp_path):
    from wally.finance.config import load_profiles, load_sources

    catalog, chain, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    path = tmp_path / "finance.yaml"
    path.write_text("schema_version: 1\nsources: []\nprofiles: [{id: invalid}]\n")
    catalog.sources = lambda: load_sources(path)
    catalog.profiles = lambda: load_profiles(path)
    assert catalog.tracked_instances()
    with pytest.raises(FinanceError):
        catalog.review_plan(chain["definition"])
    catalog.binding(chain["definition"])
