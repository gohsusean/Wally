"""Receipt confirmation within an exact supervised payment, after settlement only."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from wally.finance.bill_identity import require_billing_account_match
from wally.finance.models import FinanceError, digest, text


@dataclass(frozen=True)
class ConfirmationTarget:
    required: bool
    recipient: str
    account_reference: str
    definition_page_id: str
    binding: str

    def validate(self):
        if type(self.required) is not bool:
            raise FinanceError("Confirmation requirement is unreadable.")
        text(self.account_reference)
        if self.required and not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", self.recipient
        ):
            raise FinanceError("A single current confirmation email address is required.")


@dataclass(frozen=True)
class BankReceipt:
    transaction: str
    bill_digest: str
    reference: str
    paid_date: str
    content: bytes = field(repr=False)

    @property
    def sha256(self):
        return hashlib.sha256(self.content).hexdigest()

    def validate(self, bill, quote, settlement):
        if (
            settlement.status != "settled"
            or self.transaction != quote.transaction
            or self.bill_digest != digest(asdict(bill))
            or self.reference != settlement.reference
            or not self.content.startswith(b"%PDF-")
            or not 20 <= len(self.content) <= 10_000_000
        ):
            raise FinanceError("The actual bank receipt is not bound to this verified payment.")
        if date.fromisoformat(self.paid_date).isoformat() != self.paid_date:
            raise FinanceError("The receipt requires the actual bank transaction date.")


class ReceiptConfirmations:
    """One durable email claim per bank reference; ambiguous sends recover by reads.

    The trusted bank adapter must obtain/independently match the original PDF.
    No document is synthesized from a successful login or transfer response.
    """

    def __init__(self, source, mailer, evidence_directory: Path):
        self.source, self.mailer = source, mailer
        self.evidence_directory = evidence_directory

    def review(self, bill):
        target = self.source.read_confirmation(bill.instance_id)
        target.validate()
        require_billing_account_match(bill.account_reference, target.account_reference)
        return asdict(target)

    def finish(self, payments, proposal, spec, bill, quote, settlement, *, guard):
        store = payments.store
        reviewed = spec.get("confirmation")
        if reviewed is None:
            raise FinanceError("The payment did not review a confirmation scope.")
        with store._connect() as connection:
            row = connection.execute(
                "SELECT confirmation FROM payment_attempts WHERE proposal_id=?", (proposal.id,)
            ).fetchone()
        previous = json.loads(row[0]) if row and row[0] else {}
        if previous.get("state") in {"sent", "not_required"}:
            return previous
        if settlement.status != "settled":
            raise FinanceError("Only independently settled payments can email a receipt.")
        if self.review(bill) != reviewed:
            raise FinanceError("Confirmation destination changed; retain a follow-up.")
        # A lost send response/crash may already have sent mail. Never send again.
        if previous.get("state") in {"sending", "uncertain"}:
            guard()
            evidence = self.mailer.verify_receipt_email(previous)
            if evidence:
                return self._save(store, proposal.id, {**previous, **evidence, "state": "sent"})
            return self._save(store, proposal.id, {**previous, "state": "uncertain"})
        if previous:
            return previous  # Explicit operator investigation, never an automatic retry.
        guard()
        receipt = payments.provider.receipt(bill, quote, settlement)
        receipt.validate(bill, quote, settlement)
        directory = self.evidence_directory / proposal.id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / "bank-receipt.pdf"
        try:
            with path.open("xb") as output:
                output.write(receipt.content)
            path.chmod(0o600)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != receipt.sha256:
                raise FinanceError("Retained receipt changed; investigate before email.") from None
        if not reviewed["required"]:
            return self._save(
                store,
                proposal.id,
                {
                    "state": "not_required",
                    "paid_date": receipt.paid_date,
                    "receipt_sha256": receipt.sha256,
                    "receipt_path": str(path.resolve()),
                    "definition_page_id": reviewed["definition_page_id"],
                },
            )
        key = digest([payments.provider.transaction_namespace, settlement.reference])
        confirmation_reference = f"WALLY-{key}"
        subject = f"Payment confirmation — {bill.payee} — {reviewed['account_reference']}"
        body = (
            f"Payment for {reviewed['account_reference']}\n"
            f"Billing period: {bill.period_start} to {bill.period_end}\n"
            f"Amount: {bill.currency} {bill.amount}\n"
            f"Payment date: {receipt.paid_date}\n"
            f"Bank reference: {settlement.reference}\n"
            f"Invoice/reference: {bill.reference}\n\n"
            "The bank payment receipt is attached.\n"
            f"Confirmation reference: {confirmation_reference}\n"
        )
        details = {
            "state": "sending",
            "recipient": reviewed["recipient"],
            "rfc822_id": f"<wally-payment-{key}@wally.local>",
            "confirmation_reference": confirmation_reference,
            "subject": subject,
            "body": body,
            "receipt_sha256": receipt.sha256,
            "receipt_path": str(path.resolve()),
            "paid_date": receipt.paid_date,
            "definition_page_id": reviewed["definition_page_id"],
        }
        guard()
        if self.review(bill) != reviewed:
            raise FinanceError("Confirmation destination changed before send.")
        # Reserve BEFORE the potentially irreversible send, including a lost response.
        try:
            with store._connect() as connection:
                claimed = connection.execute(
                    "UPDATE payment_attempts SET confirmation=?,confirmation_key=? "
                    "WHERE proposal_id=? AND confirmation=''",
                    (json.dumps(details), key, proposal.id),
                ).rowcount
        except sqlite3.IntegrityError:
            raise FinanceError("This bank receipt already has an email claim.") from None
        if not claimed:
            raise FinanceError("Confirmation already claimed; recover by reading Gmail.")
        try:
            guard()
            evidence = self.mailer.verify_receipt_email(details)
            if not evidence:
                guard()
                if self.review(bill) != reviewed:
                    raise FinanceError("Confirmation destination changed before send.")
                sent = self.mailer.send_receipt_email(details, receipt.content)
                if isinstance(sent, dict) and sent.get("id"):
                    details = {**details, "message_id": sent["id"]}
                    self._save(store, proposal.id, details)
                evidence = self.mailer.verify_receipt_email(details)
            if not evidence:
                raise FinanceError("Gmail has not independently confirmed the receipt email.")
            return self._save(store, proposal.id, {**details, **evidence, "state": "sent"})
        except Exception:
            self._save(store, proposal.id, {**details, "state": "uncertain"})
            raise FinanceError("Receipt email is unconfirmed; never retry the transfer.") from None

    @staticmethod
    def _save(store, proposal_id, details):
        details = {**details, "checked_at": datetime.now(UTC).isoformat()}
        with store._connect() as connection:
            connection.execute(
                "UPDATE payment_attempts SET confirmation=? WHERE proposal_id=?",
                (json.dumps(details), proposal_id),
            )
        return details
