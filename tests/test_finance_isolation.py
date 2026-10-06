"""Incremental D03 certification in partially dirty, completely enumerated sources."""

import copy
import json
from dataclasses import asdict, replace

import pytest

from tests.finance_fixture import ATTEST, PROOF
from tests.test_finance_catalog import NotionFixture, uid
from wally.finance.models import FinanceError, Kind, Scope


def set_text(page, key, value):
    page["properties"][key]["rich_text"][0]["plain_text"] = value


def add_page(fixture, kind, number):
    page = copy.deepcopy(fixture.page(kind))
    page["id"] = uid(number)
    source = next(s for s in fixture.sources if s.kind == kind)
    fixture.rows[source.data_source_id].append(page)
    fixture.page_source[page["id"]] = source.data_source_id
    return page


def second_chain(fixture):
    prop = add_page(fixture, Kind.PROPERTY, 301)
    set_text(prop, "address", "Unrelated property")
    account = add_page(fixture, Kind.ACCOUNT, 303)
    account["properties"]["subject"]["relation"] = [{"id": prop["id"]}]
    # Same provider, namespace and identifier; subject alone proves isolation.
    definition = add_page(fixture, Kind.DEFINITION, 304)
    definition["properties"]["account"]["relation"] = [{"id": account["id"]}]
    return prop, account, definition


def record(catalog, page):
    return next(r for r in catalog.store.records() if r.candidate.locator.page == page["id"])


def certify(catalog, item):
    return catalog.certify(
        item.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )


def ready_chain(catalog, fixture, other=None):
    catalog.refresh()
    pages = [
        fixture.page(Kind.PROPERTY),
        fixture.page(Kind.PROVIDER),
        fixture.page(Kind.ACCOUNT),
        fixture.page(Kind.DEFINITION),
    ]
    if other:
        pages = [other[0], fixture.page(Kind.PROVIDER), other[1], other[2]]
    for page in pages:
        item = record(catalog, page)
        if catalog.store.certificate(item.id, Scope.IDENTITY) is None:
            certify(catalog, item)
    definition = record(catalog, pages[-1])
    catalog.enable(definition.id, Scope.IDENTITY, context=catalog.authority.issue("cli"))
    return definition


def instance(catalog, definition, occurrence):
    item = catalog.register_local(
        Kind.INSTANCE,
        {
            "definition": definition.id,
            "occurrence": occurrence,
            "stage": "issued",
            "currency": "MYR",
            "amount": "17.25",
            "due_date": "2026-10-20",
            "invoice_reference": occurrence,
            "amount_basis": "invoice_total",
            "evidence": [{**PROOF[0], "kind": "invoice"}],
        },
        context=catalog.authority.issue("cli"),
    )
    certify(catalog, item)
    return item


@pytest.mark.parametrize("failure", ["recurrence", "unsupported", "dependency"])
def test_unrelated_dirty_chain_allows_certification_and_tracking(tmp_path, failure):
    fixture = NotionFixture()
    other = second_chain(fixture)
    if failure == "recurrence":
        set_text(other[2], "frequency", "not-a-recurrence")
    elif failure == "unsupported":
        set_text(other[1], "responsibility", "legacy")
    else:
        other[1]["properties"]["provider"]["relation"] = []
    catalog = fixture.catalog(tmp_path)
    definition = ready_chain(catalog, fixture)
    occurrence = instance(catalog, definition, "clean-occurrence")
    assert [r.id for r, _ in catalog.tracked_instances()] == [occurrence.id]
    issues = catalog.store.read_issues()
    assert issues and all(i.locator.page != fixture.page(Kind.ACCOUNT)["id"] for i in issues)
    assert any(r["state"] == "needs_attention" for r in catalog.status())
    assert not any(
        r.candidate.locator.page in {i.locator.page for i in issues}
        for r, _ in catalog.tracked_instances()
    )


def test_unrelated_new_malformed_account_preserves_exact_certificates(tmp_path):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    definition = ready_chain(catalog, fixture)
    before = catalog.binding(definition.id)
    # Same namespace with unreadable identifier is scoped by the different subject.
    other[1]["properties"]["identifier"]["rich_text"] = [{"type": "mention"}]
    catalog.refresh()
    assert catalog.binding(definition.id) == before
    assert catalog.store.enabled(definition.id, Scope.IDENTITY, before)


@pytest.mark.parametrize(
    "failure", ["nonidentity", "identifier", "namespace", "truncated_relation", "multi_relation"]
)
def test_malformed_competing_account_cannot_hide_identity_collision(tmp_path, failure):
    fixture = NotionFixture()
    competing = add_page(fixture, Kind.ACCOUNT, 399)
    if failure == "nonidentity":
        set_text(competing, "responsibility", "legacy")
    elif failure in {"identifier", "namespace"}:
        set_text(competing, failure, "")
    elif failure == "truncated_relation":
        competing["properties"]["provider"]["has_more"] = True
        fixture.relation_items = {"results": [], "has_more": True, "next_cursor": None}
    else:
        competing["properties"]["provider"]["relation"].append({"id": uid(201)})
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    certify(catalog, record(catalog, fixture.page(Kind.PROPERTY)))
    certify(catalog, record(catalog, fixture.page(Kind.PROVIDER)))
    with pytest.raises(FinanceError, match="namespace"):
        certify(catalog, record(catalog, fixture.page(Kind.ACCOUNT)))
    assert not any(r.candidate.locator.page == competing["id"] for r in catalog.store.records())


