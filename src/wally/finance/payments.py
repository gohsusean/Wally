"""Exact Telegram-authorized payments on the operational proposal path.

No model payloads, inferred settlement, serialized authority or automatic retries.
Provider/Notion adapters are trusted registrations, never selected by source prose.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from wally.finance.models import FinanceError, Kind, Scope, digest, money, text
from wally.models.ops import (
    ExecutionStatus,
    Matter,
    MatterDomain,
    MatterStatus,
    ProposalExecution,
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)
from wally.models.principal import Capability, RequestContext
from wally.ops.store import OperationsStore, _insert_proposal, _proposal_payload
from wally.runtime.principals import ChannelPolicy, PrincipalAuthority

PAYMENT_WORKER = "telegram_payment_worker"
PAYMENT_POLICY = ChannelPolicy(
    "trusted_exact_payment_worker",
    frozenset({Capability.READ_CONTEXT, Capability.EXECUTE_PAYMENT, Capability.VERIFY_PAYMENT}),
)


@dataclass(frozen=True)
class BillPayment:
    instance_id: str
    account_page_id: str
    occurrence: str
    payee: str
    destination: str
    amount: str
    currency: str
    reference: str
    period_start: str
    period_end: str
    evidence_hash: str
    catalog_binding: str

    @property
    def obligation(self):
        # Identical to ADR-048 manual handoffs: both paths share one unique claim.
        occurrence = " ".join(unicodedata.normalize("NFKC", self.occurrence).casefold().split())
        return hashlib.sha256(
            json.dumps([str(UUID(self.account_page_id)), occurrence]).encode()
        ).hexdigest()


class CertifiedPaymentSource:
    """Reuse existing evidence, complete refresh, certificates and enablement."""

    def __init__(self, catalog):
        self.catalog = catalog

    def read(self, instance_id: str) -> BillPayment:
        catalog = self.catalog
        catalog.refresh()
        record = catalog.store.get(instance_id)
        if record.candidate.kind != Kind.INSTANCE:
            raise FinanceError("An issued canonical bill instance is required.")
        instance = record.candidate.facts
        definition = catalog.store.get(instance.definition)
        account = catalog.store.get(definition.candidate.facts.account)
        provider = catalog.store.get(account.candidate.facts.provider)
        if (
            instance.stage != "issued"
            or not definition.candidate.facts.active
            or account.candidate.facts.responsibility != "owner"
            or not catalog.store.enabled(
                definition.id, Scope.IDENTITY, catalog.binding(definition.id)
            )
        ):
            raise FinanceError("A current owner-responsible enabled obligation is required.")
        portal_binding = catalog.binding(definition.id, Scope.PORTAL_REVIEW)
        if not catalog.store.accepted(definition.id, portal_binding) or not catalog.store.enabled(
            definition.id, Scope.PORTAL_REVIEW, portal_binding
        ):
            raise FinanceError("This exact portal requires governed acceptance and enablement.")
        return BillPayment(
            record.id,
            str(UUID(account.candidate.locator.page)),
            instance.occurrence,
            provider.candidate.facts.issuer,
            account.candidate.facts.identifier,
            instance.amount,
            instance.currency,
            instance.invoice_reference,
            instance.period_start,
            instance.period_end,
            digest([asdict(e) for e in instance.evidence]),
            catalog.binding(record.id),
        )


@dataclass(frozen=True)
class PaymentQuote:
    transaction: str
    bill_digest: str
    funding: str  # Provider token label / masked card only, never a PAN.
    expires_at: float
    duplicate_check: str  # Must be "clear": includes pending, mandates and history.
    evidence_hash: str
    fee: str = "0.00"


@dataclass(frozen=True)
class Settlement:
    transaction: str
    bill_digest: str
    funding: str
    reference: str
    status: str
    evidence_hash: str


class PaymentProvider(Protocol):
    sandbox: bool
    fingerprint: str
    transaction_namespace: str

    def prepare(self, bill: BillPayment) -> PaymentQuote: ...
    def inspect(self, bill: BillPayment, quote: PaymentQuote) -> PaymentQuote: ...
    def authenticate(self, resolver, *, guard, session_id: str): ...
    def submit(self, bill: BillPayment, quote: PaymentQuote, *, guard): ...
    def verify(self, bill: BillPayment, quote: PaymentQuote) -> Settlement: ...
    def close(self): ...


class PaymentHistory(Protocol):
    fingerprint: str

    def check(self, bill: BillPayment): ...
    def record(self, bill: BillPayment, settlement: Settlement, *, guard): ...
    def verify_record(self, bill: BillPayment, settlement: Settlement): ...


class MandatoryChallenge(FinanceError):
    """App/biometric/hardware authentication cannot be replaced with stored TOTP."""


class SupervisedPayments:
    def __init__(
        self,
        store: OperationsStore,
        authority: PrincipalAuthority,
        source,
        provider: PaymentProvider,
        history: PaymentHistory,
        resolver,
        *,
        enabled=lambda: False,
        live_validated=False,
    ):
        self.store, self.authority, self.source = store, authority, source
        self.provider, self.history, self.resolver = provider, history, resolver
        self.enabled, self.live_validated = enabled, live_validated
        with store._connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS payment_specs (
                    proposal_id TEXT PRIMARY KEY, spec TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS payment_attempts (
                    proposal_id TEXT PRIMARY KEY, execution_id TEXT UNIQUE NOT NULL,
                    prepared_key TEXT NOT NULL DEFAULT '',
                    settlement TEXT NOT NULL DEFAULT '', reconciliation TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS payment_events (
                    id INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL, event TEXT NOT NULL,
                    details TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS payment_baseline_exceptions (
                    instance_id TEXT NOT NULL, bill_digest TEXT NOT NULL,
                    catalog_binding TEXT NOT NULL,
                    provenance TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(instance_id,bill_digest));
                CREATE TABLE IF NOT EXISTS manual_payment_attempts (
                    id TEXT PRIMARY KEY, obligation TEXT NOT NULL, facts TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '');
                CREATE UNIQUE INDEX IF NOT EXISTS manual_payment_open_obligation
                    ON manual_payment_attempts(obligation) WHERE state != 'failure';
                CREATE UNIQUE INDEX IF NOT EXISTS manual_payment_receipt
                    ON manual_payment_attempts(receipt_hash) WHERE state = 'success';
            """)
            columns = {
                row[1] for row in c.execute("PRAGMA table_info(payment_baseline_exceptions)")
            }
            if "catalog_binding" not in columns:
                c.execute(
                    "ALTER TABLE payment_baseline_exceptions "
                    "ADD COLUMN catalog_binding TEXT NOT NULL DEFAULT ''"
                )
            attempt_columns = {row[1] for row in c.execute("PRAGMA table_info(payment_attempts)")}
            if "prepared_key" not in attempt_columns:
                c.execute(
                    "ALTER TABLE payment_attempts ADD COLUMN prepared_key TEXT NOT NULL DEFAULT ''"
                )
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS payment_prepared_once "
                "ON payment_attempts(prepared_key) WHERE prepared_key!=''"
            )

    def _gate(self):
        if not self.enabled() or (not self.provider.sandbox and not self.live_validated):
            raise FinanceError("Payment execution is disabled or this live route is unvalidated.")

    def identify_unpaid(self, instance_id: str, *, context: RequestContext):
        """Only an explicit authenticated owner /unpaid command can lift coverage.

        This records one specific unpaid obligation, never payment authorization.
        No Gateway or model-facing operation exposes it.
        """
        self.authority.authorize(context, Capability.DECIDE_PROPOSAL)
        if (
            context.principal.channel != "telegram"
            or context.principal.authentication != "telegram_private"
            or not context.external_request_ref
        ):
            raise FinanceError("A direct private-owner Telegram instruction is required.")
        bill = self.source.read(instance_id)
        with self.store._connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO payment_baseline_exceptions "
                "(instance_id,bill_digest,catalog_binding,provenance,created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    instance_id,
                    digest(asdict(bill)),
                    bill.catalog_binding,
                    json.dumps(context.provenance().as_dict()),
                    datetime.now(UTC).isoformat(),
                ),
            )
        self._event("", "owner_identified_specific_unpaid", {"instance_id": instance_id})

    def _validate_bill(self, bill):
        for value in asdict(bill).values():
            text(value)
        UUID(bill.account_page_id)
        if bill.currency != "MYR" or Decimal(money(bill.amount, bill.currency)) <= 0:
            raise FinanceError("A positive exact MYR payment is required.")
        # Administrative coverage is not settlement. Never prepare old debt from mail.
        try:
            start = datetime.fromisoformat(bill.period_start).date()
            end = datetime.fromisoformat(bill.period_end).date()
        except ValueError:
            raise FinanceError("A verified exact billing period is required.") from None
        with self.store._connect() as c:
            exception = c.execute(
                "SELECT 1 FROM payment_baseline_exceptions WHERE instance_id=? AND bill_digest=?",
                (bill.instance_id, digest(asdict(bill))),
            ).fetchone()
        if start > end or (start.year < 2027 and not exception):
            raise FinanceError("Through-2026 obligations are covered; no automatic payment.")

    @staticmethod
    def _validate_quote(bill, quote):
        if (
            quote.bill_digest != digest(asdict(bill))
            or not quote.transaction
            or not quote.funding
            or quote.expires_at <= time.time()
            or quote.duplicate_check != "clear"
            or quote.fee != "0.00"
            or not math.isfinite(quote.expires_at)
            or not re.fullmatch(r"[a-f0-9]{64}", quote.evidence_hash)
            or re.search(r"(?:\d[ -]?){5}", quote.funding)
        ):
            raise FinanceError("Prepared transaction, duplicate check or exact debit is unsafe.")
        text(quote.transaction)
        text(quote.funding)

    def prepare(self, instance_id: str, *, context: RequestContext):
        self.authority.authorize(context, Capability.SUBMIT_REQUEST)
        self._gate()
        bill = self.source.read(instance_id)
        self._validate_bill(bill)
        self.history.check(bill)
        self._duplicates(bill)
        quote = self.provider.prepare(bill)
        self._validate_quote(bill, quote)
        now = datetime.now(UTC).isoformat()
        spec = {
            "bill": asdict(bill),
            "quote": asdict(quote),
            "route": self.provider.fingerprint,
            "history": self.history.fingerprint,
            "sandbox": self.provider.sandbox,
            "scope": "one_payment_and_receipt_reconciliation",
        }
        matter_id = "payment_" + bill.obligation[:24]
        if self.store.get_matter(matter_id) is None:
            self.store.save_matter(
                Matter(
                    matter_id,
                    matter_id,
                    "Bill payment",
                    MatterDomain.FINANCE,
                    MatterStatus.OPEN,
                    now,
                    now,
                    "Prepared bill",
                    "Owner-requested supervised payment",
                    "Prepared",
                    knowledge_ids=(bill.instance_id,),
                    source="supervised_payment",
                )
            )
        proposal = ProposedAction(
            "pa_" + uuid4().hex[:16],
            digest(spec),
            matter_id,
            ProposalIntent.PAY_BILL,
            ProposalStatus.PROPOSED,
            ProposalProvenance.DETERMINISTIC_RULES,
            ProposalRisk.HIGH,
            now,
            now,
            "Review prepared payment",
            "Exact Telegram payment review required",
            "Review the prepared transaction in Telegram.",
            knowledge_ids=(bill.instance_id,),
            request_provenance=context.provenance(),
            expires_at=datetime.fromtimestamp(
                min(quote.expires_at, time.time() + 900), UTC
            ).isoformat(),
        )
        try:
            with self.store._connect() as c:
                _insert_proposal(c, _proposal_payload(proposal))
                c.execute("INSERT INTO payment_specs VALUES (?,?)", (proposal.id, json.dumps(spec)))
        except sqlite3.IntegrityError:
            raise FinanceError("An existing payment review blocks another preparation.") from None
        self._event(proposal.id, "prepared", {"fingerprint": proposal.fingerprint})
        return proposal

    def load(self, proposal_id):
        proposal = self.store.get_proposal(proposal_id)
        with self.store._connect() as c:
            row = c.execute(
                "SELECT spec FROM payment_specs WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if proposal is None or proposal.intent != ProposalIntent.PAY_BILL or row is None:
            raise FinanceError("Unknown payment review.")
        spec = json.loads(row[0])
        if digest(spec) != proposal.fingerprint:
            raise FinanceError("Payment review was modified.")
        return proposal, spec, BillPayment(**spec["bill"]), PaymentQuote(**spec["quote"])

    def _current(self, proposal_id, *, approved=False, dispatched=False):
        self._gate()
        proposal, spec, bill, quote = self.load(proposal_id)
        expected = ProposalStatus.APPROVED if approved else ProposalStatus.PROPOSED
        matter = self.store.get_matter(proposal.matter_id)
        if (
            proposal.status != expected
            or matter is None
            or matter.status != MatterStatus.OPEN
            or datetime.fromisoformat(proposal.expires_at).timestamp() <= time.time()
            or spec["route"] != self.provider.fingerprint
            or spec["history"] != self.history.fingerprint
            or spec["sandbox"] != self.provider.sandbox
            or (approved and proposal.decision_fingerprint != proposal.fingerprint)
            or self.source.read(bill.instance_id) != bill
        ):
            raise FinanceError("Payment facts, policy, decision or Matter changed.")
        self._validate_bill(bill)
        self._validate_quote(bill, quote)
        if not dispatched:
            self.history.check(bill)
            if self.provider.inspect(bill, quote) != quote:
                raise FinanceError("The prepared transaction or duplicate evidence changed.")
        # Provider/source reads can outlive the human review or change local policy.
        self._gate()
        if (
            self.store.get_proposal(proposal_id) != proposal
            or self.store.get_matter(proposal.matter_id) != matter
            or self.provider.fingerprint != spec["route"]
            or self.history.fingerprint != spec["history"]
            or datetime.fromisoformat(proposal.expires_at).timestamp() <= time.time()
        ):
            raise FinanceError("Payment authorization changed during current-evidence reads.")
        return proposal, spec, bill, quote

    def decide(self, proposal_id, *, reject=False, context: RequestContext):
        self.authority.authorize(context, Capability.DECIDE_PROPOSAL)
        proposal, spec, _, _ = self.load(proposal_id)
        presentation = {
            "operation": "payment_decision",
            "proposal_id": proposal_id,
            "fingerprint": proposal.fingerprint,
            "reject": reject,
        }
        confirmation = self.authority.confirm_human(
            context,
            Capability.DECIDE_PROPOSAL,
            presentation=presentation,
        )
        if not reject:
            self._current(proposal_id)
        if self.load(proposal_id)[:2] != (proposal, spec):
            raise FinanceError("Payment decision changed during confirmation.")
        self.authority.consume_confirmation(
            confirmation,
            context,
            Capability.DECIDE_PROPOSAL,
            presentation=presentation,
        )
        if not self.store.record_decision(
            proposal_id,
            status=ProposalStatus.REJECTED if reject else ProposalStatus.APPROVED,
            updated_at=datetime.now(UTC).isoformat(),
            decided_by=context.provenance(),
            status_reason="Exact Telegram payment decision",
        ):
            raise FinanceError("Payment decision was already used.")
        self._event(proposal_id, "decision", {"confirmation": confirmation.id, **presentation})

    def execute(self, proposal_id, *, context: RequestContext, guard=lambda: False):
        self.authority.authorize(context, Capability.EXECUTE_PAYMENT)
        self.authority.authorize(context, Capability.VERIFY_PAYMENT)
        proposal, spec, bill, quote = self._current(proposal_id, approved=True)
        presentation = {
            "operation": "execute_payment",
            "proposal_id": proposal_id,
            "fingerprint": proposal.fingerprint,
        }
        confirmation = self.authority.confirm_human(
            context,
            Capability.EXECUTE_PAYMENT,
            presentation=presentation,
        )
        if self._current(proposal_id, approved=True)[:2] != (proposal, spec):
            raise FinanceError("Payment changed during execution authorization.")
        if not guard():
            raise FinanceError("Payment worker lost its dispatch lease.")
        self.authority.consume_confirmation(
            confirmation,
            context,
            Capability.EXECUTE_PAYMENT,
            presentation=presentation,
        )
        now = datetime.now(UTC).isoformat()
        execution = ProposalExecution(
            "ex_" + uuid4().hex[:16],
            proposal.id,
            proposal.fingerprint,
            proposal.matter_id,
            ProposalIntent.PAY_BILL,
            ExecutionStatus.RUNNING,
            context.principal.channel,
            now,
            now,
            executor="supervised_payment",
            plan_digest=digest(spec),
            authorization=confirmation.id,
            principal=context.principal.subject,
            correlation_id=context.correlation_id,
            request_provenance=context.provenance(),
            started_at=now,
        )
        # Claim canonical occurrence AND proposal atomically before credentials or effects.
        # A crash before an execution record is written still leaves an uncertain claim.
        try:
            with self.store._connect() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO payment_attempts(proposal_id,execution_id,prepared_key) "
                    "VALUES (?,?,?)",
                    (
                        proposal_id,
                        execution.id,
                        digest([self.provider.transaction_namespace, quote.transaction]),
                    ),
                )
                c.execute(
                    "INSERT INTO manual_payment_attempts "
                    "(id,obligation,facts,state,created_at) VALUES (?,?,?,?,?)",
                    (
                        execution.id,
                        bill.obligation,
                        json.dumps(
                            {
                                "account_page_id": bill.account_page_id,
                                "occurrence": bill.occurrence,
                                "payee": bill.payee,
                                "amount": bill.amount,
                                "currency": bill.currency,
                                "statement_hash": bill.evidence_hash,
                                "recipient_hash": digest(bill.destination),
                            }
                        ),
                        "reserved",
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            raise FinanceError(
                "An existing payment attempt blocks execution; reconcile first."
            ) from None
        if not self.store.insert_execution(execution):
            raise FinanceError("Execution record conflict; retained payment claim blocks retry.")
        self._event(proposal_id, "execution_authorized", {"confirmation": confirmation.id})

        def revalidate():
            self.authority.authorize(context, Capability.EXECUTE_PAYMENT)
            self.authority.authorize(context, Capability.VERIFY_PAYMENT)
            if time.time() >= confirmation.expires_at or not guard():
                raise FinanceError("Execution authorization expired or lease lost.")
            if self._current(proposal_id, approved=True)[:2] != (proposal, spec):
                raise FinanceError("Payment changed before submission.")

        submitted = False
        try:
            revalidate()
            self.provider.authenticate(self.resolver, guard=revalidate, session_id=execution.id)
            revalidate()  # Source, duplicate, target and approval reread after secrets/login.
            self._event(proposal_id, "submission_entered", {"transaction": quote.transaction})
            submitted = True  # Includes a transport exception from submit: outcome unknown.
            self.provider.submit(bill, quote, guard=revalidate)
        except MandatoryChallenge:
            return self._outcome(
                execution, "challenge_pending" if submitted else "challenge_required"
            )
        except Exception:
            # Never stringify provider exceptions: they may contain auth/page material.
            return self._outcome(execution, "uncertain" if submitted else "not_submitted")
        finally:
            with suppress(Exception):
                self.provider.close()
        return self.reconcile(proposal_id, context=context)

    def reconcile(self, proposal_id, *, context: RequestContext):
        """Read-only payment recovery; never calls submit or releases a duplicate claim."""
        self.authority.authorize(context, Capability.VERIFY_PAYMENT)
        proposal, spec, bill, quote = self.load(proposal_id)
        if (
            spec["route"] != self.provider.fingerprint
            or spec["history"] != self.history.fingerprint
        ):
            raise FinanceError("Recovery route changed; retain the claim for operator review.")
        with self.store._connect() as c:
            row = c.execute(
                "SELECT * FROM payment_attempts WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise FinanceError("No payment attempt to reconcile.")
        execution = self.store.get_execution(row["execution_id"])
        if execution is None:
            raise FinanceError("Interrupted execution record; retain claim and check provider.")
        try:
            settlement = self.provider.verify(bill, quote)
            if (
                settlement.transaction != quote.transaction
                or settlement.bill_digest != quote.bill_digest
                or settlement.funding != quote.funding
                or settlement.status != "settled"
                or not settlement.reference
                or len(settlement.evidence_hash) != 64
            ):
                raise FinanceError("Provider has not independently confirmed this exact payment.")
        except Exception:
            return self._outcome(execution, "uncertain")
        if row["settlement"] and json.loads(row["settlement"]) != asdict(settlement):
            self._event(proposal_id, "contradictory_settlement", {})
            raise FinanceError("Settlement evidence changed; retain the claim for investigation.")
        with self.store._connect() as c:
            c.execute(
                "UPDATE payment_attempts SET settlement=? WHERE proposal_id=?",
                (json.dumps(asdict(settlement)), proposal_id),
            )
            c.execute(
                "UPDATE manual_payment_attempts SET state='success',receipt_hash=?,"
                "reference=? WHERE id=?",
                (settlement.evidence_hash, settlement.reference, execution.id),
            )
        execution = replace(
            execution,
            evidence={
                "receipt_reference": settlement.reference,
                "evidence_hash": settlement.evidence_hash,
                "transaction": settlement.transaction,
            },
            verification_provenance=context.provenance(),
            verified_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
        )
        execution = self._outcome(execution, "settled")
        # Reserve Notion dispatch once. Ambiguous POST is recovered by a separate read,
        # never replayed. Payment settlement is preserved even if Notion is unavailable.
        with self.store._connect() as c:
            claimed = c.execute(
                "UPDATE payment_attempts SET reconciliation='started' "
                "WHERE proposal_id=? AND reconciliation=''",
                (proposal_id,),
            ).rowcount
        if claimed or row["reconciliation"] == "started":
            try:
                if claimed:

                    def record_guard():
                        self.authority.authorize(context, Capability.VERIFY_PAYMENT)
                        if self.history.fingerprint != spec["history"]:
                            raise FinanceError("Reviewed receipt target changed before recording.")

                    self.history.record(
                        bill,
                        settlement,
                        guard=record_guard,
                    )
                else:
                    self.history.verify_record(bill, settlement)  # Recovery NEVER repeats POST.
            except Exception:
                self._event(proposal_id, "notion_unconfirmed", {})
                return execution
            with self.store._connect() as c:
                c.execute(
                    "UPDATE payment_attempts SET reconciliation='verified' WHERE proposal_id=?",
                    (proposal_id,),
                )
            self._event(proposal_id, "notion_verified", {})
        return execution

    def _duplicates(self, bill):
        with self.store._connect() as c:
            row = c.execute(
                "SELECT 1 FROM manual_payment_attempts WHERE obligation=? AND state!='failure'",
                (bill.obligation,),
            ).fetchone()
        if row:
            raise FinanceError("A manual or automated payment claim blocks this obligation.")

    def _outcome(self, execution, outcome):
        status = (
            ExecutionStatus.VERIFIED_SUCCESS
            if outcome == "settled"
            else ExecutionStatus.EXECUTED_UNVERIFIED
            if outcome in {"uncertain", "challenge_pending"}
            else ExecutionStatus.FAILED
        )
        updated = replace(
            execution,
            status=status,
            updated_at=datetime.now(UTC).isoformat(),
            outcome=outcome,
            failure_category="" if outcome == "settled" else outcome,
            verification="independent_provider_ledger" if outcome == "settled" else "",
        )
        if execution.status == ExecutionStatus.VERIFIED_SUCCESS and outcome != "settled":
            return execution  # Failed retrieval never destroys confirmed settlement.
        self.store.transition_execution(updated, expected=execution.status)
        self._event(execution.proposal_id, outcome, {"execution_id": execution.id})
        return self.store.get_execution(execution.id)

    def _event(self, proposal_id, event, details):
        with self.store._connect() as c:
            c.execute(
                "INSERT INTO payment_events(proposal_id,event,details,created_at) VALUES (?,?,?,?)",
                (proposal_id, event, json.dumps(details), datetime.now(UTC).isoformat()),
            )
