"""Live HLB RM1 owner-account acceptance, exclusively under the exact worker scope.

Preparation is local. Login, favourite selection and every transfer request occur
after Telegram authorization. Unrecognized commands are aborted, never retried.
Only Next initiates this transfer; no subsequent Submit control is ever clicked.
"""

import hashlib
import marshal
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from wally.adapters.browser.hlb import HLB_ORIGIN, check_hlb_origin
from wally.adapters.browser.hlb_transfer import HLBTransferProvider
from wally.finance.models import FinanceError, digest
from wally.finance.payments import (
    MandatoryChallenge,
    PaymentNotSubmitted,
    PaymentQuote,
    Settlement,
    require_payment_submission,
)
from wally.finance.receipts import BankReceipt

ENTRY = HLB_ORIGIN + "/rib/app/fo/trx/3rdpartytrsf"
HISTORY = HLB_ORIGIN + "/rib/app/fo/trx/hist"
JSF_METADATA = {
    "javax.faces.partial.ajax",
    "javax.faces.partial.execute",
    "javax.faces.partial.render",
    "javax.faces.behavior.event",
    "javax.faces.partial.event",
    "javax.faces.source",
}


class HLBTestTransferProvider(HLBTransferProvider):
    allow_receipt_downloads = True

    def __init__(self, browser, resolver, source, *, event=lambda name, details: None):
        self.source, self.event = source, event
        profile = SimpleNamespace(
            sandbox=False,
            submission_enabled=True,
            username_ref="op://Private/HLB/username",
            password_ref="op://Private/HLB/password",
            history_url=HISTORY,
            entry_url=ENTRY,
        )
        super().__init__(browser, profile, resolver)
        self._pending = None
        self._review_dispatched = False
        self._review_claimed = False
        self._recipient_name = ""
        self._verified_detail = None
        self._receipt_content = None
        self.execution_id = ""
        self._test_request_blocked = False

    @property
    def fingerprint(self):
        return digest(
            [
                "hlb_owner_account_test_v1",
                self.source.policy,
                ENTRY,
                HISTORY,
                *[
                    hashlib.sha256(marshal.dumps(getattr(type(self), method).__code__)).hexdigest()
                    for method in (
                        "submit",
                        "_request_allowed",
                        "_command",
                        "verify",
                        "receipt",
                        "_check_recipient",
                        "_select",
                        "_rows",
                        "_detail",
                        "_validate",
                        "inspect",
                        "prepare",
                        "authenticate",
                        "await_authorization",
                    )
                ],
            ]
        )

    def _validate(self, bill):
        if bill != self.source.read(bill.instance_id):
            raise FinanceError("Only the exact owner-approved MYR 1.00 UOB test is permitted.")

    def prepare(self, bill):
        self._validate(bill)
        return PaymentQuote(
            "WT" + bill.instance_id[-14:].upper(),
            digest(asdict(bill)),
            "HLB account ending " + self.source.policy["funding_last4"],
            time.time() + 900,
            "clear",
            digest(["local_test_attempt", asdict(bill), self.source.policy]),
        )

    def inspect(self, bill, quote):
        self._validate(bill)
        if quote.transaction != "WT" + bill.instance_id[-14:].upper():
            raise FinanceError("The prepared test transaction changed.")
        return quote

    def authenticate(self, resolver, *, guard, session_id):
        self.execution_id = session_id
        self._test_request_blocked = False
        guard()
        self.event("test_bank_login_started", {})
        self._login()
        guard()
        self.event("test_bank_login_verified", {})

    def _request_allowed(self, request):
        path = ""
        try:
            u = urlsplit(request.url)
            if u.scheme != "https" or u.netloc != "s.hongleongconnect.my":
                return False
            path = u.path.split(";")[0]
            # No general same-origin bank permission. These are read/auth resources.
            if request.method == "GET" and (
                path
                in {
                    "/",
                    "/rib/app/fo/login",
                    "/rib/app/fo/main",
                    "/rib/app/fo/acct/inq",
                    "/rib/app/fo/trx/hist",
                }
                or (
                    request.resource_type in {"script", "stylesheet", "image", "font"}
                    and path.startswith(
                        (
                            "/rib/css/",
                            "/rib/js/",
                            "/rib/img/",
                            "/rib/fonts/",
                            "/rib/app/javax.faces.resource/",
                        )
                    )
                )
            ):
                return True
            if (
                request.method == "POST"
                and path in {"/rib/app/fo/login", "/rib/app/fo/doLogin"}
                and self._authorization is None
                and self._session is not None
            ):
                return True
            pending = self._pending
            if pending is None or request.url != pending["url"]:
                raise FinanceError("Unknown HLB bank request.")
            if self._test_request_blocked and pending["phase"] != "history_read":
                raise FinanceError("An earlier unknown bank request requires a fresh review.")
            if pending["phase"] != "history_read":
                authorization, bill, quote = self._authorization
                authorization.check(bill, quote)
                self._validate(bill)
            if request.method == "GET" and pending["phase"] == "entry":
                if pending.get("used"):
                    raise FinanceError("Repeated entry request.")
                pending["used"] = True
                return True
            if request.method != "POST":
                raise FinanceError("Unknown HLB bank method.")
            if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
                raise FinanceError("Unreviewed HLB request encoding.")
            raw = request.post_data or ""
            if len(raw) > 200_000:
                raise FinanceError("Oversized bank request.")
            pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
            body = dict(pairs)
            if len(body) != len(pairs):
                raise FinanceError("Ambiguous repeated bank fields.")
            expected, control = pending["fields"], pending["control"]
            if any(k not in expected and k not in JSF_METADATA and k != control for k in body):
                raise FinanceError("Unknown bank command field.")
            if any(body.get(k) != v for k, v in expected.items()):
                raise FinanceError("Bank request differs from the exact prepared form.")
            if body.get("javax.faces.source") not in {None, control}:
                raise FinanceError("Different bank action source.")
            if control not in body and body.get("javax.faces.source") != control:
                raise FinanceError("The requested bank command is not bound.")
            if control in body and body[control] != pending["control_value"]:
                raise FinanceError("The bank command value changed.")
            if pending["phase"] == "review":
                if self._review_dispatched or not self._review_claimed:
                    raise FinanceError("Review was already dispatched; never repeat it.")
                if any(body.get(k) != v for k, v in pending["material"].items()):
                    raise FinanceError("The exact approved debit changed.")
                self._review_dispatched = True
                self.event("test_review_request_dispatched", {"transaction": quote.transaction})
            elif pending.get("used") and pending["phase"] != "history_read":
                raise FinanceError("Repeated bank form request.")
            pending["used"] = True
            return True
        except Exception:
            self._test_request_blocked = True
            # Never retain request values, cookies, CSRF state or credential errors.
            self.event(
                "test_bank_request_blocked",
                {
                    "method": request.method,
                    "path": urlsplit(request.url).path.split(";")[0],
                    "phase": self._pending["phase"] if self._pending else "unmapped",
                    "field_names": sorted(dict(parse_qsl(request.post_data or "")).keys())
                    if request.method == "POST"
                    and path not in {"/rib/app/fo/login", "/rib/app/fo/doLogin"}
                    else [],
                },
            )
            return False

    def _command(self, element, action, *, phase="edit", updates=None, material=None):
        data = element.evaluate("""e => {
            const f = e.form || e.closest('form');
            if (!f) throw Error();
            return {url:f.action, control:e.name || e.id, control_value:e.value || e.id,
                fields:Array.from(new FormData(f).entries())};
        }""")
        if data["url"].split(";", 1)[0].split("?", 1)[0] not in {ENTRY, HISTORY}:
            raise FinanceError("Unreviewed bank form action.")
        fields = dict(data["fields"])
        if len(fields) != len(data["fields"]) or not data["control"]:
            raise FinanceError("Ambiguous bank form fields.")
        fields.update(updates or {})
        self._pending = {
            **data,
            "fields": fields,
            "phase": phase,
            "material": material or {},
        }
        try:
            action()
            # Allow only the exact command's response/redirect to complete.
            element.page.wait_for_timeout(700)
            if self._test_request_blocked and phase != "history_read":
                raise FinanceError("An unreviewed bank request was blocked; no automatic retry.")
        finally:
            self._pending = None

    @staticmethod
    def _rows(frame):
        result = {}
        labels = {
            "Recipient Bank / Wallet Name",
            "Account Number",
            "Recipient Name",
            "Amount (MYR)",
            "Recipient Reference",
            "Other Payment Detail",
            "Reference Number",
            "Transaction Status",
            "When to Transfer",
            "Transaction Date",
            "Transfer From",
            "From Account",
            "Charges",
            "Transaction Fee",
            "Service Charge",
        }
        for row in frame.locator("tr").all():
            cells = row.locator("th,td")
            if cells.count() != 2 or not row.is_visible():
                continue
            label = " ".join(cells.first.inner_text().split())
            if label not in labels:
                continue
            if label in result:
                raise FinanceError("Ambiguous HLB evidence labels.")
            cell = cells.nth(1)
            controls = cell.locator("input:not([type=hidden]),select")
            result[label] = (
                controls.input_value() if controls.count() == 1 else cell.inner_text()
            ).strip()
        return result

    def _form(self, page):
        frames = [
            f
            for f in page.frames
            if urlsplit(f.url).path.split(";")[0] == "/rib/app/fo/trx/3rdpartytrsf"
        ]
        if len(frames) != 1:
            raise FinanceError("HLB transfer document is missing or ambiguous.")
        check_hlb_origin(frames[0])
        return frames[0]

    def _check_recipient(self, form):
        fields = self._rows(form)
        account = re.sub(r"[\s-]", "", fields.get("Account Number", ""))
        bank = " ".join(fields.get("Recipient Bank / Wallet Name", "").upper().split())
        name = fields.get("Recipient Name", "")
        if (
            account != self.source.policy["destination_account"]
            or not ("UNITED OVERSEAS" in bank or bank in {"UOB", "UOB BANK"})
            or not name
        ):
            raise FinanceError("Selected HLB recipient does not match the owner UOB allowlist.")
        if self._recipient_name and name != self._recipient_name:
            raise FinanceError("HLB recipient name changed.")
        self._recipient_name = name

    def _select(self, form, selector, predicate):
        field = form.locator(selector)
        if field.count() != 1 or not field.is_visible():
            raise FinanceError("Expected bank selector is missing.")
        choices = [o for o in field.locator("option").all() if predicate(o.inner_text())]
        if len(choices) != 1:
            raise FinanceError("Bank option cannot be selected confidently.")
        value = choices[0].get_attribute("value")
        if field.input_value() != value:
            self._command(
                field,
                lambda: field.select_option(value=value, timeout=5000),
                updates={field.get_attribute("name"): value},
            )
        return value

    def _mapping(self, form):
        controls = form.locator("input:not([type=hidden]),select,button").evaluate_all(
            "es=>es.filter(e=>e.offsetWidth||e.offsetHeight).map(e=>"
            "({id:e.id,name:e.name,type:e.type,tag:e.tagName}))"
        )
        self.event("test_transfer_control_mapping", {"controls": controls})

    def submit(self, bill, quote, *, guard):
        self._validate(bill)
        authorization = require_payment_submission(bill, quote)
        if quote.transaction in self._submitted:
            raise FinanceError("This test attempt has already entered the bank flow.")
        self._submitted.add(quote.transaction)
        self._review_dispatched = self._review_claimed = False
        self._recipient_name = ""
        self._verified_detail = self._receipt_content = None
        self._authorization = (authorization, bill, quote)
        try:
            guard()
            page = self._page()
            self._pending = {"phase": "entry", "url": ENTRY}
            try:
                page.goto(ENTRY, wait_until="domcontentloaded", timeout=30000)
            finally:
                self._pending = None
            form = self._form(page)
            self._mapping(form)
            favourites = []
            for link in form.locator("a").all():
                label = " ".join(link.inner_text().split())
                if (
                    "transfer >" in label.casefold()
                    and "sean uob" in label.casefold()
                    and "".join(re.findall(r"\d", label))
                    == self.source.policy["destination_account"]
                ):
                    favourites.append(link)
            if len(favourites) != 1:
                raise FinanceError(
                    "Sean UOB favourite is missing or ambiguous; no beneficiary changed."
                )
            guard()
            self._command(favourites[0], lambda: favourites[0].click(timeout=5000))
            form = self._form(page)
            self._check_recipient(form)
            self.event("test_recipient_verified", {"name": self._recipient_name})
            self._mapping(form)
            source_value = self._select(
                form,
                "#idSOMTransferFrom",
                lambda label: any(
                    n.endswith(self.source.policy["funding_last4"])
                    for n in re.findall(r"\d{4,}", re.sub(r"[\s-]", "", label))
                ),
            )
            self._select(
                form,
                "#idSOMTrsfMode",
                lambda label: bool(re.search(r"duitnow|instant", label, re.I)),
            )
            once = form.locator('[id="idRecur:0"]')
            if once.count() != 1 or not once.is_checked():
                raise FinanceError("Only the bank's one-time transfer mode is permitted.")
            once_label = form.locator('label[for="idRecur:0"]')
            if once_label.count() != 1 or " ".join(
                once_label.inner_text().casefold().split()
            ) not in {
                "one time",
                "one-time",
                "now",
                "immediate",
            }:
                raise FinanceError("The bank's one-time transfer label changed.")
            when = form.locator("#idOneTimeDate").input_value()
            today = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()
            if datetime.strptime(when, "%d-%b-%Y").date() != today:
                raise FinanceError("A current one-time transfer date is required.")
            self._select(
                form, "#idRR", lambda label: label.strip().casefold() in {"other", "others"}
            )
            material = {
                "idAmt": "1.00",
                "idDesc": bill.reference,
                "idOthInfo": quote.transaction,
                "idSOMTransferFrom": source_value,
            }
            for key in ("idAmt", "idDesc", "idOthInfo"):
                field = form.locator("#" + key)

                def fill(field=field, value=material[key]):
                    field.fill(value, timeout=5000)
                    field.press("Tab")

                self._command(field, fill, updates={field.get_attribute("name"): material[key]})
            terms = form.locator("#idSBCTnc")
            if terms.count() != 1:
                raise FinanceError("The reviewed DuitNow transfer terms control is unavailable.")
            if not terms.is_checked():
                row = terms.locator("xpath=ancestor::tr[1]")
                if (
                    "I have read, understood and accept the DuitNow Terms & Conditions"
                    not in row.inner_text()
                ):
                    raise FinanceError("Bank transfer terms changed.")
                link = row.locator("a")
                if (
                    link.count() != 1
                    or link.get_attribute("href")
                    != "https://www.hlb.com.my/content/dam/hlb/my/images/HL_Connect/Connect/"
                    "duitnow/hlb-duitnow-tnc-en.pdf"
                ):
                    raise FinanceError("Bank transfer terms link changed.")
                self._command(
                    terms,
                    lambda: terms.check(timeout=5000),
                    updates={
                        terms.get_attribute("name"): terms.get_attribute("value") or "on",
                    },
                )
            self._check_recipient(form)
            for key, value in material.items():
                if form.locator("#" + key).input_value() != value:
                    raise FinanceError("HLB form differs from the approved RM1 test.")
            # Check any displayed fee before the initiating action; reject nonzero fees.
            for label, value in self._rows(form).items():
                if label in {"Charges", "Transaction Fee", "Service Charge"} and (
                    re.sub(r"[^0-9.]", "", value) not in {"0", "0.00"}
                ):
                    raise FinanceError("A nonzero or unreadable HLB fee is not authorized.")
            guard()
            authorization.check(bill, quote)
            button = form.locator("#idBtnSubmit")
            if button.count() != 1 or button.input_value().strip().casefold() not in {
                "next",
                "review",
            }:
                raise FinanceError("The bank's initiating Next/Review control changed.")
            self._review_claimed = True  # Claim BEFORE click, including timeout/lost response.
            self.event("test_review_claimed", {"transaction": quote.transaction})
            self._command(
                button, lambda: button.click(timeout=15000), phase="review", material=material
            )
            if not self._review_dispatched:
                raise FinanceError("The bank request was blocked or validation did not advance.")
        except Exception as error:
            if not self._review_dispatched:
                self.event(
                    "test_stopped_before_initiating_request",
                    {
                        "reason": str(error)
                        if type(error) is FinanceError
                        else "Bank control mapping failed.",
                        "category": type(error).__name__,
                    },
                )
                raise PaymentNotSubmitted("Test stopped before any initiating request.") from None
            # A dispatched Next may have initiated AppAuthorise even when its
            # response/polling was lost or blocked. Recover only through history.
            raise MandatoryChallenge(
                "HLB outcome pending; verify without another transfer."
            ) from None
        raise MandatoryChallenge("Authorize this exact RM1 transfer in the HLB app.")

    def _detail(self, bill, quote):
        with self._read_page(HISTORY) as page:
            candidates = []
            for link in page.locator('a[id$=":test"]').all():
                if not re.fullmatch(r"j_idt\d+:\d+:test", link.get_attribute("id") or ""):
                    continue
                cells = link.locator("xpath=ancestor::tr[1]").locator("td")
                if cells.count() != 8:
                    raise FinanceError("HLB history row mapping changed.")
                dest = re.sub(r"\D", "", cells.nth(3).inner_text())
                amount = re.sub(r"[^0-9.]", "", cells.nth(4).inner_text())
                if self.source.policy["destination_account"] in dest and amount == "1.00":
                    funding = re.sub(r"\D", "", cells.nth(2).inner_text())
                    if not funding.endswith(self.source.policy["funding_last4"]):
                        continue
                    candidates.append(link)
            if not candidates:
                raise FinanceError("No independently matching test history row yet.")
            # Never confuse a prior RM1 test with this unique approved tracking reference.
            # A fresh history document per candidate avoids stale row locators.
            for index in range(min(len(candidates), 20)):
                if index:
                    page.goto(HISTORY, wait_until="domcontentloaded")
                    links = page.locator('a[id$=":test"]').all()
                    candidates = [
                        a
                        for a in links
                        if (
                            a.locator("xpath=ancestor::tr[1]").locator("td").count() == 8
                            and self.source.policy["destination_account"]
                            in re.sub(
                                r"\D",
                                "",
                                a.locator("xpath=ancestor::tr[1]")
                                .locator("td")
                                .nth(3)
                                .inner_text(),
                            )
                            and re.sub(
                                r"[^0-9.]",
                                "",
                                a.locator("xpath=ancestor::tr[1]")
                                .locator("td")
                                .nth(4)
                                .inner_text(),
                            )
                            == "1.00"
                        )
                    ]
                link = candidates[index]
                self._command(
                    link, lambda link=link: link.click(timeout=10000), phase="history_read"
                )
                fields = self._rows(page)
                if fields.get("Other Payment Detail") != quote.transaction:
                    continue
                if (
                    re.sub(r"[\s-]", "", fields.get("Account Number", ""))
                    != self.source.policy["destination_account"]
                    or fields.get("Amount (MYR)") != "1.00"
                    or fields.get("Recipient Reference") != bill.reference
                    or "UNITED OVERSEAS"
                    not in fields.get("Recipient Bank / Wallet Name", "").upper()
                    or fields.get("Recipient Name") != self._recipient_name
                ):
                    raise FinanceError("Independent bank detail contradicts the exact test.")
                reference = fields.get("Reference Number", "")
                if not reference:
                    raise FinanceError("Bank-issued transaction reference is missing.")
                return fields
        raise FinanceError("This exact test has no independently verified bank detail.")

    def verify(self, bill, quote):
        self._validate(bill)
        detail = self._detail(bill, quote)
        status = {"Successful": "settled", "Pending": "pending", "Failed": "failed"}.get(
            detail.get("Transaction Status")
        )
        if not status:
            raise FinanceError("The bank has not confirmed the transfer outcome.")
        self._verified_detail = detail
        return Settlement(
            quote.transaction,
            quote.bill_digest,
            quote.funding,
            detail["Reference Number"],
            status,
            digest(detail),
        )

    def await_authorization(self, *, guard):
        _, bill, quote = self._authorization
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            guard()
            try:
                if self.verify(bill, quote).status in {"settled", "failed"}:
                    return
            except FinanceError:
                pass
            self._page().wait_for_timeout(3000)
        raise MandatoryChallenge("HLB outcome remains pending; reconcile without another transfer.")

    def receipt(self, bill, quote, settlement):
        if settlement.status != "settled" or self.verify(bill, quote) != settlement:
            raise FinanceError("A receipt requires this exact independently verified transfer.")
        contents = []
        page = self._page()

        def download(item):
            try:
                data = Path(item.path()).read_bytes()
                if data.startswith(b"%PDF-") and 20 <= len(data) <= 10_000_000:
                    contents.append(data)
            except Exception:
                pass

        def response(item):
            try:
                if urlsplit(item.url).netloc != "s.hongleongconnect.my":
                    return
                mime = item.headers.get("content-type", "").casefold()
                if "application/pdf" not in mime and "application/octet-stream" not in mime:
                    return
                data = item.body()
                if data.startswith(b"%PDF-") and 20 <= len(data) <= 10_000_000:
                    contents.append(data)
            except Exception:
                pass

        with self._read_page(HISTORY) as history:
            # Re-select the exact bank-issued reference, never a receipt from the submit response.
            links = [
                a
                for a in history.locator('a[id$=":test"]').all()
                if a.inner_text().strip() == settlement.reference
            ]
            if len(links) != 1:
                raise FinanceError("Exact bank receipt history link is ambiguous.")
            self._command(links[0], lambda: links[0].click(timeout=10000), phase="history_read")
            fields = self._rows(history)
            if fields != self._verified_detail:
                raise FinanceError("Bank receipt detail changed.")
            button = history.locator('input[value="Get Receipt"]')
            if button.count() != 1:
                raise FinanceError("Original bank receipt control is unavailable.")
            watched = list(page.context.pages)

            def new_page(p):
                watched.append(p)
                p.on("download", download)

            for owned in watched:
                owned.on("download", download)
            page.context.on("page", new_page)
            page.context.on("response", response)
            try:
                self._command(button, lambda: button.click(timeout=10000), phase="history_read")
                deadline = time.monotonic() + 20
                while not contents and time.monotonic() < deadline:
                    history.wait_for_timeout(250)
            finally:
                page.context.remove_listener("page", new_page)
                page.context.remove_listener("response", response)
                for owned in watched:
                    owned.remove_listener("download", download)
        unique = {hashlib.sha256(b).hexdigest(): b for b in contents}
        if len(unique) != 1:
            raise FinanceError("Original HLB PDF retrieval is unconfirmed; no synthetic receipt.")
        detail = self._verified_detail
        date = datetime.strptime(detail["When to Transfer"], "%d-%b-%Y").date().isoformat()
        return BankReceipt(
            quote.transaction,
            quote.bill_digest,
            settlement.reference,
            date,
            next(iter(unique.values())),
        )
