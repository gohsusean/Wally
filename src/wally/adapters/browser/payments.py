"""Bounded browser payment contract. No bank-specific live profile ships enabled.

Each provider must be reviewed against its actual DOM and requirements before a
live route can use this adapter. The included routed HTML sandbox exercises it.
"""

from dataclasses import asdict, dataclass

from wally.adapters.browser.authentication import LoginState, LoginStep
from wally.adapters.browser.hlb import HLB_ORIGIN, HLBSecurityPicture
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
    username_step: LoginStep | None = None
    password_step: LoginStep | None = None
    authenticated_selector: str = "#authenticated"
    hlb_picture: HLBSecurityPicture | None = None

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
        if not self.authenticated_selector:
            raise FinanceError("Authentication needs an observed success condition.")
        if HLB_ORIGIN in self.allowed_origins and not self.hlb_picture:
            raise FinanceError("HLB login requires an enrolled security-picture gate.")
        if self.hlb_picture and (self.allowed_origins != (HLB_ORIGIN,) or not self.password_step):
            raise FinanceError("HLB picture verification requires official-origin staged login.")
        if self.username_step is not None or self.password_step is not None:
            if not self.username_step or not self.password_step or not self.challenge_selector:
                raise FinanceError(
                    "Staged login requires both steps and a mandatory challenge check."
                )
            if any(
                self.challenge_selector not in step.challenge_selectors
                for step in (self.username_step, self.password_step)
            ):
                raise FinanceError("Every login step must retain the mandatory challenge check.")
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
        _ = self.fingerprint
        guard()
        values = resolver.resolve_many(
            (self.profile.username_ref, self.profile.password_ref),
            authorized=True,
            session_id=session_id,
            purpose="payment_login",
        )
        guard()
        self._session = self._open(self.profile.quote_url)
        for selector, value in zip(("#username", "#password"), values, strict=True):
            guard()  # Secrets acquisition may outlive approval or worker lease.
            step = (
                self.profile.username_step
                if selector == "#username"
                else self.profile.password_step
            )
            if step:
                if selector == "#password" and self.profile.hlb_picture:
                    result = self.browser.submit_hlb_password(
                        self._session.session_id,
                        self.profile.hlb_picture,
                        step,
                        value,
                        guard=guard,
                    )
                else:
                    result = self.browser.submit_authentication_step(
                        self._session.session_id,
                        step,
                        value,
                        allowed_origins=self.profile.allowed_origins,
                        before_submit=guard,
                    )
                if result.state == LoginState.CHALLENGE:
                    raise MandatoryChallenge("A mandatory bank challenge requires the owner.")
                if result.state != LoginState.ADVANCED:
                    raise FinanceError("Login transition was not verified; no automatic retry.")
            else:
                self._actions(
                    BrowserAction(BrowserActionType.FILL, {"selector": selector, "value": value})
                )
            del value
        del values
        if not self.profile.username_step:
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
        self._actions(
            BrowserAction(
                BrowserActionType.VERIFY_AUTH, {"selector": self.profile.authenticated_selector}
            )
        )

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
