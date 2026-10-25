"""Narrow test exceptions and authorization regressions; no bank/network effects."""

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode
from uuid import uuid4

import pytest
import yaml

from wally.adapters.browser.hlb_test_transfer import ENTRY
from wally.finance.models import FinanceError
from wally.finance.payments import require_payment_submission
from wally.finance.test_transfers import APPROVED_TEST
from wally.finance.test_transfers import TestReceiptMailer as ReceiptTestMailer
from wally.telegram.poll import build_service
from wally.telegram.service import TelegramConfig


@pytest.fixture
def world(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "hlb-payments.yaml").write_text("schema_version: 1\nprofile: null\nrecipients: {}\n")
    (config / "test-transfer.yaml").write_text(yaml.safe_dump(APPROVED_TEST))
    settings = SimpleNamespace(
        project_root=tmp_path,
        ops_database=tmp_path / "operations.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit",
        dry_run=False,
        notion_api_key=None,
        secrets_enabled=False,
        communications_enabled=False,
        browser_headless=True,
        browser_channel="chrome",
    )
    from wally.finance.payment_demo import DemoTelegram
    from wally.finance.production_payments import build_production_payments

    transport = DemoTelegram()
    service = build_service(
        settings,
        TelegramConfig("test", "42", "test", "42"),
        transport,
        payment_factory=build_production_payments,
    )
    service.set_dispatch_guard(lambda: True)
    tests = service._payment_approvals.test_payments
    tests.provider.browser.open_session = Mock(side_effect=AssertionError("No live bank"))
    return SimpleNamespace(service=service, payments=tests, transport=transport, settings=settings)


def prepare(world):
    payments = world.payments
    proposal = payments.prepare_new(context=payments.authority.issue("cli"))
    world.service.sync()
    world.service.deliver_pending()
    card = next(m for m in world.transport.sent if m["buttons"])
    callback = {
        "update_id": 1,
        "callback_query": {
            "id": "owner-test-tap",
            "from": {"id": 42, "is_bot": False},
            "data": card["buttons"][0][0]["callback_data"],
            "message": {"message_id": 1, "date": 1, "chat": {"id": 42, "type": "private"}},
        },
    }
    return proposal, card, callback


def test_real_test_card_is_local_and_bill_route_remains_disabled(world):
    proposal, card, _ = prepare(world)
    text = str(card["text"])
    for required in ("Test transfer", "RM1.00", "Sean UOB", "2203062747", "gohsusean@gmail.com"):
        assert required in text
    assert "Typical Amount" not in text
    assert "Period:" not in text
    assert "Billing account" not in text
    _, spec, bill, quote = world.payments.load(proposal.id)
    assert quote.transaction in text
    assert spec["kind"] == "owner_account_test"
    assert not bill.period_start and not bill.invoice_account_reference
    assert spec["typical_amount_review"] is None
    world.payments.provider.browser.open_session.assert_not_called()
    bills = world.service._payment_approvals.payments
    assert not bills.enabled()
    with pytest.raises(FinanceError, match="different trusted route"):
        bills.load(proposal.id)
    with pytest.raises(FinanceError):
        bills._validate_bill(bill)  # The test exception never weakens ordinary bills.


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "1.01"),
        ("currency", "USD"),
        ("destination", "UOB:2203062748"),
        ("destination", "OTHER:2203062747"),
        ("payee", "Another favourite"),
        ("period_start", "2027-01-01"),
        ("account_page_id", str(uuid4())),
    ],
)
def test_test_route_rejects_any_material_drift(world, field, value):
    bill = world.payments.source.read("test_" + uuid4().hex)
    with pytest.raises(FinanceError):
        world.payments._validate_bill(replace(bill, **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "2.00"),
        ("destination_bank", "OTHER"),
        ("destination_account", "9999999999"),
        ("receipt_email", "other@example.test"),
    ],
)
def test_config_is_not_blanket_transfer_permission(world, field, value):
    policy = {**APPROVED_TEST, field: value}
    (world.settings.project_root / "config/test-transfer.yaml").write_text(yaml.safe_dump(policy))
    with pytest.raises(FinanceError, match="explicit owner review"):
        world.payments.prepare_new(context=world.payments.authority.issue("cli"))


