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
from contextvars import ContextVar, copy_context
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from wally.finance.bill_identity import (
    BillingAccountError,
    require_billing_account_match,
    reviewed_account_match,
)
from wally.finance.models import FinanceError, Kind, Scope, digest, money, text
from wally.finance.receipts import ConfirmationTarget
from wally.finance.typical_amount import TypicalAmount, comparable_period
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


@dataclass
class _SubmissionAuthorization:
    """Ephemeral worker scope, never reconstructed from saved approval/provenance."""

    bill_digest: str
    quote_digest: str
    check_current: object
    active: bool = True

    def check(self, bill, quote):
        if (
            not self.active
            or digest(asdict(bill)) != self.bill_digest
            or digest(asdict(quote)) != self.quote_digest
        ):
            raise FinanceError("A fresh exact Telegram payment authorization is required.")
        self.check_current()


_SUBMISSION: ContextVar[_SubmissionAuthorization | None] = ContextVar(
    "payment_submission", default=None
)


def require_payment_submission(bill, quote):
    """Only the worker's authenticated, claimed execution can enter an HLB form."""
    authorization = _SUBMISSION.get()
    if authorization is None:
        raise FinanceError("A fresh exact Telegram payment authorization is required.")
    authorization.check(bill, quote)
    return authorization


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
    account_reference: str = ""
    invoice_account_reference: str = ""
    account_match_binding: str = ""

    @property
    def obligation(self):
        # Identical to ADR-048 manual handoffs: both paths share one unique claim.
        occurrence = " ".join(unicodedata.normalize("NFKC", self.occurrence).casefold().split())
        return hashlib.sha256(
            json.dumps([str(UUID(self.account_page_id)), occurrence]).encode()
        ).hexdigest()


