"""Synthetic settlement, Gmail MIME/readback and durable confirmation claims."""

import base64
import hashlib
import json
from dataclasses import asdict, replace
from email import policy
from email.parser import BytesParser

import pytest

from tests.test_supervised_payments import FastBrowser
from tests.test_typical_amount import notion_source as notion_source
from wally.adapters.google.receipts import GmailReceiptMailer
from wally.finance.models import FinanceError, Kind, digest
from wally.finance.notion import FieldMapping
from wally.finance.payment_demo import build_demo, prepare_card, uid
from wally.finance.payments import PAYMENT_WORKER, CertifiedPaymentSource
from wally.finance.receipts import (
    BankReceipt,
    ConfirmationTarget,
    ReceiptConfirmations,
    bank_payment_date,
    billing_period,
    render_confirmation,
)
from wally.models.ops import ExecutionStatus, ProposalStatus

PDF = b"%PDF-1.7\nSYNTHETIC BANK RECEIPT ONLY\n%%EOF\n"


class Gmail:
    def __init__(self):
        self.sent = []
        self.calls = []
        self.lose_response = False
        self.reject_send = False
        self.change = lambda payload: None
        self.inventory = None

    def _request(self, method, url, *, params=None, json_body=None):
        self.calls.append((method, url))
        if method == "POST":
            assert url.endswith("/messages/send")
            if self.reject_send:
                raise RuntimeError("synthetic transport failure")
            message = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(json_body["raw"])
            )

            def payload(part):
                return {
                    "mimeType": part.get_content_type(),
                    "filename": part.get_filename() or "",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            part.get_payload(decode=True) or b""
                        ).decode()
                    },
                    "parts": [payload(child) for child in part.iter_parts()],
                }

            tree = payload(message)
            tree["headers"] = [{"name": k, "value": str(v)} for k, v in message.items()]
            sent = {
                "id": "sent-1",
                "threadId": "thread-1",
                "labelIds": ["SENT"],
                "internalDate": "1792713600000",
                "payload": tree,
            }
            self.change(sent)
            self.sent.append(sent)
            if self.lose_response:
                raise RuntimeError("synthetic lost send response")
            return {"id": "sent-1", "threadId": "thread-1"}
        if url.endswith("/messages"):
            assert 'subject:"Payment Receipt' in params["q"] or '"WALLY-' in params["q"]
            return (
                self.inventory
                if self.inventory is not None
                else {
                    "messages": [{"id": sent["id"]} for sent in self.sent],
                    "resultSizeEstimate": len(self.sent),
                }
            )
        if url.endswith("/messages/sent-1"):
            return self.sent[0]
        raise AssertionError("Unexpected synthetic Gmail request")

    @property
    def send_count(self):
        return sum(method == "POST" for method, _ in self.calls)


@pytest.fixture
def world(tmp_path):
    state = build_demo(tmp_path, browser=FastBrowser())
    state.gmail = Gmail()
    state.target = ConfirmationTarget(True, "provider@example.test", "CSM 020", uid(11), "binding")
    state.source.bill = replace(
        state.source.bill, account_reference="CSM 020", invoice_account_reference="CSM 020"
    )
    state.browser.quote_bill = state.source.bill
    state.source.read_confirmation = lambda _: state.target
    state.receipts = []

    def receipt(bill, quote, settlement):
        state.receipts.append(settlement.reference)
        return BankReceipt(
            quote.transaction, digest(asdict(bill)), settlement.reference, "2026-10-23", PDF
        )

    state.provider.receipt = receipt
    state.payments.confirmations = ReceiptConfirmations(
        state.source, GmailReceiptMailer(state.gmail), tmp_path / "receipts"
    )
    yield state
    state.notion.client.close()


def email_state(world):
    with world.store._connect() as connection:
        row = connection.execute("SELECT confirmation FROM payment_attempts").fetchone()
    return json.loads(row[0]) if row and row[0] else {}


def reconcile(world, proposal):
    return world.payments.reconcile(proposal.id, context=world.authority.issue(PAYMENT_WORKER))