@pytest.mark.parametrize("decision", ["reject", "expired", "other_owner"])
def test_invalid_telegram_approval_never_starts_bank(world, decision):
    _, _, callback = prepare(world)
    world.payments.provider.authenticate = Mock()
    if decision == "reject":
        callback["callback_query"]["data"] = callback["callback_query"]["data"][:-1] + "r"
    elif decision == "expired":
        with world.payments.store._connect() as c:
            c.execute("UPDATE telegram_payment_reviews SET expires_at=?", (time.time() - 1,))
    else:
        callback["callback_query"]["from"]["id"] = 43
    world.service.handle_update(callback)
    world.payments.provider.authenticate.assert_not_called()


def test_fresh_callback_binds_worker_once_and_unknown_outcome_blocks_new_attempt(world):
    proposal, _, callback = prepare(world)
    world.payments.provider.authenticate = Mock(side_effect=lambda r, guard, session_id: guard())
    called = []

    def submit(bill, quote, *, guard):
        assert world.transport.answers == ["Checking this exact payment approval."]
        require_payment_submission(bill, quote)
        guard()
        called.append(quote.transaction)
        raise FinanceError("Uncertain transport result")

    world.payments.provider.submit = submit
    world.service.handle_update(callback)
    callback["update_id"] += 1
    world.service.handle_update(callback)
    assert len(called) == 1
    execution = world.payments.store.list_executions(proposal_id=proposal.id)[0]
    assert execution.outcome == "uncertain"
    with pytest.raises(FinanceError, match="previous test outcome is uncertain"):
        world.payments.prepare_new(context=world.payments.authority.issue("cli"))


def test_worker_scope_revalidates_exact_entry_request(world):
    from greenlet import greenlet

    prepare_result = prepare(world)
    provider = world.payments.provider
    provider.authenticate = Mock(side_effect=lambda r, guard, session_id: guard())
    events = []
    provider.event = lambda name, details: events.append((name, details))
    allowed = []

    def submit(bill, quote, *, guard):
        provider._authorization = (require_payment_submission(bill, quote), bill, quote)
        provider._pending = {"phase": "entry", "url": ENTRY}
        request = SimpleNamespace(url=ENTRY, method="GET", resource_type="document")
        allowed.append(greenlet(lambda: provider._request_allowed(request)).switch())
        raise FinanceError("Offline boundary only")

    provider.submit = submit
    world.service.handle_update(prepare_result[2])
    assert allowed == [True], events


def test_real_submit_keeps_route_identity_stable_during_entry_callback(world):
    from greenlet import greenlet

    proposal, _, callback = prepare(world)
    provider = world.payments.provider
    provider.authenticate = Mock(side_effect=lambda r, guard, session_id: guard())
    events, allowed = [], []
    provider.event = lambda name, details: events.append((name, details))

    def entry(*args, **kwargs):
        _, spec, _, _ = world.payments.load(proposal.id)
        assert provider.fingerprint == spec["route"], "Loaded route identity changed during submit"
        provider._pending = {"phase": "entry", "url": ENTRY}
        request = SimpleNamespace(url=ENTRY, method="GET", resource_type="document")
        allowed.append(greenlet(lambda: provider._request_allowed(request)).switch())
        raise FinanceError("Offline entry complete")

    # Keep the real submit frame active while the bank callback revalidates it.
    provider._enter_transfer = entry
    provider._page = lambda: SimpleNamespace()
    world.service.handle_update(callback)
    assert allowed == [True], events


@pytest.mark.parametrize("target", [ENTRY, ENTRY + "?o=1", ENTRY + "?o=1&o=2"])
def test_interbank_entry_uses_exact_menu_context_not_bare_shared_endpoint(world, target):
    provider = world.payments.provider
    link = Mock()
    link.inner_text.return_value = "DuitNow to Account/ IBG"
    link.is_visible.return_value = True
    link.evaluate.return_value = target
    anchors = Mock()
    anchors.all.return_value = [link]
    anchors.filter.return_value.count.return_value = 1
    anchors.filter.return_value.is_visible.return_value = True
    anchors.filter.return_value.get_attribute.return_value = "#"
    frame = SimpleNamespace(
        url="https://s.hongleongconnect.my/rib/app/fo/main", locator=lambda _: anchors
    )
    transfer = SimpleNamespace(url=target, wait_for_load_state=Mock())
    page = SimpleNamespace(frames=[frame], wait_for_load_state=Mock(), wait_for_timeout=Mock())

    def finish_child_navigation(milliseconds):
        assert provider._pending == {"phase": "entry", "url": target}
        assert link.click.call_count == 1
        page.frames.append(transfer)
        response = Mock(status=200)
        response.text.return_value = "<partial-response><changes/></partial-response>"
        provider._initialization_request = Mock(response=lambda: response)

    page.wait_for_timeout.side_effect = finish_child_navigation
    if target == ENTRY + "?o=1":
        provider._enter_transfer(page)
        anchors.filter.return_value.click.assert_called_once()
        anchors.filter.return_value.hover.assert_not_called()
        link.wait_for.assert_called_once_with(state="visible", timeout=5000)
        link.click.assert_called_once()
        transfer.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=15000)
        page.wait_for_load_state.assert_not_called()
        assert provider._pending is None
        assert provider._initialization_request is None
    else:
        with pytest.raises(FinanceError, match="menu destination changed"):
            provider._enter_transfer(page)
        link.click.assert_not_called()


