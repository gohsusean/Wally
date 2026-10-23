"""Private delivered Telegram card -> two exact-purpose runtime confirmations."""

from __future__ import annotations

import time
from contextlib import suppress
from datetime import UTC, datetime
from html import escape

from wally.finance.payments import PAYMENT_WORKER
from wally.models.ops import ExecutionStatus, ProposalIntent, ProposalStatus
from wally.presentation import TelegramHTML, human_time
from wally.runtime.confirmation import canonical, digest
from wally.telegram.notion_approvals import _Binding
from wally.telegram.outbox import NotificationOutbox


class TelegramPaymentApprovals:
    def __init__(self, payments, confirmation, owner_user_id):
        self.payments, self.confirmation = payments, confirmation
        self.owner_user_id = owner_user_id
        self.store = payments.store
        self.outbox = NotificationOutbox(self.store)
        self.dispatch_guard = lambda: False
        self.notify_progress = lambda proposal_id: None
        with self.store._connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS telegram_payment_reviews ("
                "notification_id TEXT PRIMARY KEY, expires_at REAL NOT NULL, "
                "state TEXT NOT NULL DEFAULT 'open', callback_id TEXT NOT NULL DEFAULT '')"
            )

    def identify_unpaid(self, instance_id, update_id, chat_id):
        context = self.payments.authority.issue(
            "telegram",
            external_request_ref=f"tg-{update_id}",
            external_session_ref=chat_id,
        )
        try:
            self.payments.identify_unpaid(instance_id, context=context)
        except Exception:
            return "That exact obligation could not be identified. No payment was authorized."
        return "Recorded that specific bill as unpaid. Its payment still needs a separate approval."

    def sync(self, chat_id, now, **_):
        if not chat_id or not self.owner_user_id:
            return
        for proposal in self.store.list_proposals():
            if proposal.intent != ProposalIntent.PAY_BILL:
                continue
            with self.store._connect() as c:
                attempt = c.execute(
                    "SELECT * FROM payment_attempts WHERE proposal_id=?", (proposal.id,)
                ).fetchone()
            if attempt:
                execution = self.store.get_execution(attempt["execution_id"])
                self.outbox.enqueue_status(
                    kind="payment_result",
                    execution_id=proposal.id,
                    proposal_id=proposal.id,
                    fingerprint=digest(
                        [
                            execution.outcome if execution else "interrupted",
                            attempt["reconciliation"],
                        ]
                    ),
                    owner_user_id=self.owner_user_id,
                    chat_id=chat_id,
                    now=now,
                )
            elif (
                proposal.status == ProposalStatus.PROPOSED
                and datetime.fromisoformat(proposal.expires_at).timestamp() > time.time()
            ):
                row = self.outbox.enqueue_decision(
                    proposal_id=proposal.id,
                    fingerprint=proposal.fingerprint,
                    owner_user_id=self.owner_user_id,
                    chat_id=chat_id,
                    now=now,
                    kind="payment_review",
                    allowed_actions="approve_execute,reject",
                )
                with self.store._connect() as c:
                    c.execute(
                        "INSERT OR IGNORE INTO telegram_payment_reviews "
                        "(notification_id,expires_at) VALUES (?,?)",
                        (row.id, datetime.fromisoformat(proposal.expires_at).timestamp()),
                    )

    def render(self, row):
        proposal, spec, bill, quote = self.payments.load(row.proposal_id)
        prefix = "SIMULATED PAYMENT — no money moves\n\n" if spec["sandbox"] else "Pay bill\n\n"
        body = (
            prefix
            + escape(bill.payee)
            + "\n"
            + escape(f"{bill.currency} {bill.amount} · {bill.occurrence}")
            + "\n"
            + escape(f"Period: {bill.period_start} to {bill.period_end}")
            + "\n"
            + "Destination: "
            + escape(bill.destination)
            + "\n"
            + "Bill reference: "
            + escape(bill.reference)
            + "\n"
            + "From: "
            + escape(quote.funding)
            + "\n"
            + escape(f"Fee: {bill.currency} {quote.fee}")
            + "\n"
            + (
                escape(spec["typical_amount_review"]["notice"]) + "\n"
                if spec.get("typical_amount_review", {}).get("notice") else ""
            )
            + "Approve this one payment, then verify and record its receipt.\n"
            + (
                escape("Transfer tracking reference: " + quote.transaction) + "\n"
                + "Then authorize this exact transfer in the HLB bank app when prompted.\n"
                if getattr(self.payments.provider, "requires_bank_app", False) else ""
            )
            + "Expires "
            + human_time(datetime.fromisoformat(proposal.expires_at), now=datetime.now(UTC))
        )
        with self.store._connect() as c:
            review = c.execute(
                "SELECT * FROM telegram_payment_reviews WHERE notification_id=?", (row.id,)
            ).fetchone()
        valid = (
            review
            and review["state"] == "open"
            and review["expires_at"] > time.time()
            and proposal.status == ProposalStatus.PROPOSED
            and proposal.fingerprint == row.fingerprint
        )
        if len(body.encode("utf-16-le")) // 2 > 3900:
            valid = False  # Never truncate material approval details.
        buttons = [
            [
                {"text": "Approve", "callback_data": row.callback_nonce + ".e"},
                {"text": "Reject", "callback_data": row.callback_nonce + ".r"},
            ]
        ]
        return TelegramHTML(body), buttons if valid else None

    def result(self, proposal_id):
        _, spec, bill, _ = self.payments.load(proposal_id)
        with self.store._connect() as c:
            row = c.execute(
                "SELECT * FROM payment_attempts WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        execution = self.store.get_execution(row["execution_id"]) if row else None
        if execution and execution.status == ExecutionStatus.VERIFIED_SUCCESS:
            status = "Payment verified."
            status += (
                " Notion updated."
                if row["reconciliation"] == "verified"
                else " Notion update is unconfirmed; do not pay again."
            )
        elif execution and execution.outcome == "challenge_pending":
            status = (
                "Bank app approval is required. Authorize this exact transfer in the bank app "
                "when prompted. Payment remains unconfirmed; reconcile before trying again."
            )
        elif execution and execution.outcome == "challenge_required":
            status = "Bank app or hardware approval is required. No payment was submitted."
        elif execution and execution.outcome == "not_submitted":
            status = "Payment stopped before submission. Review the cause before preparing again."
        elif execution and execution.outcome == "bank_failed":
            status = (
                "Bank history reports failure. No retry was made; review before another payment."
            )
        elif execution and execution.outcome == "bank_pending":
            status = (
                "Bank history reports pending; payment is unconfirmed. "
                "Do not pay again; reconcile the existing transfer."
            )
        else:
            status = "Payment outcome is unconfirmed. Reconcile before any new payment."
        return TelegramHTML(
            ("SIMULATION\n" if spec["sandbox"] else "") + escape(bill.payee) + "\n" + status
        )

    def handle(self, row, action, callback):
        message, user = callback.get("message", {}), callback.get("from", {})
        chat = message.get("chat", {})
        if (
            str(user.get("id", "")) != self.owner_user_id
            or user.get("is_bot")
            or chat.get("type") != "private"
            or str(chat.get("id", "")) != row.chat_id
            or row.owner_user_id != self.owner_user_id
            or row.status != "delivered"
            or not row.telegram_message_id
            or str(message.get("message_id", "")) != row.telegram_message_id
            or message.get("date") == 0
            or not callback.get("id")
            or any(message.get(k) for k in ("forward_origin", "forward_from", "forward_date"))
            or callback.get("data")
            != row.callback_nonce + (".e" if action == "approve_execute" else ".r")
            or action not in {"approve_execute", "reject"}
            or not row.allows(action)
        ):
            return "That payment button is not valid."
        try:
            proposal, _, _, _ = self.payments.load(row.proposal_id)
        except Exception:
            return "Payment review changed or is unavailable. No payment was authorized."
        if proposal.fingerprint != row.fingerprint:
            return "Payment details changed. Review a new transaction."
        with self.store._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            review = c.execute(
                "SELECT * FROM telegram_payment_reviews WHERE notification_id=?", (row.id,)
            ).fetchone()
            if not review or review["state"] != "open" or review["expires_at"] <= time.time():
                return "This payment review expired or was already used."
            c.execute(
                "UPDATE telegram_payment_reviews SET state='claimed',callback_id=? "
                "WHERE notification_id=?",
                (str(callback["id"]), row.id),
            )
        authority = self.payments.authority
        context = authority.issue(
            "telegram", external_session_ref=row.chat_id, external_request_ref=str(callback["id"])
        )
        decision = {
            "operation": "payment_decision",
            "proposal_id": proposal.id,
            "fingerprint": proposal.fingerprint,
            "reject": action == "reject",
        }
        execution = {
            "operation": "execute_payment",
            "proposal_id": proposal.id,
            "fingerprint": proposal.fingerprint,
        }
        binding = _Binding(
            context.principal.subject,
            context.correlation_id,
            min(review["expires_at"], time.time() + 300),
            canonical(decision),
            frozenset({canonical(execution)}),
            guard=lambda: self.dispatch_guard(),
        )
        try:
            with self.confirmation.bind(binding):
                self.payments.decide(proposal.id, reject=action == "reject", context=context)
                if action == "reject":
                    return "Payment rejected."
                worker = authority.issue(
                    PAYMENT_WORKER,
                    correlation_id=context.correlation_id,
                    external_request_ref=str(callback["id"]),
                )
                self.payments.execute(
                    proposal.id, context=worker, guard=self.confirmation.dispatch_allowed,
                    on_pending=lambda: self.notify_progress(proposal.id),
                )
        except Exception:
            # Provider error strings may contain credentials. Persist bounded events only.
            self.payments._event(proposal.id, "review_stopped", {})
            return "Payment stopped. Check its recorded outcome before trying again."
        finally:
            with suppress(Exception):
                self.payments.provider.close()
        return "Payment processed. Check the result message."