def test_payment_approval_covers_exact_receipt_email_and_replays_do_not_resend(world):
    proposal, callback = prepare_card(world)
    card = str(world.transport.sent[0]["text"])
    assert "After verified payment, email the bank receipt to provider@example.test" in card
    assert world.gmail.send_count == 0
    world.telegram.handle_update(callback)
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    result = email_state(world)
    assert result["state"] == "sent"
    assert result["recipient"] == "provider@example.test"
    assert result["message_id"] == "sent-1"
    assert result["receipt_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert result["subject"] == "Payment Receipt – CSM 020 – January 2027"
    assert result["body"] == (
        "Hi team,\n\n"
        "Please find attached the bank payment receipt\nfor account CSM 020.\n"
        "Invoice number: SANDBOX-INV-2027-01\n\n"
        "Amount paid: RM100.00\nPayment date: 23 October 2026\n\nThank you.\n"
    )
    outgoing = result["subject"] + result["body"]
    for internal in (
        "WALLY", proposal.id, world.source.bill.instance_id, world.source.bill.payee,
        world.browser.receipt,
    ):
        assert internal not in outgoing
    attachment = world.gmail.sent[0]["payload"]["parts"][1]
    assert base64.urlsafe_b64decode(attachment["body"]["data"]) == PDF
    assert "Receipt email verified sent" in world.approvals.result(proposal.id)
    assert "Email status in Notion is unconfirmed" not in world.approvals.result(proposal.id)
    page = next(iter(world.notion.pages.values()))
    recorded = json.loads(page["properties"]["evidence"]["rich_text"][0]["text"]["content"])
    assert recorded["confirmation"]["state"] == "sent"
    assert recorded["confirmation"]["recipient"] == "provider@example.test"
    assert recorded["confirmation"]["message_id"] == "sent-1"
    world.telegram.handle_update({**callback, "update_id": 2})
    reconcile(world, proposal)
    assert world.gmail.send_count == world.browser.submissions == world.notion.creates == 1


@pytest.mark.parametrize("status", ["pending", "failed", "unknown"])
def test_unverified_payment_never_obtains_or_emails_receipt(world, status):
    world.browser.ledger_status = status
    _, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.gmail.send_count == 0
    assert world.receipts == []
    assert world.notion.creates == 0


def test_pending_payment_can_later_verify_and_email_once(world):
    world.browser.ledger_status = "pending"
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.gmail.send_count == 0
    world.browser.ledger_status = "settled"
    reconcile(world, proposal)
    reconcile(world, proposal)
    assert email_state(world)["state"] == "sent"
    assert world.gmail.send_count == world.browser.submissions == 1


@pytest.mark.parametrize(
    "change", [{"recipient": "other@example.test"}, {"required": False}, {"binding": "new-mapping"}]
)
def test_destination_or_requirement_drift_requires_new_review(world, change):
    proposal, callback = prepare_card(world)
    world.target = replace(world.target, **change)
    world.telegram.handle_update(callback)
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED
    assert world.browser.submissions == world.gmail.send_count == 0
    assert world.secrets.resolve_calls == []


def test_destination_drift_after_bank_settlement_is_email_follow_up(world):
    proposal, callback = prepare_card(world)
    world.browser.on_ledger = lambda: setattr(
        world, "target", replace(world.target, recipient="changed@example.test")
    )
    world.telegram.handle_update(callback)
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.gmail.send_count == 0
    assert "email needs follow-up" in world.approvals.result(proposal.id)


def test_lost_email_response_recovers_by_gmail_read_without_resend_or_repayment(world):
    world.gmail.lose_response = True
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert email_state(world)["state"] == "uncertain"
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    assert "email needs follow-up" in world.approvals.result(proposal.id)
    reconcile(world, proposal)
    assert email_state(world)["state"] == "sent"
    assert world.gmail.send_count == world.browser.submissions == world.notion.creates == 1
    page = next(iter(world.notion.pages.values()))
    recorded = json.loads(page["properties"]["evidence"]["rich_text"][0]["text"]["content"])
    assert recorded["confirmation"]["state"] == "sent"


def test_ambiguous_notion_email_status_update_recovers_by_read(world):
    original = world.notion.handle

    def lose_patch_response(request):
        response = original(request)
        if request.method == "PATCH":
            raise RuntimeError("synthetic lost Notion response")
        return response

    world.notion.handle = lose_patch_response
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert "Email status in Notion is unconfirmed" in world.approvals.result(proposal.id)
    reconcile(world, proposal)
    assert "Email status in Notion is unconfirmed" not in world.approvals.result(proposal.id)
    assert world.notion.patches == world.gmail.send_count == world.browser.submissions == 1


def test_email_failure_retains_follow_up_without_retrying_bank_or_email(world):
    world.gmail.reject_send = True
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert email_state(world)["state"] == "uncertain"
    reconcile(world, proposal)
    assert world.gmail.send_count == world.browser.submissions == world.notion.creates == 1
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS


def test_unrequired_confirmation_sends_no_mail(world):
    world.target = replace(world.target, required=False, recipient="")
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert email_state(world)["state"] == "not_required"
    assert world.gmail.send_count == 0
    assert "email needs follow-up" not in world.approvals.result(proposal.id)


def test_unmatched_bank_receipt_never_emails(world):
    original = world.provider.receipt
    world.provider.receipt = lambda *args: replace(original(*args), reference="unrelated")
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.gmail.send_count == 0
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    assert "email needs follow-up" in world.approvals.result(proposal.id)


@pytest.mark.parametrize("address", ["", "a@b.test\r\nBcc: x@y.test", "a@b.test,c@d.test"])
def test_invalid_recipient_blocks_preparation(world, address):
    world.target = replace(world.target, recipient=address)
    with pytest.raises(FinanceError):
        prepare_card(world)
    assert world.browser.submissions == world.gmail.send_count == 0


@pytest.mark.parametrize("changed", ["attachment", "recipient", "status", "message-id", "body"])
def test_gmail_readback_must_match_original_receipt_and_envelope(world, changed):
    def alter(sent):
        if changed == "attachment":
            sent["payload"]["parts"][1]["body"]["data"] = base64.urlsafe_b64encode(
                b"other"
            ).decode()
        elif changed == "status":
            sent["labelIds"] = ["DRAFT"]
        elif changed == "body":
            sent["payload"]["parts"][0]["body"]["data"] = base64.urlsafe_b64encode(
                b"other"
            ).decode()
        else:
            header = "To" if changed == "recipient" else "Message-ID"
            next(h for h in sent["payload"]["headers"] if h["name"] == header)["value"] = "other"

    world.gmail.change = alter
    _, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert email_state(world)["state"] == "uncertain"
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS


@pytest.mark.parametrize(
    "inventory",
    [
        {},
        {"messages": [{"id": "a"}, {"id": "b"}]},
        {"nextPageToken": "more", "resultSizeEstimate": 0},
    ],
)
def test_incomplete_gmail_duplicate_inventory_cannot_send(world, inventory):
    world.gmail.inventory = inventory
    _, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.gmail.send_count == 0
    assert email_state(world)["state"] == "uncertain"


def test_linked_provider_confirmation_uses_stable_ids_and_live_values(notion_source):
    fixture, catalog, instance, _, _ = notion_source
    index = next(i for i, source in enumerate(fixture.sources) if source.kind == Kind.PROVIDER)
    source = fixture.sources[index]
    mapped = replace(
        source,
        payment_review_fields={
            "confirmation_required": FieldMapping("flag-id", "checkbox"),
            "confirmation_email": FieldMapping("mail-id", "email"),
        },
    )
    fixture.sources[index] = mapped
    assert mapped.fingerprint == source.fingerprint
    fixture.schemas[source.data_source_id]["properties"].update(
        {
            "Renamed flag": {"id": "flag-id", "type": "checkbox"},
            "Renamed address": {"id": "mail-id", "type": "email"},
        }
    )
    provider = fixture.page(Kind.PROVIDER)
    provider["properties"].update(
        {
            "Renamed flag": {"id": "flag-id", "type": "checkbox", "checkbox": True},
            "Renamed address": {"id": "mail-id", "type": "email", "email": "live@example.test"},
        }
    )
    payment_source = CertifiedPaymentSource(catalog)
    target = payment_source.read_confirmation(instance.id)
    assert target.required is True
    assert target.recipient == "live@example.test"
    assert target.account_reference == "000123456789"
    assert target.identifier_label == "account"  # Utility customer number, not the unit.
    unit = fixture.page(Kind.PROPERTY)["properties"]["unit"]["rich_text"][0]
    unit["plain_text"] = unit["text"]["content"] = "000123456789"
    assert payment_source.read_confirmation(instance.id).identifier_label == "unit"
    provider["properties"]["Renamed address"]["email"] = "changed@example.test"
    changed = payment_source.read_confirmation(instance.id)
    assert changed.recipient == "changed@example.test"
    assert changed.binding != target.binding
    fixture.schemas[source.data_source_id]["properties"]["Renamed address"]["id"] = "recreated"
    with pytest.raises(FinanceError, match="schema changed"):
        payment_source.read_confirmation(instance.id)


def test_gmail_rewritten_message_id_recovers_lost_send_without_body_reference(world):
    def rewrite(sent):
        for header in sent["payload"]["headers"]:
            if header["name"].casefold() == "message-id":
                header["value"] = "<gmail-rewritten@mail.gmail.com>"

    world.gmail.change = rewrite
    world.gmail.lose_response = True
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert email_state(world)["state"] == "uncertain"
    reconcile(world, proposal)
    assert email_state(world)["state"] == "sent"
    assert email_state(world)["sent_rfc822_id"] == "<gmail-rewritten@mail.gmail.com>"
    assert world.gmail.send_count == world.browser.submissions == 1


def test_rewritten_message_id_still_requires_exact_body_and_pdf(world):
    def rewrite(sent):
        sent["payload"]["parts"][0]["body"]["data"] = base64.urlsafe_b64encode(
            b"wrong body"
        ).decode()

    world.gmail.change = rewrite
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert email_state(world)["state"] == "uncertain"
    reconcile(world, proposal)
    assert world.gmail.send_count == world.browser.submissions == 1


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-10-01", "2026-12-31", "Q4 2026"),
        ("2027-01-01", "2027-03-31", "Q1 2027"),
        ("2026-10-01", "2026-10-31", "October 2026"),
        ("2028-02-01", "2028-02-29", "February 2028"),
    ],
)
def test_verified_invoice_period_labels(start, end, expected):
    assert billing_period(start, end) == expected