def initialization_request(provider):
    bill = provider.source.read("test_" + uuid4().hex)
    quote = provider.prepare(bill)
    authorization = Mock()
    provider._authorization = (authorization, bill, quote)
    provider._pending = {"phase": "entry", "url": ENTRY + "?o=1"}
    fields = {
        "idBtnCancel": "Cancel",
        "idFormEntry": "idFormEntry",
        "idOneTimeDate": "24-Oct-2026",
        "idRecur": "0",
        "idSOMTransferFrom": "SOURCE-SECRET-CANARY",
        "javax.faces.ViewState": "CSRF-SECRET-CANARY",
        "j_idt601": "",
        "javax.faces.source": "mobileDeviceFlagUpdateBtn2",
        "javax.faces.partial.event": "click",
        "javax.faces.partial.execute": "mobileDeviceFlagUpdateBtn2 mobileDeviceFlagUpdateBtn2",
        "javax.faces.partial.render": "idFormEntry",
        "javax.faces.behavior.event": "action",
        "javax.faces.partial.ajax": "true",
    }
    request = SimpleNamespace(
        url=ENTRY,
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=urlencode(fields),
    )
    return authorization, fields, request


def test_observed_nonpayment_initialization_requires_exact_live_scope_once(world):
    provider = world.payments.provider
    authorization, _, request = initialization_request(provider)
    events = []
    provider.event = lambda name, details: events.append((name, details))
    assert provider._request_allowed(request) is True
    authorization.check.assert_called_once()
    assert provider._initialization_dispatched and not provider._review_dispatched
    assert provider._request_allowed(request) is False
    assert "SOURCE-SECRET-CANARY" not in str(events)
    assert "CSRF-SECRET-CANARY" not in str(events)


@pytest.mark.parametrize("drift", [None, "token", "extra_query", "origin"])
def test_setup_pins_the_current_native_bank_execution_url(world, drift):
    provider = world.payments.provider
    _, _, request = initialization_request(provider)
    current_action = ENTRY + "?execution=PRIVATE-FLOW-CANARY"
    request.url = current_action
    request.frame = Mock()
    request.frame.locator.return_value.evaluate.return_value = current_action
    if drift == "token":
        request.url = ENTRY + "?execution=DIFFERENT-FLOW"
    elif drift == "extra_query":
        request.url += "&o=2"
    elif drift == "origin":
        request.url = request.url.replace("s.hongleongconnect.my", "untrusted.invalid")
    assert provider._request_allowed(request) is (drift is None)


@pytest.mark.parametrize("drift", [None, "amount", "account", "selector"])
def test_setup_keeps_empty_payment_fields_and_native_selector_defaults(world, drift):
    provider = world.payments.provider
    _, fields, request = initialization_request(provider)
    fields.update({"idAmt": "", "idAcctNo": "", "idEmail": "", "idSms": "",
                   "idOthInfo": "", "idBnk": "", "idAcctTyp": "", "idRR": ""})
    request.frame = Mock()
    request.frame.locator.return_value.input_value.return_value = ""
    if drift == "amount":
        fields["idAmt"] = "1.00"
    elif drift == "account":
        fields["idAcctNo"] = "2203062747"
    elif drift == "selector":
        fields["idBnk"] = "CHANGED"
    request.post_data = urlencode(fields)
    assert provider._request_allowed(request) is (drift is None)