def test_valid_duplicate_still_blocks_candidate(tmp_path):
    fixture = NotionFixture()
    add_page(fixture, Kind.ACCOUNT, 399)
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    certify(catalog, record(catalog, fixture.page(Kind.PROPERTY)))
    certify(catalog, record(catalog, fixture.page(Kind.PROVIDER)))
    with pytest.raises(FinanceError, match="Duplicate"):
        certify(catalog, record(catalog, fixture.page(Kind.ACCOUNT)))


def test_malformed_existing_dependency_invalidates_only_its_chain(tmp_path):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    first = ready_chain(catalog, fixture)
    second = ready_chain(catalog, fixture, other)
    first_instance = instance(catalog, first, "first")
    second_instance = instance(catalog, second, "second")
    second_binding = catalog.binding(second.id)
    set_text(fixture.page(Kind.ACCOUNT), "responsibility", "legacy")
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(first.id)
    assert catalog.binding(second.id) == second_binding
    assert {r.id for r, _ in catalog.tracked_instances()} == {second_instance.id}
    assert catalog.store.certificate(first_instance.id, Scope.IDENTITY) is None
    set_text(fixture.page(Kind.ACCOUNT), "responsibility", "owner")
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(first.id)


@pytest.mark.parametrize(
    "failure", ["type", "property", "relation_target", "pagination", "classification"]
)
def test_source_failures_block_all_dependent_chains_but_preserve_provider(tmp_path, failure):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    first = ready_chain(catalog, fixture)
    second = ready_chain(catalog, fixture, other)
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    binding = catalog.binding(provider.id)
    source = fixture.sources[2]
    schema = fixture.schemas[source.data_source_id]["properties"]
    if failure == "type":
        schema["identifier"]["type"] = "number"
    elif failure == "property":
        schema.pop("identifier")
    elif failure == "relation_target":
        schema["subject"]["relation"]["data_source_id"] = uid(999)
    elif failure == "pagination":
        fixture.bad_cursor = True
    else:
        from wally.models.knowledge import KnowledgeClass

        catalog.registry.approve(source.database_id, KnowledgeClass.GOVERNANCE, approved_by="owner")
    catalog.refresh()
    for definition in (first, second):
        with pytest.raises(FinanceError):
            catalog.binding(definition.id)
    assert catalog.binding(provider.id) == binding
    assert any(
        i.kind == Kind.ACCOUNT and i.reason == "source_invalid" for i in catalog.store.read_issues()
    )


@pytest.mark.parametrize(
    "kind,field", [(Kind.ACCOUNT, "responsibility"), (Kind.DEFINITION, "frequency")]
)
def test_new_malformed_collision_invalidates_affected_chain_without_grandfathering(
    tmp_path,
    kind,
    field,
):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    first = ready_chain(catalog, fixture)
    second = ready_chain(catalog, fixture, other)
    before = catalog.binding(second.id)
    competing = add_page(fixture, kind, 399)
    set_text(competing, field, "legacy")
    catalog.refresh()
    assert catalog.store.certificate(first.id, Scope.IDENTITY) is None
    with pytest.raises(FinanceError):
        catalog.binding(first.id)
    assert catalog.binding(second.id) == before
    # Removing the ambiguity clears diagnostics but requires explicit recertification.
    source = next(s for s in fixture.sources if s.kind == kind)
    fixture.rows[source.data_source_id].remove(competing)
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(first.id)
    if kind == Kind.ACCOUNT:
        certify(catalog, record(catalog, fixture.page(Kind.ACCOUNT)))
    certify(catalog, record(catalog, fixture.page(Kind.DEFINITION)))
    assert not catalog.store.enabled(first.id, Scope.IDENTITY, catalog.binding(first.id))


