"""Bounded browser payment contract. No bank-specific live profile ships enabled.

Each provider must be reviewed against its actual DOM and requirements before a
live route can use this adapter. The included routed HTML sandbox exercises it.
"""

from dataclasses import asdict, dataclass

from wally.finance.models import FinanceError, digest, origin
from wally.finance.payments import BillPayment, MandatoryChallenge, PaymentQuote, Settlement
from wally.models.browser import BrowserAction, BrowserActionType, BrowserStepStatus
from wally.runtime.secrets_safety import evaluate_secret_reference

BILL_FIELDS = ("destination", "payee", "amount", "currency", "reference")
QUOTE_FIELDS = (*BILL_FIELDS, "transaction", "funding", "expires_at", "duplicate_check", "fee")
LEDGER_FIELDS = (*BILL_FIELDS, "transaction", "funding", "receipt", "status")


@dataclass(frozen=True)
class PaymentBrowserProfile:
    id: str
    version: str
    quote_url: str
    ledger_url: str
    allowed_origins: tuple[str, ...]
    username_ref: str
    password_ref: str
    totp_ref: str = ""
    challenge_selector: str = "#mandatory-bank-challenge"
    sandbox: bool = True

    @property
    def fingerprint(self):
        if (
            not self.id
            or not self.version
            or not self.allowed_origins
            or any(origin(o) != o for o in self.allowed_origins)
            or any(origin(u) not in self.allowed_origins for u in (self.quote_url, self.ledger_url))
        ):
            raise FinanceError("Invalid payment browser profile.")
        for ref in (self.username_ref, self.password_ref, self.totp_ref):
            if ref and (not evaluate_secret_reference(ref).allowed or "?" in ref or "#" in ref):
                raise FinanceError("Payment authentication uses plain secret references only.")
        if not self.username_ref or not self.password_ref:
            raise FinanceError("Automatic login requires configured credential references.")
        return digest(asdict(self))


class BrowserPaymentProvider:
    def __init__(self, browser, profile):
        self.browser, self.profile = browser, profile
        self._session = None

    @property
    def sandbox(self):
        return self.profile.sandbox

    @property
    def fingerprint(self):
        return self.profile.fingerprint

    @property
    def transaction_namespace(self):
        return origin(self.profile.quote_url)

    def _open(self, url):
        return self.browser.open_session(url=url, allowed_origins=self.profile.allowed_origins)

    def _read(self, session, fields):
        return self.browser.read_fields(session.session_id, {key: "#" + key for key in fields})

    @staticmethod
    def _match(bill, fields):
        if any(fields[key] != getattr(bill, key) for key in BILL_FIELDS):
            raise FinanceError("Provider account, recipient, amount or bill reference changed.")

    def _quote(self, session, bill):
        fields = self._read(session, QUOTE_FIELDS)
        self._match(bill, fields)
        return PaymentQuote(
            fields["transaction"],
            digest(asdict(bill)),
            fields["funding"],
            float(fields["expires_at"]),
            fields["duplicate_check"],
            digest(fields),
            fields["fee"],
        )

    def prepare(self, bill: BillPayment):
        session = self._open(self.profile.quote_url)
        try:
            return self._quote(session, bill)
        finally:
            self.browser.close_session(session.session_id)

    def inspect(self, bill, quote):
        return self.prepare(bill)

    def _actions(self, *actions):
        result = self.browser.run_actions(self._session.session_id, tuple(actions))
        if result.status != BrowserStepStatus.COMPLETED:
            raise FinanceError("Payment browser step failed; no automatic retry.")

    def authenticate(self, resolver, *, guard, session_id):
        self._session = self._open(self.profile.quote_url)
        guard()
        for selector, reference in (
            ("#username", self.profile.username_ref),
            ("#password", self.profile.password_ref),
        ):
            value = resolver.resolve(
                reference, authorized=True, session_id=session_id, purpose="payment_login"
            )
            guard()  # Secrets acquisition may outlive approval or worker lease.
            self._actions(
                BrowserAction(BrowserActionType.FILL, {"selector": selector, "value": value})
            )
            del value
        self._actions(BrowserAction(BrowserActionType.CLICK, {"selector": "#login"}))
        if self.browser.has_selector(self._session.session_id, self.profile.challenge_selector):
            raise MandatoryChallenge("A mandatory bank challenge requires the owner.")
        if self.profile.totp_ref:
            value = resolver.resolve_totp(
                self.profile.totp_ref, authorized=True, session_id=session_id
            )
            guard()
            self._actions(
                BrowserAction(BrowserActionType.FILL, {"selector": "#totp", "value": value})
            )
            del value
            self._actions(BrowserAction(BrowserActionType.CLICK, {"selector": "#verify-totp"}))
        self._actions(BrowserAction(BrowserActionType.VERIFY_AUTH, {"selector": "#authenticated"}))

    def submit(self, bill, quote, *, guard):
        if self._session is None or self._quote(self._session, bill) != quote:
            raise FinanceError("Authenticated transaction differs from the Telegram review.")
        guard()
        self._actions(BrowserAction(BrowserActionType.CLICK, {"selector": "#pay-once"}))
        if self.browser.has_selector(self._session.session_id, self.profile.challenge_selector):
            raise MandatoryChallenge(
                "Bank authorization is pending; reconcile before another payment."
            )
        # Deliberately ignore confirmation page text. Submission is not settlement.

    def verify(self, bill, quote):
        # A NEW provider history read independent of the submission response.
        session = self._open(self.profile.ledger_url)
        try:
            fields = self._read(session, LEDGER_FIELDS)
            self._match(bill, fields)
            return Settlement(
                fields["transaction"],
                digest(asdict(bill)),
                fields["funding"],
                fields["receipt"],
                fields["status"],
                digest(fields),
            )
        finally:
            self.browser.close_session(session.session_id)

    def close(self):
        if self._session is not None:
            self.browser.close_session(self._session.session_id)
            self._session = None