@pytest.mark.parametrize(
    "start,end",
    [
        ("", ""), ("2026-10-01", ""), ("", "2026-12-31"),
        ("2026-10-01", "2026-12-30"), ("2026-10-02", "2026-12-31"),
        ("2026-11-01", "2027-01-31"), ("2026-12-31", "2026-10-01"),
        ("Q4 2026", "Q4 2026"),
    ],
)
def test_missing_or_ambiguous_period_holds_email(world, start, end):
    bill = replace(world.source.bill, period_start=start, period_end=end)
    receipt = BankReceipt("", "", "", "2026-10-23", PDF)
    with pytest.raises(FinanceError, match="billing period"):
        render_confirmation(bill, asdict(world.target), receipt)
    assert world.gmail.send_count == 0


def test_ambiguous_period_keeps_settlement_and_holds_email_for_follow_up(world):
    world.source.bill = replace(world.source.bill, period_end="2027-01-30")
    world.browser.quote_bill = world.source.bill
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    reconcile(world, proposal)
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    assert "email needs follow-up" in world.approvals.result(proposal.id)
    assert world.gmail.send_count == 0
    assert world.browser.submissions == world.notion.creates == 1
    path = world.payments.confirmations.evidence_directory / proposal.id / "bank-receipt.pdf"
    assert path.read_bytes() == PDF


