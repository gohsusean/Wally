"""Synthetic HLB transfers only. All Chrome requests are locally fulfilled/aborted."""

from dataclasses import asdict, replace

import pytest
from playwright.sync_api import Error as PlaywrightError

from tests.finance_fixture import ATTEST, PROOF, FixtureInvoiceAccounts, catalog_fixture
from wally.adapters.browser.hlb_transfer import HLBTransferProvider
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.finance.hlb_transfer_demo import (
    SimulatedHLBBrowser,
    build_hlb_demo,
    fixture_profile,
)
from wally.finance.models import FinanceError, Kind, Scope
from wally.finance.payment_demo import prepare_card
from wally.finance.payments import PAYMENT_WORKER
from wally.finance.transfers import CertifiedTransferSource
from wally.models.ops import ExecutionStatus
from wally.runtime.principals import PrincipalAuthority


@pytest.fixture
def world(tmp_path):
    browser = SimulatedHLBBrowser()
    state = None
    try:
        state = build_hlb_demo(tmp_path, browser=browser)
        yield state
    finally:
        if state is not None:
            state.provider.close()
            state.notion.client.close()
        browser.shutdown()


def execution(world, proposal):
    return world.store.list_executions(proposal_id=proposal.id)[0]


def reconcile(world, proposal):
    return world.payments.reconcile(proposal.id, context=world.authority.issue(PAYMENT_WORKER))


def test_hlb_card_app_history_notion_and_replay(world):
    proposal, callback = prepare_card(world)
    card = str(world.transport.sent[0]["text"])
    assert "TESTBANK:0001234567" in card
    assert "MYR 100.00" in card
    assert "HLB current ending 1234" in card
    assert world.browser.submissions == 0  # Preparing/logging in has no payment side effect.
    assert world.browser.entry_reads == 0
    world.telegram.handle_update(callback)
    assert execution(world, proposal).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.browser.authorizations == 1
    assert world.browser.entry_reads == 1
    assert world.browser.submit_clicks == 0  # Review/Next initiated the bank-app request.
    assert world.notion.creates == world.notion.reads == 1
    assert any("Bank app" in str(m["text"]) for m in world.transport.sent)
    world.telegram.handle_update(callback)
    callback["update_id"] += 1
    world.telegram.handle_update(callback)
    assert world.browser.submissions == 1
    # Read-only recovery logs in again in a fresh isolated context, without another debit.
    reconcile(world, proposal)
    assert world.browser.submissions == world.notion.creates == 1


def test_inspection_gate_cannot_submit_even_after_exact_approval(world):
    world.provider.profile = replace(world.provider.profile, submission_enabled=False)
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert execution(world, proposal).status != ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.notion.creates == world.browser.entry_reads == 0


@pytest.mark.parametrize("change", ["duplicate", "incomplete", "recipient"])
def test_preflight_blocks_unsafe_inventory_before_proposal(world, change):
    if change == "duplicate":
        world.browser.duplicate = True
    elif change == "incomplete":
        world.browser.inventory_complete = False
    else:
        world.browser.recipient_changes = {"destination": "9999999999"}
    with pytest.raises(FinanceError):
        prepare_card(world)
    assert world.store.list_proposals() == []
    assert world.browser.submissions == world.notion.creates == 0
    assert world.provider._session is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("bank", "OTHERBANK"),
        ("destination", "9999999999"),
        ("payee", "Wrong beneficiary"),
        ("amount", "100.01"),
        ("currency", "USD"),
        ("reference", "OTHER-INVOICE"),
        ("funding", "Different source ending 4321"),
        ("fee", "1.00"),
        ("mode", "scheduled"),
        ("tracking", "UNREVIEWED-TRANSFER"),
    ],
)
def test_changed_request_or_review_never_clicks_another_debit(world, field, value):
    proposal, callback = prepare_card(world)
    world.browser.review_changes = {field: value}
    world.telegram.handle_update(callback)
    assert execution(world, proposal).status != ExecutionStatus.VERIFIED_SUCCESS
    # Bank/amount/etc in the outgoing body are checked before dispatch. Fee/mode
    # contradictions first visible in the response remain uncertain after initiation.
    assert world.browser.submissions == (1 if field in {"fee", "mode"} else 0)
    assert world.browser.submit_clicks == world.notion.creates == 0
    assert execution(world, proposal).outcome == "uncertain"