class CertifiedPaymentSource:
    """Reuse existing evidence, complete refresh, certificates and enablement."""

    def __init__(self, catalog, *, invoice_accounts=None):
        self.catalog = catalog
        self.invoice_accounts = invoice_accounts

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
        self._check_route(definition)
        catalog_binding = catalog.binding(record.id)
        if self.invoice_accounts is None:
            raise FinanceError("Invoice account extraction is unavailable; payment is blocked.")
        identity = reviewed_account_match(
            account.candidate.facts.identifier, instance.evidence,
            self.invoice_accounts.read(instance.evidence), account.candidate.fingerprint,
        )
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
            catalog_binding,
            **identity,
        )

    def _check_route(self, definition):
        catalog = self.catalog
        portal_binding = catalog.binding(definition.id, Scope.PORTAL_REVIEW)
        if not catalog.store.accepted(definition.id, portal_binding) or not catalog.store.enabled(
            definition.id, Scope.PORTAL_REVIEW, portal_binding
        ):
            raise FinanceError("This exact portal requires governed acceptance and enablement.")

    def read_typical_amount(self, instance_id: str) -> TypicalAmount:
        # Follow this instance's canonical relation, never a title/account-name search.
        catalog = self.catalog
        instance = catalog.store.get(instance_id)
        if instance.candidate.kind != Kind.INSTANCE:
            raise FinanceError("An issued canonical bill instance is required.")
        definition = catalog.store.get(instance.candidate.facts.definition)
        if definition.candidate.kind != Kind.DEFINITION:
            raise FinanceError("The payment review requires its exact recurring definition.")
        locator = definition.candidate.locator
        sources = [
            source for source in catalog.sources()
            if source.kind == Kind.DEFINITION
            and str(UUID(source.database_id)) == locator.database
            and str(UUID(source.data_source_id)) == locator.data_source
        ]
        if locator.system != "notion":
            return TypicalAmount("invalid")
        if len(sources) != 1 or catalog.reader is None:
            raise FinanceError("Payment review definition source is unavailable or ambiguous.")
        catalog._source_gate(sources[0])
        baseline = catalog.reader.typical_amount(
            sources[0], locator.page, definition.candidate.facts.currency
        )
        return replace(baseline, comparable=comparable_period(
            definition.candidate.facts.frequency,
            instance.candidate.facts.period_start, instance.candidate.facts.period_end,
        ))

    def read_confirmation(self, instance_id: str) -> ConfirmationTarget:
        catalog = self.catalog
        # Settlement/app waiting can outlive the pre-submit catalog snapshot.
        # Re-follow current relations rather than emailing a stale provider.
        catalog.refresh()
        instance = catalog.store.get(instance_id)
        if instance.candidate.kind != Kind.INSTANCE:
            raise FinanceError("Confirmation requires the exact issued instance.")
        definition = catalog.store.get(instance.candidate.facts.definition)
        account = catalog.store.get(definition.candidate.facts.account)
        provider = catalog.store.get(account.candidate.facts.provider)
        locator = provider.candidate.locator
        sources = [
            source for source in catalog.sources()
            if source.kind == Kind.PROVIDER and source.key == (
                f"notion:{locator.database}:{locator.data_source}"
            )
        ]
        if locator.system != "notion" or len(sources) != 1 or catalog.reader is None:
            raise FinanceError("Current linked Bill Providers confirmation source is unavailable.")
        catalog._source_gate(sources[0])
        values, binding = catalog.reader.payment_confirmation(sources[0], locator.page)
        identifier_label = "account"
        if account.candidate.facts.subject_kind == "property":
            subject = catalog.store.get(account.candidate.facts.subject)
            if (
                subject.candidate.kind == Kind.PROPERTY
                and subject.candidate.facts.unit == account.candidate.facts.identifier
            ):
                identifier_label = "unit"
        return ConfirmationTarget(
            values["confirmation_required"], values["confirmation_email"],
            account.candidate.facts.identifier, definition.candidate.locator.page,
            digest([binding, account.id, definition.id]),
            identifier_label,
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


class PaymentNotSubmitted(FinanceError):
    """Trusted adapter proved its initiating request was never dispatched."""


class SupervisedPayments:
    payment_kind = "bill"
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
        confirmations=None,
        preparation_enabled=None,
    ):
        self.store, self.authority, self.source = store, authority, source
        self.provider, self.history, self.resolver = provider, history, resolver
        self.enabled, self.live_validated = enabled, live_validated
        self.confirmations = confirmations
        self.preparation_enabled = preparation_enabled
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
            for name in ("confirmation", "confirmation_key", "confirmation_reconciliation"):
                if name not in attempt_columns:
                    c.execute(
                        f"ALTER TABLE payment_attempts ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                    )
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS payment_receipt_email_once "
                "ON payment_attempts(confirmation_key) WHERE confirmation_key!=''"
            )
            if "prepared_key" not in attempt_columns:
                c.execute(
                    "ALTER TABLE payment_attempts ADD COLUMN prepared_key TEXT NOT NULL DEFAULT ''"
                )
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS payment_prepared_once "
                "ON payment_attempts(prepared_key) WHERE prepared_key!=''"
            )

    def _gate(self, *, preparation=False):
        if preparation and self.preparation_enabled is not None:
            if not self.preparation_enabled():
                raise FinanceError("Read-only payment preparation is disabled.")
            # Read-only preparation may produce a card while execution stays off.
            # It still requires an explicit reviewed route, never guessed selectors.
            _ = self.provider.fingerprint
            required = getattr(self.source, "required_transaction_namespace", "")
            if required and self.provider.transaction_namespace != required:
                raise FinanceError("The certified transfer source requires its exact bank route.")
            return
        if not self.enabled() or (not self.provider.sandbox and not self.live_validated):
            raise FinanceError("Payment execution is disabled or this live route is unvalidated.")
        if not self.provider.sandbox and (
            self.confirmations is None or not callable(getattr(self.provider, "receipt", None))
        ):
            raise FinanceError("Live payments require validated receipt and confirmation handling.")
        required = getattr(self.source, "required_transaction_namespace", "")
        if required and self.provider.transaction_namespace != required:
            raise FinanceError("The certified transfer source requires its exact bank route.")

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
        require_billing_account_match(bill.account_reference, bill.invoice_account_reference)
        if not re.fullmatch(r"[a-f0-9]{64}", bill.account_match_binding):
            raise FinanceError("The billing-account evidence match is missing or stale.")
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
        self._gate(preparation=True)
        bill = self.source.read(instance_id)
        self._validate_bill(bill)
        self.history.check(bill)
        self._duplicates(bill)
        quote = self.provider.prepare(bill)
        self._validate_quote(bill, quote)
        now = datetime.now(UTC).isoformat()
        spec = {
            "kind": self.payment_kind,
            "bill": asdict(bill),
            "quote": asdict(quote),
            "route": self.provider.fingerprint,
            "history": self.history.fingerprint,
            "sandbox": self.provider.sandbox,
            "scope": "one_payment_and_receipt_reconciliation",
            "typical_amount_review": self._typical_amount_review(bill),
            "confirmation": self.confirmations.review(bill) if self.confirmations else None,
        }
        if spec["confirmation"] is not None:
            spec["scope"] = "one_payment_receipt_and_associated_confirmation"
        matter_id = "payment_" + bill.obligation[:24]
        if self.store.get_matter(matter_id) is None:
            self.store.save_matter(
                Matter(
                    matter_id,
                    matter_id,
                    "Owner-account test transfer" if self.payment_kind == "owner_account_test"
                    else "Bill payment",
                    MatterDomain.FINANCE,
                    MatterStatus.OPEN,
                    now,
                    now,
                    "Prepared owner-account test" if self.payment_kind == "owner_account_test"
                    else "Prepared bill",
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

    def _typical_amount_review(self, bill):
        baseline = self.source.read_typical_amount(bill.instance_id)
        return {"baseline": asdict(baseline), "notice": baseline.notice(bill.amount, bill.currency)}

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
        if spec.get("kind", "bill") != self.payment_kind:
            raise FinanceError("This payment belongs to a different trusted route.")
        return proposal, spec, BillPayment(**spec["bill"]), PaymentQuote(**spec["quote"])

    def _current(self, proposal_id, *, approved=False, dispatched=False):
        self._gate()
        proposal, spec, bill, quote = self.load(proposal_id)
        expected = ProposalStatus.APPROVED if approved else ProposalStatus.PROPOSED
        matter = self.store.get_matter(proposal.matter_id)
        current_bill = self.source.read(bill.instance_id)
        if any(
            getattr(current_bill, field) != getattr(bill, field)
            for field in ("account_reference", "invoice_account_reference", "account_match_binding")
        ):
            raise BillingAccountError(
                "Billing account details changed; a fresh payment review is required."
            )
        if (
            proposal.status != expected
            or matter is None
            or matter.status != MatterStatus.OPEN
            or datetime.fromisoformat(proposal.expires_at).timestamp() <= time.time()
            or spec["route"] != self.provider.fingerprint
            or spec["history"] != self.history.fingerprint
            or spec["sandbox"] != self.provider.sandbox
            or (approved and proposal.decision_fingerprint != proposal.fingerprint)
            or current_bill != bill
            or self._typical_amount_review(bill) != spec.get("typical_amount_review")
            or (self.confirmations.review(bill) if self.confirmations else None)
            != spec.get("confirmation")
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

    def execute(
        self, proposal_id, *, context: RequestContext, guard=lambda: False, on_pending=lambda: None
    ):
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

        def revalidate(*, dispatched=False):
            self.authority.authorize(context, Capability.EXECUTE_PAYMENT)
            self.authority.authorize(context, Capability.VERIFY_PAYMENT)
            if time.time() >= confirmation.expires_at or not guard():
                raise FinanceError("Execution authorization expired or lease lost.")
            current = self._current(proposal_id, approved=True, dispatched=dispatched)
            if (
                current[:2] != (proposal, spec)
                or time.time() >= confirmation.expires_at or not guard()
            ):
                raise FinanceError("Payment changed before submission.")

        submitted = False
        try:
            revalidate()
            self.provider.authenticate(self.resolver, guard=revalidate, session_id=execution.id)
            revalidate()  # Source, duplicate, target and approval reread after secrets/login.
            self._event(proposal_id, "submission_entered", {"transaction": quote.transaction})
            submitted = True  # Includes a transport exception from submit: outcome unknown.
            # Bind only AFTER exact human confirmation, current-evidence checks and
            # durable occurrence/transaction claims. HLB also checks this scope before
            # entry navigation and from its network policy (without nested browser reads).
            callback_context = copy_context()  # Browser callbacks run on a separate greenlet.
            authorization = _SubmissionAuthorization(
                digest(asdict(bill)), digest(asdict(quote)),
                lambda: callback_context.run(revalidate, dispatched=True),
            )
            token = _SUBMISSION.set(authorization)
            try:
                self.provider.submit(bill, quote, guard=revalidate)
            finally:
                authorization.active = False
                _SUBMISSION.reset(token)
            return self.reconcile(proposal_id, context=context)
        except MandatoryChallenge:
            pending = self._outcome(
                execution, "challenge_pending" if submitted else "challenge_required"
            )
            wait = getattr(self.provider, "await_authorization", None)
            if not submitted or wait is None:
                return pending
            # Persist pending BEFORE notifying/waiting. No new money-moving action
            # is permitted here, even if the original execution approval expires.
            with suppress(Exception):
                on_pending()

            def read_guard():
                self.authority.authorize(context, Capability.VERIFY_PAYMENT)
                if not guard():
                    raise FinanceError("Payment worker lost its read-only recovery lease.")

            try:
                wait(guard=read_guard)
                read_guard()
                return self.reconcile(proposal_id, context=context)
            except Exception:
                return pending
        except PaymentNotSubmitted:
            return self._outcome(execution, "not_submitted")
        except BillingAccountError:
            return self._outcome(execution, "uncertain" if submitted else "billing_account_changed")
        except Exception:
            # Never stringify provider exceptions: they may contain auth/page material.
            return self._outcome(execution, "uncertain" if submitted else "not_submitted")
        finally:
            with suppress(Exception):
                self.provider.close()

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
                or settlement.status not in {"settled", "pending", "failed"}
                or not settlement.reference
                or not re.fullmatch(r"[a-f0-9]{64}", settlement.evidence_hash)
            ):
                raise FinanceError("Provider has not independently confirmed this exact payment.")
        except Exception:
            return self._outcome(execution, "uncertain")
        if row["settlement"] and json.loads(row["settlement"]) != asdict(settlement):
            self._event(proposal_id, "contradictory_settlement", {})
            raise FinanceError("Settlement evidence changed; retain the claim for investigation.")
        if settlement.status != "settled":
            self._event(proposal_id, "provider_outcome", asdict(settlement))
            return self._outcome(
                execution, "bank_pending" if settlement.status == "pending" else "bank_failed"
            )
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
        # The original exact payment review authorizes its associated receipt email.
        # Email outcome is separate from bank settlement and can never resubmit money.
        if spec.get("confirmation") is not None:
            try:
                if self.confirmations is None:
                    raise FinanceError("Receipt confirmation adapter is unavailable.")

                def email_guard():
                    self.authority.authorize(context, Capability.VERIFY_PAYMENT)
                    current = self.store.get_proposal(proposal_id)
                    if (
                        current.status != ProposalStatus.APPROVED
                        or current.fingerprint != proposal.fingerprint
                        or current.decision_fingerprint != proposal.fingerprint
                        or self.load(proposal_id)[1] != spec
                        or self.history.fingerprint != spec["history"]
                        or self.provider.fingerprint != spec["route"]
                    ):
                        raise FinanceError("Exact payment/email authorization changed.")

                self.confirmations.finish(
                    self, proposal, spec, bill, quote, settlement, guard=email_guard
                )
            except Exception:
                self._event(proposal_id, "confirmation_follow_up", {})
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
        if spec.get("confirmation") is not None:
            try:
                with self.store._connect() as c:
                    current = c.execute(
                        "SELECT confirmation,reconciliation FROM payment_attempts "
                        "WHERE proposal_id=?",
                        (proposal_id,),
                    ).fetchone()
                if current["reconciliation"] != "verified":
                    raise FinanceError("Payment receipt must be independently recorded first.")
                details = json.loads(current["confirmation"]) if current["confirmation"] else {
                    "state": "outstanding"
                }
                self.history.record_confirmation(
                    bill, settlement, details, guard=email_guard
                )
                with self.store._connect() as c:
                    c.execute(
                        "UPDATE payment_attempts SET confirmation_reconciliation='verified' "
                        "WHERE proposal_id=?", (proposal_id,),
                    )
            except Exception:
                with self.store._connect() as c:
                    c.execute(
                        "UPDATE payment_attempts SET confirmation_reconciliation='unconfirmed' "
                        "WHERE proposal_id=?", (proposal_id,),
                    )
                self._event(proposal_id, "confirmation_record_follow_up", {})
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
            if outcome in {"uncertain", "challenge_pending", "bank_pending"}
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
