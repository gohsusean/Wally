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
from wally.finance.receipts import BankReceipt, ConfirmationTarget, ReceiptConfirmations
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
            assert "in:sent rfc822msgid:" in params["q"]
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
    assert "CSM 020" in result["body"]
    assert "2027-01-01 to 2027-01-31" in result["body"]
    assert "MYR 100.00" in result["body"]
    assert "2026-10-23" in result["body"]
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
    provider["properties"]["Renamed address"]["email"] = "changed@example.test"
    changed = payment_source.read_confirmation(instance.id)
    assert changed.recipient == "changed@example.test"
    assert changed.binding != target.binding
    fixture.schemas[source.data_source_id]["properties"]["Renamed address"]["id"] = "recreated"
    with pytest.raises(FinanceError, match="schema changed"):
        payment_source.read_confirmation(instance.id)
