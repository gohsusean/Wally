"""All-provider billing-account gate; no live Notion, bank or credential access."""

import hashlib
from dataclasses import replace

import pytest

from tests.finance_fixture import FixtureInvoiceAccounts, catalog_fixture
from tests.test_supervised_payments import FastBrowser
from wally.finance.bill_identity import (
    BillingAccountError,
    InvoiceAccounts,
    RetainedInvoiceAccounts,
    require_billing_account_match,
    reviewed_account_match,
)
from wally.finance.models import Evidence, FinanceError, Scope
from wally.finance.payment_demo import build_demo, prepare_card
from wally.finance.payments import CertifiedPaymentSource
from wally.models.ops import ProposalStatus
from wally.runtime.principals import PrincipalAuthority


@pytest.mark.parametrize(
    "notion,invoice",
    [
        ("B2-28-03A", "B2-28-03A"),
        ("b2 28 03a", "B2-28-03A"),
        ("B2–28–03A", " b2-28-03a "),
        ("0000123", "0000-123"),
    ],
)
def test_exact_and_formatting_only_matches(notion, invoice):
    require_billing_account_match(notion, invoice)


@pytest.mark.parametrize("invoice", ["B2-28-03", "B2-28-03B", "B2-28-O3A", "B2283A"])
def test_letters_and_digits_must_not_change(invoice):
    with pytest.raises(BillingAccountError, match="does not match"):
        require_billing_account_match("B2-28-03A", invoice)


@pytest.mark.parametrize(
    "notion,invoice,diagnostic",
    [
        (None, "B22803A", "Notion"),
        ("", "B22803A", "Notion"),
        ("B22803A", "", "Invoice"),
        ("B22803A", None, "Invoice"),
        ("B22803A", "Bayberry, B22803A", "confidently"),
        ("B22803A", "Ｂ22803A", "confidently"),
    ],
)
def test_missing_or_uncertain_identifiers_block(notion, invoice, diagnostic):
    with pytest.raises(BillingAccountError, match=diagnostic):
        require_billing_account_match(notion, invoice)


def proof(reference="fixture:current-invoice", content_hash="a" * 64):
    return Evidence("primary_document", reference, content_hash, "invoice")


@pytest.mark.parametrize("identifiers", [(), ("B22803A", "B22803B"), ("",)])
def test_missing_or_ambiguous_invoice_candidates_block(identifiers):
    p = proof()
    with pytest.raises(FinanceError, match="missing|ambiguous"):
        reviewed_account_match(
            "B2-28-03A",
            (p,),
            (InvoiceAccounts(p.reference, p.content_hash, identifiers),),
            "mapped-id",
        )


def test_every_invoice_in_a_quarter_must_match_and_be_current():
    evidence = tuple(proof(f"fixture:month-{month}") for month in (10, 11, 12))
    documents = tuple(InvoiceAccounts(p.reference, p.content_hash, ("B22803A",)) for p in evidence)
    matched = reviewed_account_match("B2-28-03A", evidence, documents, "notion-property-id")
    assert matched["account_reference"] == "B2-28-03A"
    assert len(matched["account_match_binding"]) == 64
    with pytest.raises(FinanceError):
        reviewed_account_match("B2-28-03A", evidence, documents[:-1], "mapped-id")
    with pytest.raises(FinanceError):
        reviewed_account_match("B2-28-03A", evidence, documents + documents[:1], "mapped-id")
    with pytest.raises(FinanceError):
        reviewed_account_match(
            "B2-28-03A",
            evidence,
            (*documents[:2], replace(documents[2], identifiers=("B22803B",))),
            "mapped-id",
        )


def test_retained_reader_extracts_from_exact_original_and_rereads_hash(tmp_path):
    original = b"Invoice\nBilling Account: UNIT-123\nBank Account: 99999999\n"
    path = tmp_path / "original"
    path.write_bytes(original)
    p = proof(content_hash=hashlib.sha256(original).hexdigest())

    def extract(content):
        return tuple(
            line.split(b":", 1)[1].strip().decode()
            for line in content.splitlines()
            if line.startswith(b"Billing Account:")
        )

    reader = RetainedInvoiceAccounts({p.reference: (path, extract)})
    documents = reader.read((p,))
    assert documents[0].identifiers == ("UNIT-123",)
    reviewed_account_match("unit 123", (p,), documents, "current-notion-binding")
    path.write_bytes(original + b"edited")
    with pytest.raises(FinanceError, match="changed"):
        reader.read((p,))


