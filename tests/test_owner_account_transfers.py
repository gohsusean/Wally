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
    _, spec, bill, _ = world.payments.load(proposal.id)
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
    page = SimpleNamespace(frames=[frame], wait_for_load_state=Mock())
    if target == ENTRY + "?o=1":
        provider._enter_transfer(page)
        anchors.filter.return_value.click.assert_called_once()
        anchors.filter.return_value.hover.assert_not_called()
        link.wait_for.assert_called_once_with(state="visible", timeout=5000)
        link.click.assert_called_once()
    else:
        with pytest.raises(FinanceError, match="menu destination changed"):
            provider._enter_transfer(page)
        link.click.assert_not_called()


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


def test_jsf_request_exactness_replay_and_secret_redaction(world):
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
    }
    request = SimpleNamespace(
        url=ENTRY,
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=urlencode({**fields, "idBtnSubmit": "Next"}),
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