def test_tropicana_example_renders_verified_invoice_number_after_unit(world):
    bill = replace(
        world.source.bill, amount="960.96", period_start="2026-10-01", period_end="2026-12-31",
        payee="Tropicana Gardens", reference="PRIVATE-INVOICE-123",
    )
    target = replace(world.target, account_reference="B2-28-03A", identifier_label="unit")
    receipt = BankReceipt("", "", "", "2026-10-23", PDF)
    subject, body = render_confirmation(bill, asdict(target), receipt)
    assert subject == "Payment Receipt – B2-28-03A – Q4 2026"
    assert body == (
        "Hi team,\n\n"
        "Please find attached the bank payment receipt\nfor unit B2-28-03A.\n"
        "Invoice number: PRIVATE-INVOICE-123\n\n"
        "Amount paid: RM960.96\nPayment date: 23 October 2026\n\nThank you.\n"
    )
    assert bill.payee not in subject + body
    assert bill.reference not in subject


@pytest.mark.parametrize("reference", ["", None])
def test_missing_invoice_number_omits_entire_line_and_preserves_email(world, reference):
    bill = replace(world.source.bill, reference=reference)
    receipt = BankReceipt("", "", "", "2026-10-23", PDF)
    subject, body = render_confirmation(bill, asdict(world.target), receipt)
    assert subject == "Payment Receipt – CSM 020 – January 2027"
    assert body == (
        "Hi team,\n\n"
        "Please find attached the bank payment receipt\nfor account CSM 020.\n\n"
        "Amount paid: RM100.00\nPayment date: 23 October 2026\n\nThank you.\n"
    )