def test_extractor_cannot_borrow_another_invoice_or_race_evidence(tmp_path):
    path = tmp_path / "original"
    path.write_bytes(b"current invoice")
    p = proof(content_hash=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(FinanceError, match="no reviewed"):
        RetainedInvoiceAccounts({"other-invoice": (path, lambda _: ("A1",))}).read((p,))

    def raced(_):
        path.write_bytes(b"changed")
        return ("A1",)

    with pytest.raises(FinanceError, match="changed during"):
        RetainedInvoiceAccounts({p.reference: (path, raced)}).read((p,))


@pytest.fixture
def world(tmp_path):
    state = build_demo(tmp_path, browser=FastBrowser())
    yield state
    state.notion.client.close()


@pytest.mark.parametrize(
    "change",
    [
        {"account_reference": "OTHER"},
        {"invoice_account_reference": "OTHER"},
        {"account_reference": ""},
        {"invoice_account_reference": ""},
        {"account_match_binding": ""},
    ],
)
def test_gate_blocks_before_bank_preflight_or_secrets(world, change):
    world.source.bill = replace(world.source.bill, **change)
    world.provider.prepare = lambda _: pytest.fail("Bank preflight must not start")
    with pytest.raises(FinanceError):
        prepare_card(world)
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_reference", "OTHER"),
        ("invoice_account_reference", "OTHER"),
        ("account_match_binding", "d" * 64),
    ],
)
def test_changed_account_invalidates_card_before_authorization(world, field, value):
    proposal, callback = prepare_card(world)
    world.source.bill = replace(world.source.bill, **{field: value})
    world.telegram.handle_update(callback)
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED
    assert not world.secrets.resolve_calls
    assert not world.store.list_executions()
    assert world.browser.submissions == 0


def test_account_change_after_decision_blocks_execution(world):
    proposal, callback = prepare_card(world)
    execute = world.payments.execute

    def changed(*args, **kwargs):
        world.source.bill = replace(world.source.bill, account_reference="OTHER")
        return execute(*args, **kwargs)

    world.payments.execute = changed
    world.telegram.handle_update(callback)
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.APPROVED
    with pytest.raises(BillingAccountError, match="fresh payment review"):
        world.payments._current(proposal.id, approved=True)
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0


def test_account_change_during_login_stops_before_transfer(world):
    proposal, callback = prepare_card(world)
    world.browser.on_fill = lambda: setattr(
        world.source, "bill", replace(world.source.bill, account_reference="OTHER")
    )
    world.telegram.handle_update(callback)
    assert world.store.list_executions()[0].outcome == "billing_account_changed"
    assert world.browser.submissions == 0
    assert "Billing account details changed" in world.approvals.result(proposal.id)


def test_source_preserves_customer_identifier_separately_from_bank_destination(tmp_path):
    catalog, ids, _ = catalog_fixture(tmp_path, PrincipalAuthority())
    source = CertifiedPaymentSource(catalog, invoice_accounts=FixtureInvoiceAccounts())
    bill = source.read(ids["instance"])
    assert bill.account_reference == bill.invoice_account_reference == "000123456789"
    assert bill.account_match_binding
    with pytest.raises(FinanceError, match="extraction is unavailable"):
        CertifiedPaymentSource(catalog).read(ids["instance"])
    # Account edits invalidate existing certification independently of the new match.
    account = catalog.store.get(ids["account"])
    catalog.store.register(
        replace(account.candidate, facts=replace(account.candidate.facts, identifier="DIFFERENT"))
    )
    with pytest.raises(FinanceError):
        source.read(ids["instance"])
    assert catalog.store.certificate(ids["account"], Scope.IDENTITY) is None
