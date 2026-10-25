"""Live HLB RM1 owner-account acceptance, exclusively under the exact worker scope.

Preparation is local. Login, favourite selection and every transfer request occur
after Telegram authorization. Unrecognized commands are aborted, never retried.
Only Next initiates this transfer; no subsequent Submit control is ever clicked.
"""

import hashlib
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import CodeType, SimpleNamespace
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from wally.adapters.browser.hlb import HLB_ORIGIN, check_hlb_origin
from wally.adapters.browser.hlb_transfer import HLBTransferProvider
from wally.exceptions import ProviderUnavailableError
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


def _loaded_code(value):
    """Stable semantic material; marshal bytes vary with active constant references."""
    if isinstance(value, CodeType):
        return [
            "code",
            value.co_code.hex(),
            value.co_exceptiontable.hex(),
            value.co_argcount,
            value.co_posonlyargcount,
            value.co_kwonlyargcount,
            value.co_flags,
            value.co_names,
            value.co_varnames,
            value.co_freevars,
            value.co_cellvars,
            _loaded_code(value.co_consts),
        ]
    if isinstance(value, tuple):
        return ["tuple", [_loaded_code(item) for item in value]]
    if isinstance(value, frozenset):
        return ["frozenset", sorted((_loaded_code(item) for item in value), key=repr)]
    return [type(value).__name__, repr(value)]


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
        self._recipient_favourite = ""
        self._verified_detail = None
        self._receipt_content = None
        self.execution_id = ""
        self._test_request_blocked = False
        self._initialization_dispatched = False
        self._initialization_request = None

    @property
    def fingerprint(self):
        return digest(
            [
                "hlb_owner_account_test_v1",
                self.source.policy,
                ENTRY,
                HISTORY,
                *[
                    digest(_loaded_code(getattr(type(self), method).__code__))
                    for method in (
                        "submit",
                        "_request_allowed",
                        "_command",
                        "_command_data",
                        "verify",
                        "receipt",
                        "_check_recipient",
                        "_select",
                        "_rows",
                        "_detail",
                        "_history_candidates",
                        "_validate",
                        "inspect",
                        "prepare",
                        "authenticate",
                        "await_authorization",
                        "_enter_transfer",
                        "_form",
                        "_allow_initialization",
                        "_initialization_shape",
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
        try:
            self._login()
        except ProviderUnavailableError as error:
            self.event("test_login_provider_unavailable", {"provider": error.provider})
            raise
        except FinanceError:
            self.event("test_bank_login_unverified", {})
            raise
        guard()
        self.event("test_bank_login_verified", {})

    def _request_allowed(self, request):
        path = ""
        stage = "origin"
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
            stage = "command"
            pending = self._pending
            if pending and pending["phase"] == "entry" and request.method == "POST":
                stage = "initialization"
                return self._allow_initialization(request, pending)
            if pending is None or request.url != pending["url"]:
                raise FinanceError("Unknown HLB bank request.")
            if self._test_request_blocked and pending["phase"] != "history_read":
                raise FinanceError("An earlier unknown bank request requires a fresh review.")
            if pending["phase"] != "history_read":
                stage = "authorization"
                authorization, bill, quote = self._authorization
                authorization.check(bill, quote)
                stage = "beneficiary_policy"
                self._validate(bill)
            stage = "request_shape"
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
            if body.get("javax.faces.partial.ajax") == "true":
                expected = {**expected, **pending.get("jsf_buttons", {})}
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
        except Exception as error:
            self._test_request_blocked = True
            fields = (
                dict(parse_qsl(request.post_data or ""))
                if request.method == "POST"
                and path not in {"/rib/app/fo/login", "/rib/app/fo/doLogin"}
                else {}
            )
            source = fields.get("javax.faces.source", "")
            # Never retain request values, cookies, CSRF state or credential errors.
            self.event(
                "test_bank_request_blocked",
                {
                    "method": request.method,
                    "path": urlsplit(request.url).path.split(";")[0],
                    "phase": self._pending["phase"] if self._pending else "unmapped",
                    "stage": stage,
                    "category": type(error).__name__,
                    "reason": str(error)
                    if stage == "authorization"
                    and type(error) is FinanceError
                    and str(error)
                    in {
                        "A fresh exact Telegram payment authorization is required.",
                        "Execution authorization expired or lease lost.",
                        "Payment changed before submission.",
                        "Payment facts, policy, decision or Matter changed.",
                        "Payment authorization changed during current-evidence reads.",
                        "Prepared transaction, duplicate check or exact debit is unsafe.",
                    }
                    else "unclassified",
                    "field_names": sorted(fields),
                    "jsf_source": source
                    if re.fullmatch(r"(?:id[A-Za-z0-9_:.-]{1,70}|j_idt[0-9_:.-]{1,70})", source)
                    else "unmapped",
                    "jsf_event": fields.get("javax.faces.behavior.event")
                    if fields.get("javax.faces.behavior.event")
                    in {"action", "change", "click", "blur"}
                    else "unmapped",
                    "jsf_metadata": {
                        key: value
                        if re.fullmatch(r"[@A-Za-z0-9_:.\s-]{0,300}", value)
                        else "unmapped"
                        for key, value in fields.items()
                        if key in JSF_METADATA
                    },
                    "material_fields_present": sorted(
                        set(fields) & {"idAmt", "idAcctNo", "idBnk", "idDesc", "idOthInfo"}
                    ),
                },
            )
            return False

    def _allow_initialization(self, request, pending):
        """Forward one observed bank-generated device setup, never construct it."""
        authorization, bill, quote = self._authorization
        authorization.check(bill, quote)
        self._validate(bill)
        if self._test_request_blocked or self._initialization_dispatched:
            raise FinanceError("Repeated or previously blocked bank initialization.")
        self._initialization_shape(request, pending)
        self._initialization_dispatched = True
        self._initialization_request = request
        self.event(
            "test_bank_initialization_dispatched", {"control": "mobileDeviceFlagUpdateBtn2"}
        )
        return True

    @staticmethod
    def _initialization_shape(request, pending):
        """Validate the observed setup contract independently of payment authority."""
        destination_matches = request.url in {ENTRY, pending["url"]}
        if not destination_matches:
            url = urlsplit(request.url)
            query = parse_qsl(url.query, keep_blank_values=True)
            # HLB redirects entry into a server-owned Spring execution. Pin its
            # current native form action; never invent or retain the flow token.
            destination_matches = (
                request.url.split("?", 1)[0] == ENTRY
                and len(query) == 1 and query[0][0] == "execution"
                and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", query[0][1]))
                and not url.fragment
                and request.frame.locator("form#idFormEntry").evaluate("e => e.action")
                == request.url
            )
        if not destination_matches or (
            "application/x-www-form-urlencoded" not in request.headers.get("content-type", "")
        ):
            raise FinanceError("Unexpected bank initialization destination or encoding.")
        raw = request.post_data or ""
        if len(raw) > 200_000:
            raise FinanceError("Oversized bank initialization.")
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        fields = dict(pairs)
        metadata = {
            "javax.faces.source": "mobileDeviceFlagUpdateBtn2",
            "javax.faces.partial.event": "click",
            "javax.faces.partial.execute": "mobileDeviceFlagUpdateBtn2 mobileDeviceFlagUpdateBtn2",
            "javax.faces.partial.render": "idFormEntry",
            "javax.faces.behavior.event": "action",
            "javax.faces.partial.ajax": "true",
        }
        standard = {
            "idBtnCancel",
            "idFormEntry",
            "idOneTimeDate",
            "idRecur",
            "idSOMTransferFrom",
            "javax.faces.ViewState",
        }
        # JSF serializes empty entry controls too; earlier diagnostics discarded
        # blank values. They must stay empty and cannot carry a debit/beneficiary.
        empty_controls = {
            "idAcctNo", "idAmt", "idOthInfo", "idSms", "idEmail", "idDesc",
        } & set(fields)
        if any(fields[key] for key in empty_controls):
            raise FinanceError("Bank initialization contains nonempty payment fields.")
        selectors = {"idAcctTyp", "idBnk", "idRR"} & set(fields)
        if any(
            request.frame.locator("#" + key).input_value() != fields[key] for key in selectors
        ):
            raise FinanceError("Bank initialization differs from its current selector defaults.")
        generated = set(fields) - standard - set(metadata) - empty_controls - selectors
        if len(fields) != len(pairs):
            raise FinanceError("Bank initialization repeats form fields.")
        if (
            len(generated) != 1
            or not re.fullmatch(r"j_idt\d+", next(iter(generated)))
            or set(fields) != standard | set(metadata) | generated | empty_controls | selectors
        ):
            raise FinanceError("Bank initialization field set differs from the observed command.")
        if any(fields.get(key) != value for key, value in metadata.items()):
            raise FinanceError("Bank initialization command metadata changed.")
        if not fields.get("javax.faces.ViewState") or not fields.get("idFormEntry"):
            raise FinanceError("Bank initialization lacks nonempty form or CSRF markers.")

    @staticmethod
    def _command_data(element):
        return element.evaluate("""e => {
            const f = e.form || e.closest('form');
            if (!f) throw Error();
            const fields = Array.from(new FormData(f).entries());
            const jsf_buttons = {};
            // Mojarra includes named input[type=button]; browser FormData omits them.
            for (const c of f.elements) {
                if (c.tagName === 'INPUT' && c.type === 'button' && c.name && !c.disabled) {
                    jsf_buttons[c.name] = c.value;
                }
            }
            return {url:f.action, control:e.name || e.id, control_value:e.value || e.id,
                fields, jsf_buttons};
        }""")

    def _command(self, element, action, *, phase="edit", updates=None, material=None):
        data = self._command_data(element)
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
            "Favourite Account Name",
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
        deadline = time.monotonic() + 15
        while True:
            frames = [
                f
                for f in page.frames
                if urlsplit(f.url).path.split(";")[0] == "/rib/app/fo/trx/3rdpartytrsf"
            ]
            if len(frames) == 1:
                check_hlb_origin(frames[0])
                frames[0].wait_for_load_state("domcontentloaded", timeout=15000)
                return frames[0]
            if frames or time.monotonic() >= deadline:
                self.event(
                    "test_transfer_frame_unavailable",
                    {
                        "paths": [urlsplit(f.url).path.split(";")[0] for f in page.frames],
                    },
                )
                raise FinanceError("HLB transfer document is missing or ambiguous.")
            page.wait_for_timeout(100)

    def _check_recipient(self, form):
        fields = self._rows(form)
        account = re.sub(r"[\s-]", "", fields.get("Account Number", ""))
        bank = " ".join(fields.get("Recipient Bank / Wallet Name", "").upper().split())
        # The saved-payee entry form exposes an alias, not a bank recipient name.
        # Both paths still require the exact approved bank/account in this form.
        name = fields.get("Recipient Name", "")
        favourite = fields.get("Favourite Account Name", "")
        if (
            account != self.source.policy["destination_account"]
            or not ("UNITED OVERSEAS" in bank or bank in {"UOB", "UOB BANK"})
            or (favourite and favourite != self.source.policy["favourite"])
            or not (name or favourite == self.source.policy["favourite"])
        ):
            raise FinanceError("Selected HLB recipient does not match the owner UOB allowlist.")
        if self._recipient_name and name and name != self._recipient_name:
            raise FinanceError("HLB recipient name changed.")
        if name:
            self._recipient_name = name
        if favourite:
            self._recipient_favourite = favourite

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

        def command_metadata(element):
            script = element.get_attribute("onclick") or ""
            literals = re.findall(r"['\"]([^'\"]*)['\"]", script)
            return {
                "functions": re.findall(r"([A-Za-z_$][\w.$]*)\s*\(", script),
                "ui_literals": [
                    literal
                    for literal in literals
                    if re.fullmatch(
                        r"(?:(?:id[A-Za-z0-9_:.-]+|j_idt[0-9_:.-]+|@this|@form|@all|"
                        r"action|click|change|blur)\s*)*",
                        literal,
                    )
                ],
            }

        setup = [
            {
                "id": button.get_attribute("id"),
                "name": button.get_attribute("name"),
                **command_metadata(button),
            }
            for button in form.locator('input[type="button"]').all()
        ]
        favourites = []
        for link in form.locator("a").all():
            label = " ".join(link.inner_text().split())
            if (
                "sean uob" in label.casefold()
                and "".join(re.findall(r"\d", label)) == self.source.policy["destination_account"]
            ):
                favourites.append({"visible": link.is_visible(), **command_metadata(link)})
        self.event(
            "test_transfer_setup_mapping", {"setup_controls": setup, "favourites": favourites}
        )

    def _enter_transfer(self, page):
        """Use the bank's interbank menu context, never the bare shared endpoint."""
        menus = []
        for frame in page.frames:
            if frame.url == "about:blank":
                continue
            check_hlb_origin(frame)
            menu = frame.locator("a").filter(has_text=re.compile(r"^PAY\s*&\s*TRANSACT$"))
            if menu.count() == 1 and menu.is_visible():
                menus.append(menu)
        if len(menus) != 1:
            raise FinanceError("The PAY & TRANSACT menu is unavailable or ambiguous.")
        menu = menus[0]
        toggle = urlsplit(menu.get_attribute("href") or "")
        if toggle.scheme or toggle.netloc or toggle.path or toggle.query:
            raise FinanceError("The PAY & TRANSACT toggle has an unexpected navigation target.")
        menu.click(timeout=5000)
        links = []
        for frame in page.frames:
            if frame.url == "about:blank":
                continue
            for link in frame.locator("a").all():
                if re.fullmatch(r"DuitNow to Account/\s*IBG", " ".join(link.inner_text().split())):
                    links.append(link)
        if len(links) != 1:
            raise FinanceError(
                "The exact PAY & TRANSACT DuitNow to Account/IBG menu is unavailable."
            )
        link = links[0]
        link.wait_for(state="visible", timeout=5000)
        target = link.evaluate("e => e.href")
        url = urlsplit(target)
        parameters = parse_qsl(url.query, keep_blank_values=True)
        if (
            target.split("?", 1)[0] != ENTRY
            or len(parameters) != 1
            or parameters[0][0] != "o"
            or not re.fullmatch(r"[0-9]", parameters[0][1])
            or url.fragment
        ):
            raise FinanceError("The bank's reviewed interbank menu destination changed.")
        self.event("test_interbank_menu_selected", {"label": "DuitNow to Account/IBG"})
        self._pending = {"phase": "entry", "url": target}
        try:
            link.click(timeout=15000)
            # The dashboard is already loaded; the menu navigates its child frame.
            # Keep the exact entry request scope alive until that document loads.
            self._form(page)
            deadline = time.monotonic() + 5
            while self._initialization_request is None:
                if self._test_request_blocked or time.monotonic() >= deadline:
                    raise FinanceError("The bank's expected initialization did not complete.")
                page.wait_for_timeout(100)
            response = self._initialization_request.response()
            if response is None or response.status != 200:
                raise FinanceError("Bank initialization response is unconfirmed.")
            response.finished()
            body = response.text()
            if "<partial-response" not in body or "<error" in body or "<redirect" in body:
                raise FinanceError("Bank initialization did not return its expected form update.")
            page.wait_for_timeout(700)  # Let the native JSF response update the form.
            self.event("test_bank_initialization_completed", {})
        finally:
            self._initialization_request = None
            self._pending = None

    def submit(self, bill, quote, *, guard):
        self._validate(bill)
        authorization = require_payment_submission(bill, quote)
        if quote.transaction in self._submitted:
            raise FinanceError("This test attempt has already entered the bank flow.")
        self._submitted.add(quote.transaction)
        self._review_dispatched = self._review_claimed = False
        self._initialization_dispatched = False
        self._initialization_request = None
        self._recipient_name = ""
        self._recipient_favourite = ""
        self._verified_detail = self._receipt_content = None
        self._authorization = (authorization, bill, quote)
        try:
            guard()
            page = self._page()
            self._enter_transfer(page)
            form = self._form(page)
            self._mapping(form)
            if self._test_request_blocked:
                raise FinanceError(
                    "Unmapped bank initialization was blocked before beneficiary selection."
                )
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
            self.event("test_recipient_verified", {
                "name": self._recipient_name, "favourite": self._recipient_favourite,
            })
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

    def _history_candidates(self, page):
        candidates = {}
        for link in page.locator('a[id$=":test"]').all():
            if not re.fullmatch(r"j_idt\d+:\d+:test", link.get_attribute("id") or ""):
                continue
            cells = link.locator("xpath=ancestor::tr[1]").locator("td")
            if cells.count() != 8:
                raise FinanceError("HLB history row mapping changed.")
            dest = re.sub(r"\D", "", cells.nth(3).inner_text())
            amount = re.sub(r"[^0-9.]", "", cells.nth(4).inner_text())
            funding = re.sub(r"\D", "", cells.nth(2).inner_text())
            if (
                dest == self.source.policy["destination_account"] and amount == "1.00"
                and funding.endswith(self.source.policy["funding_last4"])
            ):
                reference = link.inner_text().strip()
                if not reference or reference in candidates:
                    raise FinanceError("Ambiguous bank history references.")
                candidates[reference] = link
        if len(candidates) > 20:
            raise FinanceError("Test history requires a complete narrower read.")
        return candidates

    def _detail(self, bill, quote):
        with self._read_page(HISTORY) as page:
            references = list(self._history_candidates(page))
        # Bind each fresh row to its bank reference, never an index that can drift.
        for reference in references:
            with self._read_page(HISTORY) as page:
                candidates = self._history_candidates(page)
                if reference not in candidates:
                    raise FinanceError("Bank history changed during independent verification.")
                link = candidates[reference]
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
                    or not fields.get("Recipient Name")
                    or (self._recipient_name and fields["Recipient Name"] != self._recipient_name)
                    or not (
                        self._recipient_name
                        or self._recipient_favourite == self.source.policy["favourite"]
                    )
                    or fields.get("Reference Number") != reference
                    or datetime.strptime(fields.get("When to Transfer", ""), "%d-%b-%Y").date()
                    != datetime.fromtimestamp(
                        quote.expires_at - 900, ZoneInfo("Asia/Kuala_Lumpur")
                    ).date()
                ):
                    raise FinanceError("Independent bank detail contradicts the exact test.")
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