@pytest.mark.parametrize(
    "change",
    [
        "amount",
        "beneficiary",
        "source",
        "execute",
        "duplicate",
        "expiry",
        "phase",
        "endpoint",
    ],
)
def test_initialization_cannot_become_a_payment_or_bypass_approval(world, change):
    provider = world.payments.provider
    authorization, fields, request = initialization_request(provider)
    if change == "amount":
        fields["idAmt"] = "1.00"
    elif change == "beneficiary":
        fields["idAcctNo"] = "2203062747"
    elif change == "source":
        fields["javax.faces.source"] = "idBtnSubmit"
    elif change == "execute":
        fields["javax.faces.partial.execute"] = "idBtnSubmit"
    elif change == "expiry":
        authorization.check.side_effect = FinanceError("Expired")
    elif change == "phase":
        provider._pending = None
    elif change == "endpoint":
        request.url = ENTRY + "?o=2"
    request.post_data = urlencode(fields) + ("&idRecur=0" if change == "duplicate" else "")
    assert provider._request_allowed(request) is False
    assert not provider._initialization_dispatched and not provider._review_dispatched


def test_distinct_preparations_get_distinct_attempt_and_tracking(world):
    a = world.payments.prepare_new(context=world.payments.authority.issue("cli"))
    b = world.payments.prepare_new(context=world.payments.authority.issue("cli"))
    _, _, bill_a, quote_a = world.payments.load(a.id)
    _, _, bill_b, quote_b = world.payments.load(b.id)
    assert bill_a.obligation != bill_b.obligation
    assert quote_a.transaction != quote_b.transaction


def test_no_arbitrary_guard_can_enter_bank_flow_without_telegram_scope(world):
    _, _, bill, quote = world.payments.load(prepare(world)[0].id)
    with pytest.raises(FinanceError, match="Telegram payment authorization"):
        world.payments.provider.submit(bill, quote, guard=lambda: True)
    world.payments.provider.browser.open_session.assert_not_called()


def test_unknown_entry_and_jsf_requests_fail_closed_without_approval(world):
    provider = world.payments.provider
    request = SimpleNamespace(url=ENTRY, method="GET", post_data=None)
    assert provider._request_allowed(request) is False
    request.method, request.post_data = "POST", "idAmt=1.00"
    assert provider._request_allowed(request) is False


@pytest.mark.parametrize("encoding", ["native", "ajax"])
def test_jsf_request_exactness_replay_and_secret_redaction(world, encoding):
    provider = world.payments.provider
    bill = provider.source.read("test_" + uuid4().hex)
    quote = provider.prepare(bill)
    auth = Mock()
    provider._authorization = (auth, bill, quote)
    provider._review_claimed = True
    fields = {
        "idAmt": "1.00",
        "idDesc": bill.reference,
        "idOthInfo": quote.transaction,
        "idSOMTransferFrom": "SOURCE-SECRET-CANARY",
        "javax.faces.ViewState": "TOKEN-CANARY",
    }
    provider._pending = {
        "url": ENTRY,
        "phase": "review",
        "fields": fields,
        "control": "idBtnSubmit",
        "control_value": "Next",
        "material": fields,
        "jsf_buttons": {"idBtnCancel": "Cancel", "j_idt601": ""},
    }
    body = {**fields, "idBtnSubmit": "Next"}
    if encoding == "ajax":
        body.update({"idBtnCancel": "Cancel", "j_idt601": "",
                     "javax.faces.partial.ajax": "true", "javax.faces.source": "idBtnSubmit"})
    request = SimpleNamespace(
        url=ENTRY,
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=urlencode(body),
    )
    events = []
    provider.event = lambda name, details: events.append((name, details))
    assert provider._request_allowed(request) is True
    assert provider._review_dispatched
    assert provider._request_allowed(request) is False
    assert "SOURCE-SECRET-CANARY" not in str(events)
    assert "TOKEN-CANARY" not in str(events)