@pytest.mark.parametrize("reference", ["INV-123\nInjected line", "INV-123\rInjected line"])
def test_invoice_number_rejects_control_characters(world, reference):
    bill = replace(world.source.bill, reference=reference)
    receipt = BankReceipt("", "", "", "2026-10-23", PDF)
    with pytest.raises(FinanceError, match="Control characters"):
        render_confirmation(bill, asdict(world.target), receipt)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-10-23", "2026-10-23"),
        ("2026-10-22T17:00:00Z", "2026-10-23"),
        ("2026-10-23T23:30:00-04:00", "2026-10-24"),
    ],
)
def test_actual_bank_date_is_in_malaysia_time(value, expected):
    assert bank_payment_date(value).isoformat() == expected


@pytest.mark.parametrize("value", ["", "2026-10-23T01:00:00", "2026-02-30", "yesterday"])
def test_missing_or_ambiguous_bank_date_requires_review(value):
    with pytest.raises(FinanceError, match="bank payment date"):
        bank_payment_date(value)


def test_editing_plain_text_template_changes_new_email_only(world, tmp_path, monkeypatch):
    from wally.finance import receipts

    template = tmp_path / "payment_confirmation.txt"
    template.write_text(
        "Subject: Receipt for {account_number} – {period}\n\n"
        "Hello,\nRM{amount} paid on {payment_date} for {identifier_label} {account_number}.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(receipts, "PAYMENT_TEMPLATE", template)
    bill = world.source.bill
    receipt = BankReceipt("", "", "", "2026-10-23", PDF)
    subject, body = render_confirmation(bill, asdict(world.target), receipt)
    assert subject == "Receipt for CSM 020 – January 2027"
    assert body == "Hello,\nRM100.00 paid on 23 October 2026 for account CSM 020.\n"


def test_already_sent_legacy_email_is_never_rerendered_or_resent(world, tmp_path, monkeypatch):
    from wally.finance import receipts

    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    previous = {**email_state(world), "subject": "Legacy subject", "body": "Legacy WALLY reference"}
    world.payments.confirmations._save(world.store, proposal.id, previous)
    monkeypatch.setattr(receipts, "PAYMENT_TEMPLATE", tmp_path / "missing.txt")
    reconcile(world, proposal)
    assert email_state(world)["body"] == previous["body"]
    assert world.gmail.send_count == world.browser.submissions == 1


def test_legacy_uncertain_claim_recovers_frozen_email_without_resend(world):
    world.gmail.lose_response = True
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    _, spec, bill, quote = world.payments.load(proposal.id)
    reviewed = dict(spec["confirmation"])
    reviewed.pop("identifier_label")
    settlement = world.provider.verify(bill, quote)
    result = world.payments.confirmations.finish(
        world.payments, proposal, {**spec, "confirmation": reviewed}, bill, quote, settlement,
        guard=lambda: None,
    )
    assert result["state"] == "sent"
    assert world.gmail.send_count == world.browser.submissions == 1


@pytest.mark.parametrize("template", [None, "Subject: {unknown}\n\nHello", "Hello"])
def test_missing_or_invalid_template_holds_email(world, tmp_path, monkeypatch, template):
    from wally.finance import receipts

    path = tmp_path / "payment_confirmation.txt"
    if template is not None:
        path.write_text(template, encoding="utf-8")
    monkeypatch.setattr(receipts, "PAYMENT_TEMPLATE", path)
    proposal, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    assert "email needs follow-up" in world.approvals.result(proposal.id)
    assert world.gmail.send_count == 0
    assert world.browser.submissions == 1
