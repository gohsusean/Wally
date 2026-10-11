import copy
from dataclasses import replace

import pytest

from tests.finance_fixture import ATTEST, PROOF
from tests.test_finance_catalog import NotionFixture
from tests.test_finance_isolation import add_page, record, set_text
from wally.finance.coverage import coverage, required_checks, reviewed_proof
from wally.finance.models import FinanceError, Kind, Scope, parse_facts


def review(item, store):
    return {
        "fingerprint": item.candidate.fingerprint,
        "evidence": PROOF,
        "checks": {k: [0] for k in required_checks(item, store)},
        "discrepancies": [],
        "owner_decisions": [],
    }


def test_coverage_never_certifies_and_reuses_existing_evidence(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    reviews = {provider.id: review(provider, catalog.store)}
    report = coverage(catalog, reviews)
    assert report["totals"]["total"] == 3
    assert report["totals"]["certified"] == 0
    assert report["records"][0]["coverage"] in {
        "reviewed_not_certified",
        "missing_supporting_evidence",
    }
    assert not catalog.approval.prompts
    proof = reviewed_proof(catalog, provider.id, reviews)
    catalog.certify(
        provider.id,
        Scope.IDENTITY,
        **proof,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    assert coverage(catalog)["totals"]["certified"] == 1
    assert coverage(catalog)["totals"]["missing_evidence"] == 2


def test_stale_review_and_external_material_edit_never_restore_certification(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    reviews = {provider.id: review(provider, catalog.store)}
    proof = reviewed_proof(catalog, provider.id, reviews)
    catalog.certify(
        provider.id,
        Scope.IDENTITY,
        **proof,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    set_text(fixture.page(Kind.PROVIDER), "issuer", "changed issuer")
    report = coverage(catalog, reviews)
    assert report["totals"]["certified"] == 0
    assert (
        next(r for r in report["records"] if r.get("id") == provider.id)["review_state"]
        == "stale_or_invalid"
    )
    with pytest.raises(FinanceError):
        reviewed_proof(catalog, provider.id, reviews)
    set_text(fixture.page(Kind.PROVIDER), "issuer", provider.candidate.facts.issuer)
    assert coverage(catalog)["totals"]["certified"] == 0


def test_new_malformed_rows_and_source_failures_are_not_hidden(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    page = add_page(fixture, Kind.ACCOUNT, 303)
    set_text(page, "identifier", "")
    report = coverage(catalog)
    assert report["totals"]["total"] == 4
    assert report["by_kind"]["account"] == 2
    assert report["totals"]["uncertified"] == 4
    fixture.bad_cursor = True
    report = coverage(catalog)
    assert not report["inventory_complete"]
    assert not report["totals"]["complete"]


@pytest.mark.parametrize("change", ["missing", "indices", "bool_index", "discrepancy", "decision"])
def test_incomplete_reviews_cannot_prepare_certification(tmp_path, change):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    entry = review(provider, catalog.store)
    if change == "missing":
        entry["checks"].pop("issuer")
    elif change == "indices":
        entry["checks"]["issuer"] = [99]
    elif change == "bool_index":
        entry["checks"]["issuer"] = [True]
    elif change == "discrepancy":
        entry["discrepancies"] = ["issuer"]
    else:
        entry["owner_decisions"] = ["issuer"]
    with pytest.raises(FinanceError):
        reviewed_proof(catalog, provider.id, {provider.id: entry})


def test_card_requires_safe_identifier_and_statement_defined_configuration():
    raw = {
        "provider": "fin_provider",
        "subject": "fin_owner",
        "subject_kind": "personal",
        "namespace": "credit_card_last4",
        "identifier": "0034",
        "responsibility": "owner",
    }
    assert parse_facts(Kind.ACCOUNT, raw).identifier == "0034"
    for value in ["1234567890123456", "xxxx0034", "34", "００３４"]:
        with pytest.raises(FinanceError):
            parse_facts(Kind.ACCOUNT, {**raw, "identifier": value})
    raw = {
        "account": "fin_account",
        "charge_key": "credit_card",
        "obligation_type": "credit_card_payment",
        "currency": "MYR",
        "frequency": "monthly",
        "amount_policy": "source_defined",
        "evidence_mode": "statement",
        "due_mode": "from_document",
        "active": True,
    }
    assert parse_facts(Kind.DEFINITION, raw).amount_policy == "source_defined"
    for field, value in [
        ("amount_policy", "fixed_contract"),
        ("evidence_mode", "issued_bill"),
        ("due_mode", "calendar_pattern"),
    ]:
        with pytest.raises(FinanceError):
            parse_facts(Kind.DEFINITION, {**raw, field: value})
    with pytest.raises(FinanceError):
        parse_facts(Kind.DEFINITION, {**raw, "statement_balance": "50.00"})


def test_explicit_personal_exception_reuses_entity_and_never_infers_missing_property(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    entity = catalog.register_local(
        Kind.ENTITY, {"entity_key": "personal-owner"}, context=catalog.authority.issue("cli")
    )
    page = add_page(fixture, Kind.ACCOUNT, 303)
    page["properties"]["subject"]["relation"] = []
    set_text(page, "namespace", "credit_card_last4")
    set_text(page, "identifier", "0034")
    source = next(s for s in fixture.sources if s.kind == Kind.ACCOUNT)
    original_fingerprint = source.fingerprint
    assert replace(source, personal_subjects={}).fingerprint == original_fingerprint
    source = replace(
        source,
        fields={
            **{k: v for k, v in source.fields.items() if k != "subject_kind"},
            "subject": replace(source.fields["subject"], optional=True),
        },
        constants={"subject_kind": "property"},
        personal_subjects={page["id"]: entity.id},
    )
    fixture.sources = [source if s.kind == Kind.ACCOUNT else s for s in fixture.sources]
    catalog.store.designate(source.key, source.fingerprint, {})
    catalog.refresh()
    card = record(catalog, page)
    assert card.candidate.facts.subject_kind == "personal"
    assert card.candidate.facts.subject == entity.id
    assert not card.invalid
    unknown = add_page(fixture, Kind.ACCOUNT, 304)
    unknown["properties"]["subject"]["relation"] = []
    assert coverage(catalog)["totals"]["certified"] == 0
    assert any(i.locator.page == unknown["id"] for i in catalog.store.read_issues())
    # Directly adding a property contradicts the personal exception; no fallback.
    page["properties"]["subject"]["relation"] = copy.deepcopy(
        fixture.page(Kind.ACCOUNT)["properties"]["subject"]["relation"]
    )
    catalog.refresh()
    assert catalog.store.get(card.id).invalid


def test_review_drift_before_owner_prompt_is_rejected(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    reviews = {provider.id: review(provider, catalog.store)}
    proof = reviewed_proof(catalog, provider.id, reviews)
    set_text(fixture.page(Kind.PROVIDER), "issuer", "new issuer")
    with pytest.raises(FinanceError):
        catalog.certify(
            provider.id,
            Scope.IDENTITY,
            **proof,
            attestations=ATTEST,
            context=catalog.authority.issue("cli"),
        )
    assert not catalog.approval.prompts


def test_direct_notion_review_notes_invalidate_without_persisting_contents(tmp_path):
    from wally.finance.notion import FieldMapping
    from wally.finance.store import encode

    fixture = NotionFixture()
    source = next(s for s in fixture.sources if s.kind == Kind.PROVIDER)
    page = fixture.page(Kind.PROVIDER)
    prop = {
        "id": "review-notes",
        "type": "rich_text",
        "rich_text": [{"type": "text", "plain_text": "original cycle arrangement"}],
    }
    page["properties"]["notes"] = prop
    fixture.schemas[source.data_source_id]["properties"]["notes"] = {
        "id": "review-notes",
        "type": "rich_text",
    }
    source = replace(
        source, review_fields={"obligation_notes": FieldMapping("review-notes", "rich_text")}
    )
    fixture.sources = [source if s.kind == Kind.PROVIDER else s for s in fixture.sources]
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    item = record(catalog, page)
    assert "original cycle arrangement" not in encode(item.candidate)
    reviews = {item.id: review(item, catalog.store)}
    proof = reviewed_proof(catalog, item.id, reviews)
    catalog.certify(
        item.id,
        Scope.IDENTITY,
        **proof,
        attestations=ATTEST,
        context=catalog.authority.issue("cli"),
    )
    page["properties"]["notes"]["rich_text"][0]["plain_text"] = "changed cycle arrangement"
    assert coverage(catalog, reviews)["totals"]["certified"] == 0
    assert catalog.store.get(item.id).version == item.version + 1
    page["properties"]["notes"]["rich_text"][0]["plain_text"] = "original cycle arrangement"
    assert coverage(catalog)["totals"]["certified"] == 0


def test_personal_card_certificate_invalidates_on_direct_identifier_and_property_edits(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    ctx = catalog.authority.issue("cli")
    entity = catalog.register_local(Kind.ENTITY, {"entity_key": "personal-owner"}, context=ctx)
    catalog.certify(entity.id, Scope.IDENTITY, evidence=PROOF, attestations=ATTEST, context=ctx)
    provider = record(catalog, fixture.page(Kind.PROVIDER))
    catalog.certify(provider.id, Scope.IDENTITY, evidence=PROOF, attestations=ATTEST, context=ctx)
    page = add_page(fixture, Kind.ACCOUNT, 303)
    page["properties"]["subject"]["relation"] = []
    set_text(page, "namespace", "credit_card_last4")
    set_text(page, "identifier", "0034")
    source = next(s for s in fixture.sources if s.kind == Kind.ACCOUNT)
    source = replace(
        source,
        fields={
            **{k: v for k, v in source.fields.items() if k != "subject_kind"},
            "subject": replace(source.fields["subject"], optional=True),
        },
        constants={"subject_kind": "property"},
        personal_subjects={page["id"]: entity.id},
    )
    fixture.sources = [source if s.kind == Kind.ACCOUNT else s for s in fixture.sources]
    catalog.store.designate(source.key, source.fingerprint, {})
    catalog.refresh()
    card = record(catalog, page)
    reviews = {card.id: review(card, catalog.store)}
    catalog.certify(
        card.id,
        Scope.IDENTITY,
        **reviewed_proof(catalog, card.id, reviews),
        attestations=ATTEST,
        context=ctx,
    )
    assert catalog.binding(card.id)
    set_text(page, "identifier", "0056")
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(card.id)
    set_text(page, "identifier", "0034")
    catalog.refresh()
    with pytest.raises(FinanceError):
        catalog.binding(card.id)
    page["properties"]["subject"]["relation"] = [{"id": fixture.page(Kind.PROPERTY)["id"]}]
    catalog.refresh()
    assert catalog.store.get(card.id).invalid


def test_card_worklist_requires_cycle_and_arrangement_without_statement_balances(tmp_path):
    from dataclasses import asdict

    from wally.finance.models import Candidate, Locator

    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    account = record(catalog, fixture.page(Kind.ACCOUNT))
    raw = asdict(account.candidate.facts)
    raw.update(namespace="credit_card_last4", identifier="0034")
    account = catalog.store.register(
        replace(account.candidate, facts=parse_facts(Kind.ACCOUNT, raw))
    )
    raw = {
        "account": account.id,
        "charge_key": "credit_card_statement",
        "obligation_type": "credit_card_payment",
        "currency": "MYR",
        "amount_policy": "source_defined",
        "frequency": "monthly",
        "evidence_mode": "statement",
        "due_mode": "from_document",
        "active": True,
    }
    item = catalog.store.register(
        Candidate(
            Kind.DEFINITION,
            Locator("local_owner", "", "", "card-stream"),
            parse_facts(Kind.DEFINITION, raw),
        )
    )
    checks = required_checks(item, catalog.store)
    assert {"statement_cycle", "payment_arrangement"} <= checks
    assert not {"statement_balance", "minimum_due", "actual_payment"} & checks


def test_namespace_edit_cannot_persist_full_card_number_as_customer_identity(tmp_path):
    fixture = NotionFixture()
    page = fixture.page(Kind.ACCOUNT)
    set_text(page, "namespace", "credit_card_last4")
    set_text(page, "identifier", "0034")
    definition = fixture.page(Kind.DEFINITION)
    set_text(definition, "obligation_type", "credit_card_payment")
    set_text(definition, "evidence_mode", "statement")
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    item = record(catalog, page)
    set_text(page, "namespace", "customer_account")
    # Synthetic canary, never a real payment secret.
    unsafe = "4111111111111111"
    set_text(page, "identifier", unsafe)
    report = coverage(catalog)
    assert catalog.store.get(item.id).invalid
    assert catalog.store.get(item.id).candidate.facts.identifier == "0034"
    with catalog.store.connect() as connection:
        assert unsafe not in "\n".join(connection.iterdump())
    assert report["totals"]["certified"] == 0