@pytest.mark.parametrize("change", ["amount", "source", "duplicate", "expiry", "command"])
def test_jsf_drift_or_expiry_blocks_before_dispatch(world, change):
    provider = world.payments.provider
    bill = provider.source.read("test_" + uuid4().hex)
    quote = provider.prepare(bill)
    auth = Mock()
    provider._authorization = (auth, bill, quote)
    provider._review_claimed = True
    fields = {
        "idAmt": "1.00",
        "idOthInfo": quote.transaction,
        "idSOMTransferFrom": "approved-source",
        "javax.faces.ViewState": "csrf",
    }
    provider._pending = {
        "url": ENTRY,
        "phase": "review",
        "fields": fields,
        "control": "idBtnSubmit",
        "control_value": "Next",
        "material": fields,
    }
    body = {**fields, "idBtnSubmit": "Next"}
    if change == "amount":
        body["idAmt"] = "1.01"
    elif change == "source":
        body["javax.faces.source"] = "another-command"
    elif change == "command":
        body["idBtnSubmit"] = "Other action"
    elif change == "expiry":
        auth.check.side_effect = FinanceError("Authorization expired")
    raw = urlencode(body) + ("&idAmt=1.00" if change == "duplicate" else "")
    request = SimpleNamespace(
        url=ENTRY,
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=raw,
    )
    assert provider._request_allowed(request) is False
    assert not provider._review_dispatched
    assert provider._test_request_blocked


def test_test_email_is_simple_and_has_no_internal_tracking(world):
    bill = world.payments.source.read("test_" + uuid4().hex)
    renderer = world.payments.confirmations
    target = renderer.review(bill)
    subject, body = renderer.render(bill, target, Mock())
    assert subject == "Wally Test Payment Receipt – RM1.00"
    assert "test transfer to Sean UOB" in body
    assert bill.instance_id not in body
    assert "Wally Test Payment" not in body


def test_repeat_test_emails_match_original_pdf_not_shared_subject(monkeypatch):
    from wally.adapters.google.adapter import GMAIL_API
    from wally.adapters.google.receipts import GmailReceiptMailer

    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        assert method == "GET" and url == GMAIL_API + "/users/me/messages"
        return {"messages": [{"id": "previous-test"}, {"id": "current-test"}]}

    def verify(self, details):
        inventory = self.communications._request("GET", GMAIL_API + "/users/me/messages")
        ident = inventory["messages"][0]["id"]
        if ident == "previous-test":
            raise FinanceError("Previous test PDF hash differs")
        return {"message_id": ident}

    monkeypatch.setattr(GmailReceiptMailer, "verify_receipt_email", verify)
    mailer = ReceiptTestMailer(SimpleNamespace(_request=request))
    details = {"subject": "Wally Test Payment Receipt – RM1.00", "recipient": "owner@example.test"}
    assert mailer.verify_receipt_email(details) == {"message_id": "current-test"}
    assert len(calls) == 1
    with pytest.raises(FinanceError, match="PDF hash differs"):
        mailer.verify_receipt_email({**details, "message_id": "previous-test"})


@pytest.mark.parametrize("bad_source", [False, True])
def test_independent_history_requires_exact_destination_and_source_each_read(world, bad_source):
    provider = world.payments.provider
    cells = Mock()
    cells.count.return_value = 8
    values = {2: "HLB ****" + ("0000" if bad_source else "4394"),
              3: "UOB 2203062747", 4: "MYR 1.00"}
    cells.nth.side_effect = lambda index: SimpleNamespace(inner_text=lambda: values[index])
    link = Mock()
    link.get_attribute.return_value = "j_idt100:0:test"
    link.inner_text.return_value = "BANK-REFERENCE"
    link.locator.return_value.locator.return_value = cells
    page = Mock()
    page.locator.return_value.all.return_value = [link]
    assert list(provider._history_candidates(page)) == ([] if bad_source else ["BANK-REFERENCE"])


