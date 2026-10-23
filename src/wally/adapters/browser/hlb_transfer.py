"""Deterministic HLB existing-beneficiary transfer adapter.

Transfer/history selectors have no live defaults: supply only an attended-reviewed
profile. This module never creates beneficiaries, saves sessions or retries a debit.
An explicit preflight may log in read-only before the Telegram payment card; money
entry/submission runs only under the existing narrow worker's exact authorization.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

from wally.adapters.browser.authentication import LoginState, LoginStep
from wally.adapters.browser.hlb import HLB_ORIGIN, REFERENCE, HLBSecurityPicture, check_hlb_origin
from wally.finance.models import FinanceError, digest, origin
from wally.finance.payments import MandatoryChallenge, PaymentQuote, Settlement
from wally.runtime.secrets_safety import evaluate_secret_reference

# Separate ledger reads bind every material field, including bank and source account.
TRANSFER_FIELDS = (
    "bank",
    "destination",
    "payee",
    "amount",
    "currency",
    "reference",
    "funding",
    "tracking",
)


@dataclass(frozen=True)
class HLBTransferProfile:
    id: str
    version: str
    entry_url: str
    history_url: str
    beneficiary_url: str
    funding: str  # Reviewed label, with at most the safe last four digits.
    funding_option: str
    beneficiary_option: str
    funding_selector: str
    beneficiary_selector: str
    amount_selector: str
    reference_selector: str
    tracking_selector: str
    review_selector: str
    submit_selector: str
    review_fields: tuple[tuple[str, str], ...]
    beneficiary_fields: tuple[tuple[str, str], ...]
    history_fields: tuple[tuple[str, str], ...]
    history_rows: str
    history_complete_selector: str
    scheduled_clear_selector: str
    mandate_clear_selector: str
    challenge_selector: str
    result_selector: str
    username_ref: str
    password_ref: str
    frame_selector: str = ""  # Entry/review frame; read pages may have their own document.
    app_timeout_seconds: int = 120
    sandbox: bool = False
    submission_enabled: bool = False  # Attended inspection cannot submit, even with a valid card.

    @property
    def fingerprint(self):
        for url in (self.entry_url, self.history_url, self.beneficiary_url):
            if origin(url) != HLB_ORIGIN:
                raise FinanceError("HLB transfers require the exact official HTTPS origin.")
        for ref in (self.username_ref, self.password_ref):
            if not evaluate_secret_reference(ref).allowed or "?" in ref or "#" in ref:
                raise FinanceError("HLB credentials require plain governed secret references.")
        if (
            not self.funding
            or re.search(r"(?:\d[ -]?){5}", self.funding)
            or type(self.app_timeout_seconds) is not int
            or not 1 <= self.app_timeout_seconds <= 180
        ):
            raise FinanceError("HLB funding label or authorization timeout is unsafe.")
        for name, value in asdict(self).items():
            if isinstance(value, str) and name != "frame_selector" and not value:
                raise FinanceError("HLB requires a complete reviewed selector profile.")
        controls = (
            self.funding_selector,
            self.beneficiary_selector,
            self.amount_selector,
            self.reference_selector,
            self.tracking_selector,
            self.review_selector,
            self.submit_selector,
        )
        if len(set(controls)) != len(controls):
            raise FinanceError("HLB entry, review and submission controls must be distinct.")
        for fields, expected in (
            (self.review_fields, {*TRANSFER_FIELDS, "fee", "mode"}),
            (self.beneficiary_fields, {"bank", "destination", "payee"}),
            (self.history_fields, {*TRANSFER_FIELDS, "receipt", "status"}),
        ):
            if len(fields) != len(expected) or set(dict(fields)) != expected:
                raise FinanceError("HLB evidence selectors are incomplete or ambiguous.")
            if any(not selector for _, selector in fields):
                raise FinanceError("HLB evidence selectors must be explicit.")
        return digest(asdict(self))


class HLBTransferProvider:
    transaction_namespace = HLB_ORIGIN
    requires_bank_app = True

    def __init__(self, browser, profile, resolver, *, picture_reference=REFERENCE):
        self.browser, self.profile, self.resolver = browser, profile, resolver
        if profile.sandbox and not getattr(browser, "offline_simulation", False):
            raise FinanceError("HLB simulation requires a browser that intercepts all requests.")
        self.picture_reference = Path(picture_reference)
        self._session = None
        self._quote = None
        self._submitted = set()

    @property
    def sandbox(self):
        if self.profile.sandbox and not getattr(self.browser, "offline_simulation", False):
            raise FinanceError("HLB simulation requires a browser that intercepts all requests.")
        return self.profile.sandbox

    @property
    def fingerprint(self):
        return self.profile.fingerprint

    def _login(self):
        """Read-only bank inspection authority, never transfer authority."""
        _ = self.fingerprint
        if self._session is not None:
            check_hlb_origin(self.browser._pages[self._session.session_id])
            return
        values = self.resolver.resolve_many(
            (self.profile.username_ref, self.profile.password_ref),
            authorized=True,
            purpose="hlb_read_only_payment_preflight",
        )
        try:
            self._session = self.browser.open_session(
                url=HLB_ORIGIN + "/", allowed_origins=(HLB_ORIGIN,), allow_read_pages=True
            )
            sid = self._session.session_id
            self.browser.wait_for_hlb_login(sid)
            picture = HLBSecurityPicture(
                "xpath=//input[@id='idSBCBConfirmPic']/preceding::img[1]", "#idSBCBConfirmPic"
            )
            result = self.browser.submit_authentication_step(
                sid,
                LoginStep("#idLoginId", (picture.image_selector,), submit_selector="#idBtnSubmit1"),
                values[0],
                allowed_origins=(HLB_ORIGIN,),
                before_submit=lambda: None,
            )
            if result.state != LoginState.ADVANCED:
                raise FinanceError("HLB username transition was not verified.")
            result = self.browser.submit_hlb_password(
                sid,
                picture,
                LoginStep(
                    "#idPswd",
                    ('a[href*="logout" i]',),
                    submit_selector="#idBtnSubmit2",
                    next_frame_selector="iframe",
                ),
                values[1],
                guard=lambda: None,
                reference=self.picture_reference,
            )
            if result.state != LoginState.ADVANCED:
                raise FinanceError("HLB authenticated dashboard was not verified.")
        except Exception:
            self.close()
            raise FinanceError("HLB read-only login failed; no automatic retry.") from None
        finally:
            values = ()

    def _page(self):
        self._login()
        page = self.browser._pages[self._session.session_id]
        check_hlb_origin(page)
        return page

    @contextmanager
    def _read_page(self, url):
        # A fresh navigation shares only this ephemeral authenticated bank context.
        # It is independent of the submit response and does not destroy the review form.
        page = self._page().context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            check_hlb_origin(page)
            yield page
            check_hlb_origin(page)
        finally:
            page.close()

    @staticmethod
    def _fields(root, selectors):
        result = {}
        for key, selector in selectors:
            locator = root.locator(selector)
            if locator.count() != 1 or not locator.is_visible():
                raise FinanceError("HLB evidence is missing, hidden or ambiguous.")
            result[key] = locator.inner_text().strip()
        return result

    @staticmethod
    def _expected(bill, funding, tracking=""):
        try:
            bank, account = bill.destination.split(":")
        except ValueError:
            raise FinanceError("A certified bank/account destination is required.") from None
        if not re.fullmatch(r"[A-Z0-9]{2,20}", bank) or not re.fullmatch(r"\d{5,34}", account):
            raise FinanceError("A certified bank/account destination is required.")
        return {
            "bank": bank,
            "destination": account,
            "payee": bill.payee,
            "amount": bill.amount,
            "currency": bill.currency,
            "reference": bill.reference,
            "funding": funding,
            "tracking": tracking,
        }

    def _ledger(self, *, preflight=False):
        p = self.profile
        with self._read_page(p.history_url) as page:
            # All three inventories are required. A visible empty list is insufficient.
            selectors = (p.history_complete_selector,)
            if preflight:
                selectors += (p.scheduled_clear_selector, p.mandate_clear_selector)
            for selector in selectors:
                loc = page.locator(selector)
                if loc.count() != 1 or not loc.is_visible():
                    raise FinanceError("HLB history, scheduled or mandate inventory is incomplete.")
            rows = page.locator(p.history_rows)
            if rows.count() > 1000:
                raise FinanceError("HLB history is too large for the reviewed inventory.")
            return [self._fields(row, p.history_fields) for row in rows.all()]

    def _inventory(self, bill):
        expected = self._expected(bill, self.profile.funding)
        with self._read_page(self.profile.beneficiary_url) as page:
            recipient = self._fields(page, self.profile.beneficiary_fields)
        if recipient != {key: expected[key] for key in ("bank", "destination", "payee")}:
            raise FinanceError("Existing HLB beneficiary does not match the certified recipient.")
        rows = self._ledger(preflight=True)
        # Conservative: the same recipient + amount OR invoice is a duplicate risk,
        # including failed/unknown rows until reviewed. Do not infer a retry is safe.
        if any(
            (
                r["bank"] == expected["bank"]
                and r["destination"] == expected["destination"]
                and r["amount"] == expected["amount"]
                and r["currency"] == expected["currency"]
            )
            or r["reference"] == expected["reference"]
            for r in rows
        ):
            raise FinanceError("HLB history contains a potential duplicate; reconcile first.")
        return digest({"recipient": recipient, "history": rows, "funding": expected["funding"]})

    def prepare(self, bill):
        try:
            evidence = self._inventory(bill)
        except Exception:
            self.close()
            raise
        self._quote = PaymentQuote(
            "W" + uuid4().hex[:14].upper(),
            digest(asdict(bill)),
            self.profile.funding,
            time.time() + 900,
            "clear",
            evidence,
        )
        return self._quote

    def inspect(self, bill, quote):
        return replace(quote, evidence_hash=self._inventory(bill))

    def authenticate(self, resolver, *, guard, session_id):
        # Preflight normally has a session already; expiry fails closed at evidence reads.
        self._login()
        guard()  # Re-read the exact authorization and source after credential acquisition.

    def _form(self, page):
        if self.profile.frame_selector:
            frame = page.locator(self.profile.frame_selector)
            if frame.count() != 1:
                raise FinanceError("HLB transfer frame is ambiguous.")
            check_hlb_origin(frame.element_handle().content_frame())
            return page.frame_locator(self.profile.frame_selector)
        return page

    def enter_review(self, bill, quote, *, guard):
        """Fill only existing-beneficiary controls. Stops before the debit control."""
        p = self.profile
        if quote.transaction in self._submitted or self.inspect(bill, quote) != quote:
            raise FinanceError("HLB review changed or this adapter has already submitted.")
        guard()
        page = self._page()
        page.goto(p.entry_url, wait_until="domcontentloaded")
        check_hlb_origin(page)
        form = self._form(page)
        for selector, value in (
            (p.funding_selector, p.funding_option),
            (p.beneficiary_selector, p.beneficiary_option),
        ):
            form.locator(selector).select_option(value=value, timeout=5000)
        form.locator(p.amount_selector).fill(bill.amount, timeout=5000)
        form.locator(p.reference_selector).fill(bill.reference, timeout=5000)
        form.locator(p.tracking_selector).fill(quote.transaction, timeout=5000)
        guard()
        form.locator(p.review_selector).click(timeout=5000)
        self._check_review(bill, quote)

    def _check_review(self, bill, quote):
        page = self._page()
        form = self._form(page)
        challenge = form.locator(self.profile.challenge_selector)
        if challenge.count() and challenge.is_visible():
            raise MandatoryChallenge("HLB requires bank authorization before proceeding.")
        fields = self._fields(form, self.profile.review_fields)
        if fields != {
            **self._expected(bill, quote.funding, quote.transaction),
            "fee": "0.00",
            "mode": "immediate",
        }:
            raise FinanceError("HLB review differs from the exact approved payment.")
        check_hlb_origin(page)

    def submit(self, bill, quote, *, guard):
        self.enter_review(bill, quote, guard=guard)
        if not self.profile.submission_enabled:
            raise FinanceError("HLB attended inspection stops before transfer submission.")
        guard()
        self._check_review(bill, quote)  # Re-read AFTER the final authorization check.
        self._submitted.add(quote.transaction)  # Includes a lost response; never repeat the click.
        self._form(self._page()).locator(self.profile.submit_selector).click(timeout=5000)
        # Bank-app authorization is always expected; completion still needs ledger evidence.
        raise MandatoryChallenge("Complete this exact HLB transfer in the bank app.")

    def await_authorization(self, *, guard):
        """Read-only wait: never clicks a continuation/retry/authorization control."""
        deadline = time.monotonic() + self.profile.app_timeout_seconds
        while time.monotonic() < deadline:
            guard()
            page = self._page()
            form = self._form(page)
            result = form.locator(self.profile.result_selector)
            if result.count() == 1 and result.is_visible():
                return
            # Playwright continues dispatching bank events during this bounded wait.
            page.wait_for_timeout(500)
        raise MandatoryChallenge("HLB bank authorization remains pending; reconcile read-only.")

    def verify(self, bill, quote):
        expected = self._expected(bill, quote.funding, quote.transaction)
        matches = [r for r in self._ledger() if all(r[k] == value for k, value in expected.items())]
        if len(matches) != 1:
            raise FinanceError("HLB history does not independently identify exactly one transfer.")
        row = matches[0]
        if row["status"] not in {"settled", "pending", "failed"} or not row["receipt"]:
            raise FinanceError("HLB history outcome or bank reference is unconfirmed.")
        return Settlement(
            quote.transaction,
            quote.bill_digest,
            quote.funding,
            row["receipt"],
            row["status"],
            digest(row),
        )

    def close(self):
        if self._session is not None:
            self.browser.close_session(self._session.session_id)
            self._session = None