def test_unknown_identity_cannot_be_declared_unrelated(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    definition = ready_chain(catalog, fixture)
    page = add_page(fixture, Kind.ACCOUNT, 399)
    page["properties"] = {}
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(definition.id)
    issue = next(i for i in catalog.store.read_issues() if i.locator.page == page["id"])
    assert issue.constraints == {}


def test_read_issues_are_persistent_and_do_not_disclose_raw_values(tmp_path):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    definition = ready_chain(catalog, fixture)
    set_text(other[1], "responsibility", "canary-invalid-value")
    catalog.refresh()
    from wally.finance.store import FinanceStore

    reopened = FinanceStore(catalog.store.path)
    assert reopened.read_issues() == catalog.store.read_issues()
    diagnostics = json.dumps([asdict(i) for i in reopened.read_issues()])
    assert "000123456789" not in diagnostics and "canary-invalid-value" not in diagnostics
    assert "canary-invalid-value" not in json.dumps(catalog.status())
    assert catalog.binding(definition.id)


def test_complete_multi_relation_blocks_possible_targets_only(tmp_path):
    fixture = NotionFixture()
    other = second_chain(fixture)
    third_account = add_page(fixture, Kind.ACCOUNT, 403)
    set_text(third_account, "namespace", "another-namespace")
    third_definition = add_page(fixture, Kind.DEFINITION, 404)
    third_definition["properties"]["account"]["relation"] = [{"id": third_account["id"]}]
    catalog = fixture.catalog(tmp_path)
    first = ready_chain(catalog, fixture)
    second = ready_chain(catalog, fixture, other)
    certify(catalog, record(catalog, third_account))
    third = record(catalog, third_definition)
    certify(catalog, third)
    before = catalog.binding(third.id)
    conflicting = add_page(fixture, Kind.DEFINITION, 499)
    conflicting["properties"]["account"]["relation"] = [
        {"id": fixture.page(Kind.ACCOUNT)["id"]},
        {"id": other[1]["id"]},
    ]
    catalog.refresh()
    for definition in (first, second):
        with pytest.raises(FinanceError):
            catalog.binding(definition.id)
    assert catalog.binding(third.id) == before
    # Truncation prevents proving the set excludes the third account.
    conflicting["properties"]["account"]["has_more"] = True
    fixture.relation_items = {"results": [], "has_more": True, "next_cursor": None}
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(third.id)


@pytest.mark.parametrize("malformed", [False, True])
def test_accepted_uuid_spelling_is_normalized_before_record_isolation(tmp_path, malformed):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    clean = ready_chain(catalog, fixture)
    before = catalog.binding(clean.id)
    old = record(catalog, other[2])
    other[2]["id"] = other[2]["id"].replace("-", "").upper()
    if malformed:
        set_text(other[2], "frequency", "legacy")
    catalog.refresh()
    assert catalog.binding(clean.id) == before
    assert catalog.store.get(old.id).invalid == malformed


def test_wrong_record_kind_mapping_cannot_supply_disjointness_evidence(tmp_path):
    fixture = NotionFixture()
    original = fixture.sources[2]
    fields = dict(original.fields)
    fields["provider"] = replace(
        fields["provider"], target_data_source=fixture.sources[0].data_source_id
    )
    bad_source = replace(original, database_id=uid(15), data_source_id=uid(115), fields=fields)
    fixture.sources.append(bad_source)
    schema = copy.deepcopy(fixture.schemas[original.data_source_id])
    schema.update(id=bad_source.data_source_id, parent={"database_id": bad_source.database_id})
    schema["properties"]["provider"]["relation"]["data_source_id"] = fields[
        "provider"
    ].target_data_source
    fixture.schemas[bad_source.data_source_id] = schema
    page = copy.deepcopy(fixture.page(Kind.ACCOUNT))
    page.update(id=uid(315), parent={"data_source_id": bad_source.data_source_id})
    page["properties"]["provider"]["relation"] = [{"id": fixture.page(Kind.PROPERTY)["id"]}]
    fixture.rows[bad_source.data_source_id] = [page]
    fixture.page_source[page["id"]] = bad_source.data_source_id
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    certify(catalog, record(catalog, fixture.page(Kind.PROPERTY)))
    certify(catalog, record(catalog, fixture.page(Kind.PROVIDER)))
    with pytest.raises(FinanceError, match="namespace"):
        certify(catalog, record(catalog, fixture.page(Kind.ACCOUNT)))
    assert any(
        i.source == bad_source.key and i.reason == "source_invalid"
        for i in catalog.store.read_issues()
    )


@pytest.mark.parametrize("subject_kind", ["entity", "personal", "unknown"])
def test_contradictory_subject_kind_cannot_prove_unrelated_scope(tmp_path, subject_kind):
    fixture = NotionFixture()
    other = second_chain(fixture)
    catalog = fixture.catalog(tmp_path)
    first = ready_chain(catalog, fixture)
    set_text(other[1], "subject_kind", subject_kind)
    catalog.refresh()
    issue = next(i for i in catalog.store.read_issues() if i.locator.page == other[1]["id"])
    assert "subject" not in issue.constraints
    with pytest.raises(FinanceError):
        catalog.binding(first.id)


def test_malformed_subject_constant_does_not_fall_into_global_invalidation(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    first = ready_chain(catalog, fixture)
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    before = catalog.binding(provider.id)
    source = fixture.sources[2]
    source = replace(
        source,
        fields={k: v for k, v in source.fields.items() if k != "subject_kind"},
        constants={"subject_kind": []},
    )
    fixture.sources[2] = source
    # Fixture-only designation of the exact changed mapping; no live configuration.
    catalog.store.designate(source.key, source.fingerprint, {})
    catalog.refresh()
    assert catalog.binding(provider.id) == before
    with pytest.raises(FinanceError):
        catalog.binding(first.id)
    assert any(
        i.kind == Kind.ACCOUNT and i.reason == "record_invalid" for i in catalog.store.read_issues()
    )