@pytest.mark.parametrize("status,outcome", [("pending", "bank_pending"), ("failed", "bank_failed")])
def test_independent_pending_and_failure_never_record_or_retry(world, status, outcome):
    world.browser.status = status
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert execution(world, proposal).outcome == outcome
    assert world.notion.creates == 0
    assert reconcile(world, proposal).outcome == outcome
    assert world.browser.submissions == 1
    with pytest.raises(FinanceError, match="claim"):
        prepare_card(world)


def test_bank_app_timeout_keeps_claim_and_can_reconcile_later(world):
    world.browser.app_completes = False
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert execution(world, proposal).outcome == "challenge_pending"
    assert world.notion.creates == 0
    world.browser.authorizations = 1  # Simulated owner finishes in the bank later.
    assert reconcile(world, proposal).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.notion.creates == 1


@pytest.mark.parametrize(
    "field",
    ["bank", "destination", "payee", "amount", "currency", "reference", "funding", "tracking"],
)
def test_bank_history_mismatch_cannot_certify_settlement(world, field):
    proposal, callback = prepare_card(world)
    world.browser.ledger_changes = {field: "WRONG"}
    world.telegram.handle_update(callback)
    assert execution(world, proposal).outcome == "uncertain"
    assert world.notion.creates == 0
    world.browser.ledger_changes = {}
    assert reconcile(world, proposal).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.notion.creates == 1


def test_lost_submit_response_recovers_by_history_without_another_click(world):
    world.browser.drop_submit_response = True
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert execution(world, proposal).outcome == "challenge_pending"
    world.browser.authorizations = 1
    assert reconcile(world, proposal).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.notion.creates == 1


def test_changed_source_or_duplicate_after_card_stops_before_entry(world):
    proposal, callback = prepare_card(world)
    world.browser.duplicate = True
    world.telegram.handle_update(callback)
    assert world.store.list_executions(proposal_id=proposal.id) == []
    assert world.browser.submissions == 0


def test_profile_cannot_use_another_bank_or_omit_material_fields():
    profile = fixture_profile()
    assert profile.fingerprint
    for changed in (
        replace(profile, entry_url="https://other-bank.invalid/"),
        replace(profile, funding="1234567890"),
        replace(profile, review_fields=profile.review_fields[:-1]),
        replace(profile, history_fields=profile.history_fields[:-1]),
        replace(profile, password_ref="op://x/y/password?attribute=secret"),
        replace(profile, app_timeout_seconds=0),
        replace(profile, review_selector=profile.submit_selector),
        replace(profile, history_url=profile.entry_url),
        replace(profile, payment_fields=profile.payment_fields[:-1]),
        replace(profile, read_requests=(("POST", profile.payment_requests[0][1]),)),
    ):
        with pytest.raises(FinanceError):
            _ = changed.fingerprint


def test_simulation_cannot_use_a_live_browser():
    with pytest.raises(FinanceError, match="intercepts"):
        HLBTransferProvider(PlaywrightBrowserAdapter(), fixture_profile(), None)


def test_transfer_source_cannot_be_composed_with_another_bank(world):
    world.source.required_transaction_namespace = "https://other-bank.invalid"
    with pytest.raises(FinanceError, match="exact bank route"):
        prepare_card(world)
    assert world.browser.submissions == 0


def test_reject_closes_the_read_only_preflight_without_transfer(world):
    proposal, callback = prepare_card(world)
    callback["callback_query"]["data"] = callback["callback_query"]["data"][:-1] + "r"
    world.telegram.handle_update(callback)
    assert world.store.list_executions(proposal_id=proposal.id) == []
    assert world.provider._session is None
    assert world.browser.submissions == 0
    assert world.browser.entry_reads == 0