@pytest.mark.parametrize(
    "drift",
    [None, "date", "reference", "name", "favourite_only", "missing_name", "missing_identity"],
)
def test_history_row_reordering_never_changes_the_selected_bank_reference(world, drift):
    from contextlib import contextmanager
    from datetime import datetime
    from zoneinfo import ZoneInfo

    provider = world.payments.provider
    bill = provider.source.read("test_" + uuid4().hex)
    quote = provider.prepare(bill)
    provider._recipient_name = "Verified owner"
    old, current = Mock(), Mock()
    fields = {
        "Other Payment Detail": quote.transaction, "Account Number": "2203062747",
        "Amount (MYR)": "1.00", "Recipient Reference": bill.reference,
        "Recipient Bank / Wallet Name": "UNITED OVERSEAS BANK",
        "Recipient Name": "Verified owner", "Reference Number": "BANK-CURRENT",
        "When to Transfer": datetime.fromtimestamp(
            quote.expires_at - 900, ZoneInfo("Asia/Kuala_Lumpur")
        ).strftime("%d-%b-%Y"),
    }
    if drift == "date":
        fields["When to Transfer"] = "01-Jan-2000"
    elif drift == "reference":
        fields["Reference Number"] = "ANOTHER-BANK-REFERENCE"
    elif drift == "name":
        fields["Recipient Name"] = "Different owner"
    elif drift in {"favourite_only", "missing_name", "missing_identity"}:
        provider._recipient_name = ""
        provider._recipient_favourite = "Sean UOB" if drift != "missing_identity" else ""
        if drift == "missing_name":
            fields["Recipient Name"] = ""
    pages = iter([
        SimpleNamespace(links={"BANK-OLD": old, "BANK-CURRENT": current}),
        SimpleNamespace(links={"BANK-CURRENT": current, "BANK-OLD": old},
                        fields={"Other Payment Detail": "OLD-TRACKING"}),
        SimpleNamespace(links={"BANK-CURRENT": current, "BANK-OLD": old}, fields=fields),
    ])

    @contextmanager
    def read_page(url):
        yield next(pages)

    provider._read_page = read_page
    provider._history_candidates = lambda page: page.links
    provider._rows = lambda page: page.fields
    provider._command = lambda element, action, **kwargs: action()
    if drift and drift != "favourite_only":
        with pytest.raises(FinanceError, match="contradicts"):
            provider._detail(bill, quote)
    else:
        assert provider._detail(bill, quote) == fields
    old.click.assert_called_once()
    current.click.assert_called_once()


def test_restart_restores_recipient_evidence_for_read_only_recovery(world, monkeypatch):
    from wally.finance.payments import PAYMENT_WORKER, SupervisedPayments

    payments = world.payments
    proposal = payments.prepare_new(context=payments.authority.issue("cli"))
    context = payments.authority.issue(PAYMENT_WORKER)
    payments._event(proposal.id, "test_recipient_verified", {"name": "Verified owner"})
    recovered = []
    monkeypatch.setattr(
        SupervisedPayments, "reconcile",
        lambda self, proposal_id, **kwargs: recovered.append(self.provider._recipient_name),
    )
    payments.reconcile(proposal.id, context=context)
    assert recovered == ["Verified owner"]
    payments.provider.browser.open_session.assert_not_called()


@pytest.mark.parametrize("drift", [None, "account", "bank", "favourite", "missing"])
def test_saved_favourite_identity_never_claims_a_bank_recipient_name(world, drift):
    provider = world.payments.provider
    fields = {
        "Account Number": "2203062747",
        "Recipient Bank / Wallet Name": "UNITED OVERSEAS BANK (MALAYSIA) BERHAD",
        "Favourite Account Name": "Sean UOB",
    }
    if drift == "account":
        fields["Account Number"] = "2203062748"
    elif drift == "bank":
        fields["Recipient Bank / Wallet Name"] = "Different bank"
    elif drift == "favourite":
        fields["Favourite Account Name"] = "Another favourite"
    elif drift == "missing":
        fields.pop("Favourite Account Name")
    provider._rows = lambda form: fields
    if drift:
        with pytest.raises(FinanceError, match="allowlist"):
            provider._check_recipient(Mock())
        assert provider._recipient_favourite == ""
    else:
        provider._check_recipient(Mock())
        assert provider._recipient_favourite == "Sean UOB"
    assert provider._recipient_name == ""


@pytest.mark.parametrize("favourite", ["Sean UOB", "Another favourite", ""])
def test_restart_restores_exact_favourite_separately_from_bank_name(world, monkeypatch, favourite):
    from wally.finance.payments import PAYMENT_WORKER, SupervisedPayments

    payments = world.payments
    proposal = payments.prepare_new(context=payments.authority.issue("cli"))
    payments._event(proposal.id, "test_recipient_verified", {"name": "", "favourite": favourite})
    recovered = []
    monkeypatch.setattr(SupervisedPayments, "reconcile", lambda self, *args, **kwargs:
                        recovered.append((self.provider._recipient_name,
                                          self.provider._recipient_favourite)))
    context = payments.authority.issue(PAYMENT_WORKER)
    if favourite != "Sean UOB":
        with pytest.raises(FinanceError, match="missing or ambiguous"):
            payments.reconcile(proposal.id, context=context)
        assert recovered == []
    else:
        payments.reconcile(proposal.id, context=context)
        assert recovered == [("", "Sean UOB")]
    payments.provider.browser.open_session.assert_not_called()
