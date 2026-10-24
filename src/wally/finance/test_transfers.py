"""Narrow owner-account acceptance transfers; ordinary bill rules stay intact."""

import json
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import yaml

from wally.adapters.google.adapter import GMAIL_API
from wally.adapters.google.receipts import GmailReceiptMailer
from wally.finance.models import FinanceError, digest
from wally.finance.payments import BillPayment, SupervisedPayments
from wally.finance.receipts import ConfirmationTarget, ReceiptConfirmations

APPROVED_TEST = {
    "schema_version": 1,
    "source_bank": "HLB",
    "funding_last4": "4394",
    "destination_bank": "UOB",
    "favourite": "Sean UOB",
    "destination_account": "2203062747",
    "currency": "MYR",
    "amount": "1.00",
    "receipt_email": "gohsusean@gmail.com",
}
TEST_ACCOUNT = str(uuid5(NAMESPACE_URL, "wally:owner-account-test:sean-uob"))


def load_test_policy(path: Path):
    try:
        policy = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        raise FinanceError("Owner-account test configuration is unavailable.") from None
    if policy != APPROVED_TEST:
        raise FinanceError("Test beneficiary or amount changed; explicit owner review is required.")
    return policy


class OwnerAccountTestSource:
    required_transaction_namespace = "https://s.hongleongconnect.my"

    def __init__(self, policy_path):
        self.policy_path = policy_path

    @property
    def policy(self):
        return load_test_policy(self.policy_path)

    def read(self, instance_id):
        if not re.fullmatch(r"test_[a-f0-9]{32}", instance_id):
            raise FinanceError("An explicit distinct owner-account test attempt is required.")
        p = self.policy
        return BillPayment(
            instance_id,
            TEST_ACCOUNT,
            instance_id,
            p["favourite"],
            p["destination_bank"] + ":" + p["destination_account"],
            p["amount"],
            p["currency"],
            "Wally Test Payment",
            "",
            "",
            digest(p),
            digest(["owner_account_test", p]),
        )

    def read_confirmation(self, instance_id):
        self.read(instance_id)
        return ConfirmationTarget(
            True,
            self.policy["receipt_email"],
            "Owner-account test transfer",
            "",
            digest(self.policy),
        )


class LocalTestHistory:
    fingerprint = digest(["owner_account_test_local_history", 1])

    def __init__(self, store):
        self.store = store

    def check(self, bill):
        # A different attempt cannot evade an uncertain test outcome.
        with self.store._connect() as c:
            rows = c.execute(
                "SELECT s.spec,e.outcome FROM payment_attempts a "
                "JOIN payment_specs s ON s.proposal_id=a.proposal_id "
                "LEFT JOIN executions e ON e.id=a.execution_id"
            ).fetchall()
        for row in rows:
            spec = json.loads(row["spec"])
            if (
                spec.get("kind") == "owner_account_test"
                and spec["bill"]["instance_id"] != bill.instance_id
                and row["outcome"] not in {"settled", "not_submitted", "bank_failed"}
            ):
                raise FinanceError(
                    "A previous test outcome is uncertain; reconcile before a new test."
                )

    def record(self, bill, settlement, *, guard):
        guard()
        # Settlement is already durable in payment_attempts; no Notion bill is created.
        self.verify_record(bill, settlement)

    def verify_record(self, bill, settlement):
        with self.store._connect() as c:
            rows = c.execute(
                "SELECT a.settlement,s.spec FROM payment_attempts a "
                "JOIN payment_specs s ON s.proposal_id=a.proposal_id WHERE a.settlement!=''"
            ).fetchall()
        matches = [
            r
            for r in rows
            if json.loads(r["spec"])["bill"] == asdict(bill)
            and json.loads(r["settlement"]) == asdict(settlement)
        ]
        if len(matches) != 1:
            raise FinanceError("Local test settlement readback is ambiguous.")

    def record_confirmation(self, bill, settlement, details, *, guard):
        guard()
        self.verify_record(bill, settlement)
        with self.store._connect() as c:
            rows = c.execute(
                "SELECT a.confirmation,s.spec FROM payment_attempts a "
                "JOIN payment_specs s ON s.proposal_id=a.proposal_id WHERE a.confirmation!=''"
            ).fetchall()
        matches = [
            r
            for r in rows
            if json.loads(r["spec"])["bill"] == asdict(bill)
            and json.loads(r["confirmation"]) == details
        ]
        if len(matches) != 1:
            raise FinanceError("Local test email evidence readback is ambiguous.")


