"""Receipt confirmation within an exact supervised payment, after settlement only."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from wally.finance.bill_identity import require_billing_account_match
from wally.finance.models import FinanceError, digest, money, text
from wally.finance.typical_amount import comparable_period

PAYMENT_TEMPLATE = Path(__file__).resolve().parents[3] / "templates/payment_confirmation.txt"


def bank_payment_date(value: str) -> date:
    """Date-only bank receipts already express MYT; timestamps must carry an offset."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is not None:
            return timestamp.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date()
    except (ValueError, TypeError):
        pass
    raise FinanceError("The receipt requires an unambiguous actual bank payment date.")


def billing_period(start: str, end: str) -> str:
    """Label only a complete verified invoice month or calendar quarter."""
    if comparable_period("monthly", start, end):
        return date.fromisoformat(start).strftime("%B %Y")
    if comparable_period("quarterly", start, end):
        first = date.fromisoformat(start)
        return f"Q{(first.month - 1) // 3 + 1} {first.year}"
    raise FinanceError("Invoice billing period is missing or ambiguous; review the email.")


def render_confirmation(bill, target, receipt):
    # The independent bank ledger has matched this exact bill amount/digest.
    if bill.currency != "MYR":
        raise FinanceError("The receipt template requires a verified MYR payment.")
    values = {
        "account_number": text(target["account_reference"]),
        "identifier_label": target.get("identifier_label", "account"),
        "period": billing_period(bill.period_start, bill.period_end),
        "amount": money(bill.amount, bill.currency),
        "payment_date": bank_payment_date(receipt.paid_date).strftime("%d %B %Y").lstrip("0"),
    }
    try:
        rendered = PAYMENT_TEMPLATE.read_text(encoding="utf-8").format(**values)
        subject, separator, body = rendered.partition("\n\n")
        if not subject.startswith("Subject: ") or not separator or not body.strip():
            raise ValueError
        subject = text(subject.removeprefix("Subject: "))
    except (OSError, ValueError, KeyError, IndexError, AttributeError):
        raise FinanceError("Payment confirmation template needs review.") from None
    return subject, body


@dataclass(frozen=True)
class ConfirmationTarget:
    required: bool
    recipient: str
    account_reference: str
    definition_page_id: str
    binding: str
    identifier_label: str = "account"

    def validate(self):
        if type(self.required) is not bool:
            raise FinanceError("Confirmation requirement is unreadable.")
        text(self.account_reference)
        if self.identifier_label not in {"unit", "account"}:
            raise FinanceError("Confirmation account label requires review.")
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
        bank_payment_date(self.paid_date)


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

    def render(self, bill, target, receipt):
        return render_confirmation(bill, target, receipt)

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
        current = self.review(bill)
        if previous.get("state") in {"sending", "uncertain"} and "identifier_label" not in reviewed:
            current.pop("identifier_label", None)  # Legacy claims recover their frozen email only.
        if current != reviewed:
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
                    "paid_date": bank_payment_date(receipt.paid_date).isoformat(),
                    "receipt_sha256": receipt.sha256,
                    "receipt_path": str(path.resolve()),
                    "definition_page_id": reviewed["definition_page_id"],
                },
            )
        key = digest([payments.provider.transaction_namespace, settlement.reference])
        subject, body = self.render(bill, reviewed, receipt)
        details = {
            "state": "sending",
            "recipient": reviewed["recipient"],
            "rfc822_id": f"<{uuid4().hex}@wally.local>",
            "subject": subject,
            "body": body,
            "receipt_sha256": receipt.sha256,
            "receipt_path": str(path.resolve()),
            "paid_date": bank_payment_date(receipt.paid_date).isoformat(),
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