def test_lost_worker_lease_during_bank_app_wait_retains_pending_claim(world):
    world.browser.app_completes = False
    proposal, callback = prepare_card(world)
    world.telegram.set_dispatch_guard(lambda: world.browser.submissions == 0)
    world.telegram.handle_update(callback)
    assert execution(world, proposal).outcome == "challenge_pending"
    assert world.browser.submissions == 1
    assert world.notion.creates == 0


def recipient_fixture(tmp_path):
    authority = PrincipalAuthority()
    catalog, ids, _ = catalog_fixture(tmp_path, authority, portal=False)
    context = authority.issue("cli")
    account = catalog.store.get(ids["account"])
    recipient = catalog.register_local(
        Kind.ACCOUNT,
        {
            **asdict(account.candidate.facts),
            "namespace": "bank_transfer:TESTBANK",
            "identifier": "0001234567",
            "username_ref": "",
            "password_ref": "",
        },
        context=context,
    )
    catalog.certify(
        recipient.id, Scope.IDENTITY, evidence=PROOF, attestations=ATTEST, context=context
    )
    return catalog, ids, recipient


def test_transfer_source_reuses_certificates_and_never_uses_customer_number(tmp_path):
    catalog, ids, recipient = recipient_fixture(tmp_path)
    page = catalog.store.get(ids["account"]).candidate.locator.page
    source = CertifiedTransferSource(
        catalog, {page: recipient.id}, invoice_accounts=FixtureInvoiceAccounts()
    )
    certificate = catalog.store.certificate(recipient.id, Scope.IDENTITY)
    bill = source.read(ids["instance"])
    assert bill.destination == "TESTBANK:0001234567"
    assert bill.account_reference == bill.invoice_account_reference == "000123456789"
    assert bill.account_reference not in bill.destination
    assert bill.amount == "100.00"
    assert bill.catalog_binding != catalog.binding(ids["instance"])
    assert catalog.store.certificate(recipient.id, Scope.IDENTITY) == certificate
    catalog.store.invalidate_scope(recipient.id, Scope.IDENTITY, "contradictory_recipient")
    with pytest.raises(FinanceError):
        source.read(ids["instance"])


def test_missing_recipient_does_not_fall_back_to_customer_identifier(tmp_path):
    catalog, ids, _ = recipient_fixture(tmp_path)
    with pytest.raises(FinanceError, match="recipient"):
        CertifiedTransferSource(
            catalog, {}, invoice_accounts=FixtureInvoiceAccounts()
        ).read(ids["instance"])


def test_tenant_responsibility_remains_excluded(tmp_path):
    catalog, ids, recipient = recipient_fixture(tmp_path)
    account = catalog.store.get(ids["account"])
    page = account.candidate.locator.page
    catalog.store.register(
        replace(account.candidate, facts=replace(account.candidate.facts, responsibility="other"))
    )
    with pytest.raises(FinanceError):
        CertifiedTransferSource(
            catalog, {page: recipient.id}, invoice_accounts=FixtureInvoiceAccounts()
        ).read(ids["instance"])


def test_confirmation_page_is_never_the_settlement_reader(world):
    proposal, _ = prepare_card(world)
    _, _, bill, quote = world.payments.load(proposal.id)
    # Even a fictional success result in the transfer window cannot create a ledger row.
    with pytest.raises(FinanceError, match="exactly one"):
        world.provider.verify(bill, quote)
    assert world.notion.creates == 0
    assert world.browser.ledger_reads > 0


def test_no_callback_or_direct_review_can_cross_the_boundary(world):
    proposal, _ = prepare_card(world)
    _, _, bill, quote = world.payments.load(proposal.id)
    assert world.browser.entry_reads == world.browser.submissions == 0
    for action in (world.provider.enter_review, world.provider.submit):
        with pytest.raises(FinanceError, match="Telegram payment authorization"):
            action(bill, quote, guard=lambda: True)
    assert world.browser.entry_reads == world.browser.submissions == 0