class TestReceiptConfirmations(ReceiptConfirmations):
    def review(self, bill):
        self.source.read(bill.instance_id)  # Exact immutable test allowlist, no invoice gate.
        target = self.source.read_confirmation(bill.instance_id)
        target.validate()
        return asdict(target)

    def render(self, bill, target, receipt):
        if bill != self.source.read(bill.instance_id):
            raise FinanceError("Test transfer changed before receipt email.")
        return (
            "Wally Test Payment Receipt – RM1.00",
            "Hi,\n\nAttached is the bank receipt for the RM1.00\n"
            "test transfer to Sean UOB.\n\nThank you.\n",
        )


class TestReceiptMailer(GmailReceiptMailer):
    """Repeated RM1 tests share a subject, but each original bank PDF is distinct."""

    def verify_receipt_email(self, details):
        request = self.communications._request
        if details.get("message_id"):
            rows = [{"id": details["message_id"]}]
        else:
            subject = details["subject"].replace("\\", "\\\\").replace('"', '\\"')
            inventory = request(
                "GET",
                GMAIL_API + "/users/me/messages",
                params={
                    "q": f'in:sent to:{details["recipient"]} subject:"{subject}"',
                    "maxResults": 100,
                },
            )
            if (
                not isinstance(inventory, dict)
                or inventory.get("nextPageToken")
                or ("messages" not in inventory and inventory.get("resultSizeEstimate") != 0)
            ):
                raise FinanceError("Test receipt email inventory is incomplete.")
            rows = inventory.get("messages", [])
        matches = []
        for row in rows:

            def scoped_request(method, url, *, row=row, **kwargs):
                if method == "GET" and url == GMAIL_API + "/users/me/messages":
                    return {"messages": [row]}
                return request(method, url, **kwargs)

            verifier = GmailReceiptMailer(SimpleNamespace(_request=scoped_request))
            try:
                match = verifier.verify_receipt_email(details)
            except FinanceError:
                if details.get("message_id"):
                    raise  # A known send must match; never replace it with another email.
                continue  # A prior distinct test receipt is not this payment's PDF.
            if match:
                matches.append(match)
        if len(matches) > 1:
            raise FinanceError("Duplicate matching test receipt emails require investigation.")
        return matches[0] if matches else None


class OwnerAccountTestPayments(SupervisedPayments):
    payment_kind = "owner_account_test"

    def _gate(self, *, preparation=False):
        _ = self.source.policy
        if (
            type(self.source) is not OwnerAccountTestSource
            or self.provider.transaction_namespace != self.source.required_transaction_namespace
            or not self.enabled()
        ):
            raise FinanceError("The narrow owner-account test route is disabled.")
        _ = self.provider.fingerprint
        if not preparation and (
            self.confirmations is None or not callable(getattr(self.provider, "receipt", None))
        ):
            raise FinanceError("Test receipt handling is unavailable.")

    def _validate_bill(self, bill):
        if bill != self.source.read(bill.instance_id):
            raise FinanceError("Test destination, bank or amount differs from the owner allowlist.")

    def _typical_amount_review(self, bill):
        return None  # No Typical Amount, invoice, due date or billing-period semantics.

    def identify_unpaid(self, *args, **kwargs):
        raise FinanceError("An owner-account test is not a bill obligation.")

    def prepare_new(self, *, context):
        return self.prepare("test_" + uuid4().hex, context=context)

    def _event(self, proposal_id, event, details):
        if event.startswith("notion_"):
            event = "test_local_" + event.removeprefix("notion_")
        return super()._event(proposal_id, event, details)