@pytest.mark.parametrize("expired", ["card", "proposal"])
def test_expired_telegram_approval_never_enters_the_form(world, expired):
    proposal, callback = prepare_card(world)
    with world.store._connect() as connection:
        if expired == "card":
            connection.execute("UPDATE telegram_payment_reviews SET expires_at=0")
        else:
            connection.execute(
                "UPDATE proposals SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (proposal.id,),
            )
    world.telegram.handle_update(callback)
    assert world.store.list_executions() == []
    assert world.browser.entry_reads == world.browser.submissions == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("payee", "Changed beneficiary"), ("destination", "TESTBANK:9999999999"),
        ("destination", "OTHERBANK:0001234567"), ("amount", "101.00"), ("currency", "USD"),
        ("account_page_id", "00000000-0000-0000-0000-000000000099"),
        ("account_reference", "ANOTHER-BILLING-ACCOUNT"), ("instance_id", "another-bill"),
        ("period_start", "2027-02-01"), ("period_end", "2027-02-28"),
    ],
)
def test_changed_approved_transaction_requires_fresh_telegram_card(world, field, value):
    _, callback = prepare_card(world)
    world.source.bill = replace(world.source.bill, **{field: value})
    world.telegram.handle_update(callback)
    assert world.browser.entry_reads == world.browser.submissions == 0
    assert world.store.list_executions() == []


@pytest.mark.parametrize("change", ["amount", "expiry", "lease"])
def test_changed_authorization_after_entry_stops_before_review_request(world, change):
    proposal, callback = prepare_card(world)

    def drift():
        if change == "amount":
            world.source.bill = replace(world.source.bill, amount="101.00")
        elif change == "expiry":
            with world.store._connect() as connection:
                connection.execute(
                    "UPDATE proposals SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                    (proposal.id,),
                )
        else:
            world.telegram.set_dispatch_guard(lambda: False)

    world.browser.on_entry = drift
    world.telegram.handle_update(callback)
    assert world.browser.entry_reads == 1
    assert world.browser.submissions == world.browser.submit_clicks == 0
    assert execution(world, proposal).outcome == "uncertain"  # Entry was already claimed.


@pytest.mark.parametrize("problem", ["retry", "navigation", "lost-response"])
def test_browser_retry_or_unexpected_navigation_retains_claim_and_never_reinitiates(world, problem):
    if problem == "retry":
        world.browser.retry_review = True
    elif problem == "navigation":
        world.browser.unexpected_navigation = True
    else:
        world.browser.drop_submit_response = True
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    world.telegram.handle_update({**callback, "update_id": 2})
    assert world.browser.submissions == world.browser.entry_reads == 1
    assert world.browser.submit_clicks == 0
    assert execution(world, proposal).outcome in {"uncertain", "challenge_pending"}
    with pytest.raises(FinanceError):
        prepare_card(world)
    # Reconciliation can only read history, including after a blocked request.
    world.browser.authorizations = 1
    assert reconcile(world, proposal).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == 1


def test_preflight_blocks_redirect_to_payment_before_telegram(world):
    world.provider.profile = replace(
        world.provider.profile,
        beneficiary_url=world.provider.profile.beneficiary_url + "?redirect-to-entry=1",
    )
    original = world.browser._route

    def redirect(route):
        if route.request.url.endswith("?redirect-to-entry=1"):
            route.fulfill(status=302, headers={"Location": world.provider.profile.entry_url})
        else:
            original(route)

    world.browser._route = redirect
    with pytest.raises((PlaywrightError, FinanceError)):
        prepare_card(world)
    assert world.browser.entry_reads == world.browser.submissions == 0
    assert world.store.list_executions() == []


def test_authorization_expiring_during_final_request_check_blocks_dispatch(world, monkeypatch):
    import time

    original = world.provider._request_allowed
    read = world.source.read
    clock = time.time()

    def request_guard(request):
        if request.url.endswith('/fixture/submit'):
            def slow_read(instance_id):
                bill = read(instance_id)
                monkeypatch.setattr(time, 'time', lambda: clock + 301)
                return bill
            world.source.read = slow_read
        return original(request)

    world.provider._request_allowed = request_guard
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.browser.entry_reads == 1
    assert world.browser.submissions == world.browser.submit_clicks == 0
    assert execution(world, proposal).outcome == 'uncertain'
