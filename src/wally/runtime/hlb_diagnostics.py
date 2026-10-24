"""Explicit operator nonfinancial HLB inspection; never a payment worker.

Only the observed empty-form setup and existing favourite lookup may POST.
No amount entry, Next/Review, app authorization or receipt email is available.
"""

import json
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from wally.adapters.browser.hlb_test_transfer import ENTRY, HISTORY, HLBTestTransferProvider
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.secrets import create_secrets_provider
from wally.audit.logger import AuditLogger
from wally.config.loader import load_settings
from wally.finance.models import FinanceError
from wally.finance.test_transfers import OwnerAccountTestSource
from wally.runtime.secret_resolver import GovernedSecretsResolver


class HLBNonfinancialInspection(HLBTestTransferProvider):
    allow_receipt_downloads = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lookup = None
        self._lookup_used = False
        self._lookup_error = False
        self.inspection_stage = "not_started"

    def submit(self, *args, **kwargs):
        raise FinanceError("Nonfinancial inspection cannot submit a payment.")

    def _request_allowed(self, request):
        if urlsplit(request.url).path.split(";")[0] != urlsplit(ENTRY).path:
            # No inherited command scope, payment authorization or history POST.
            return super()._request_allowed(request)
        try:
            if self._test_request_blocked:
                raise FinanceError("Inspection stopped after an unknown request.")
            pending = self._pending
            parameters = parse_qsl(urlsplit(request.url).query, keep_blank_values=True)
            if (
                request.method == "GET" and self._lookup_used and not self._lookup_error
                and self._lookup and request.url.split("?", 1)[0] == ENTRY
                and len(parameters) == 2 and {k for k, _ in parameters} == {"error", "o"}
                and dict(parameters)["o"] == "1"
            ):
                self._lookup_error = True
                self.event("inspection_bank_lookup_error_redirect", {})
                return True  # Read the bank's error page; no form command is authorized.
            if pending and pending["phase"] == "entry":
                if request.method == "GET" and request.url == pending["url"]:
                    if pending.get("used"):
                        raise FinanceError("Repeated inspection entry.")
                    pending["used"] = True
                    return True
                if request.method == "POST" and not self._initialization_dispatched:
                    self._initialization_shape(request, pending)
                    self._initialization_dispatched = True
                    self._initialization_request = request
                    return True
            lookup = self._lookup
            if (
                not lookup or self._lookup_used or request.method != "POST"
                or request.url != lookup["url"]
                or "application/x-www-form-urlencoded"
                not in request.headers.get("content-type", "")
            ):
                raise FinanceError("Inspection cannot issue this command.")
            pairs = parse_qsl(request.post_data or "", keep_blank_values=True, strict_parsing=True)
            body = dict(pairs)
            metadata = {
                "javax.faces.source": lookup["control"],
                "javax.faces.partial.execute": lookup["control"],
                "javax.faces.partial.render": "idFormEntry",
                "javax.faces.behavior.event": "action",
                "javax.faces.partial.event": "click",
                "javax.faces.partial.ajax": "true",
            }
            if (
                len(body) != len(pairs)
                or set(body) != set(lookup["fields"]) | set(metadata)
                or any(body.get(k) != v for k, v in lookup["fields"].items())
                or any(body.get(k) != v for k, v in metadata.items())
                or body.get("idAmt") not in {None, ""}
                or body.get("idOthInfo") not in {None, ""}
            ):
                self.event("inspection_lookup_shape", {
                    "field_names": sorted(body),
                    "extra_fields": sorted(set(body) - set(lookup["fields"]) - set(metadata)),
                    "missing_fields": sorted(set(lookup["fields"]) - set(body)),
                    "changed_fields": sorted(
                        k for k, v in lookup["fields"].items() if body.get(k) != v
                    ),
                    "metadata_matches": {k: body.get(k) == v for k, v in metadata.items()},
                })
                raise FinanceError("Favourite lookup differs from the observed nonpayment shape.")
            self._lookup_used = True
            return True
        except Exception as error:
            self._test_request_blocked = True
            fields = dict(parse_qsl(
                request.post_data or "", keep_blank_values=True
            )) if request.method == "POST" else {}
            self.event("inspection_request_blocked", {
                "method": request.method,
                "path": urlsplit(request.url).path.split(";")[0],
                "session_path_suffix": ";" in urlsplit(request.url).path,
                "query_keys": [k for k, _ in parse_qsl(urlsplit(request.url).query)],
                "entry_url_match": request.url == ENTRY,
                "pending_url_match": bool(self._pending)
                and request.url == self._pending["url"],
                "encoding": "form" if "application/x-www-form-urlencoded"
                in request.headers.get("content-type", "") else "other_or_missing",
                "phase": self._pending["phase"] if self._pending else "none",
                "reason": str(error) if type(error) is FinanceError else type(error).__name__,
                "field_names": sorted(fields),
                "duplicate_fields": sorted({
                    k for k, _ in parse_qsl(request.post_data or "", keep_blank_values=True)
                    if sum(key == k for key, _ in parse_qsl(
                        request.post_data or "", keep_blank_values=True
                    )) > 1
                }),
                "metadata": {
                    k: v if re.fullmatch(r"[@A-Za-z0-9_:.\s-]{0,300}", v) else "unmapped"
                    for k, v in fields.items() if k.startswith("javax.faces.")
                    and k != "javax.faces.ViewState"
                },
            })
            return False

    def inspect_form(self):
        self.inspection_stage = "login"
        self._login()
        self.event("inspection_login_verified", {})
        page = self._page()
        self.inspection_stage = "empty_form_setup"
        self._enter_transfer(page)
        self.event("inspection_empty_form_ready", {})
        form = self._form(page)
        self._mapping(form)
        favourites = [
            a for a in form.locator("a").all()
            if "sean uob" in " ".join(a.inner_text().casefold().split())
            and "".join(re.findall(r"\d", a.inner_text()))
            == self.source.policy["destination_account"]
        ]
        if len(favourites) != 1:
            raise FinanceError("Exact existing favourite is unavailable.")
        link = favourites[0]
        # Native JSF favourite selection, only @this; never click an unknown handler.
        script = link.get_attribute("onclick") or ""
        if not re.fullmatch(
            r"mojarra\.ab\(this,event,'action','@this','idFormEntry'\);return false", script.strip()
        ):
            raise FinanceError("Favourite handler requires mapping; no command dispatched.")
        data = self._command_data(link)
        fields = dict(data["fields"])
        if (
            len(fields) != len(data["fields"])
            or not re.fullmatch(r"j_idt\d+(?::[\w-]+)*", data["control"])
            or fields.get("idAmt") not in {None, ""}
            or fields.get("idOthInfo") not in {None, ""}
        ):
            raise FinanceError("Favourite lookup is not an empty nonfinancial form.")
        fields.update(data["jsf_buttons"])
        self._lookup = {**data, "fields": fields}
        self.inspection_stage = "favourite_lookup"
        try:
            link.click(timeout=10000)
            page.wait_for_timeout(1000)
        finally:
            self._lookup = None
        if self._lookup_error:
            text = self._form(page).locator("body").inner_text()
            self.event("inspection_bank_error_categories", {"notices": [
                word for word in ("unable", "expired", "invalid", "maintenance", "try again",
                                  "session", "unavailable", "error") if word in text.casefold()
            ]})
            notices = [
                " ".join(line.split()) for line in text.splitlines()
                if re.search(r"maintenance|unable|expired|invalid|unavailable|error", line, re.I)
            ]
            self.event("inspection_bank_error_notice", {"lines": [
                re.sub(r"[\w.+-]+@[\w.-]+|(?:\d[ -]?){5,}", "[redacted]", line)[:300]
                for line in notices[:3]
            ]})
            raise FinanceError(
                "HLB rejected the nonfinancial favourite lookup; no payment initiated."
            )
        if self._test_request_blocked or not self._lookup_used:
            raise FinanceError("Favourite lookup was not verified; stopped without retry.")
        form = self._form(page)
        self._check_recipient(form)
        self._mapping(form)
        # Expose fixed labels/control shapes, never form values, ViewState or credentials.
        self.event("inspection_recipient_verified", {"destination_suffix": "2747"})
        options = {}
        for field in form.locator("select").all():
            key = field.get_attribute("id")
            if key in {"idSOMTrsfMode", "idRR"}:
                options[key] = [o.inner_text().strip() for o in field.locator("option").all()]
        self.event("inspection_select_labels", options)
        once = form.locator('[id="idRecur:0"]')
        once_label = form.locator('label[for="idRecur:0"]')
        next_button = form.locator("#idBtnSubmit")
        self.event("inspection_form_constraints", {
            "one_time_checked": once.count() == 1 and once.is_checked(),
            "one_time_label": once_label.inner_text().strip() if once_label.count() == 1 else "",
            "current_date_matches": form.locator("#idOneTimeDate").input_value()
            == datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d-%b-%Y"),
            "terms_control_present": form.locator("#idSBCTnc").count() == 1,
            "next_label": next_button.input_value() if next_button.count() == 1 else "",
        })
        handlers = {}
        for key in ("idSOMTransferFrom", "idSOMTrsfMode", "idRR", "idDesc", "idAmt",
                    "idOthInfo", "idSBCTnc", "idBtnSubmit"):
            field = form.locator("#" + key)
            if field.count() != 1:
                continue
            handlers[key] = {
                event: re.findall(r"([A-Za-z_$][\w.$]*)\s*\(", field.get_attribute(event) or "")
                for event in ("onchange", "onblur", "onclick")
            }
        self.event("inspection_handler_functions", handlers)
        self.inspection_stage = "history_read"
        with self._read_page(HISTORY) as history:
            self.event("inspection_history_shape", {
                "reference_links": history.locator('a[id$=":test"]').count(),
                "matching_rm1_rows": len(self._history_candidates(history)),
            })


def main():
    settings = load_settings()
    browser = PlaywrightBrowserAdapter(headless=False, channel=settings.browser_channel)
    resolver = GovernedSecretsResolver(
        create_secrets_provider(settings), audit=AuditLogger(settings.audit_directory)
    )
    provider = HLBNonfinancialInspection(
        browser, resolver,
        OwnerAccountTestSource(settings.project_root / "config/test-transfer.yaml"),
        event=lambda name, details: print(json.dumps({"event": name, **details}), flush=True),
    )
    try:
        print("Nonfinancial inspection: governed 1Password read, then HLB login.", flush=True)
        provider.inspect_form()
        print("Nonfinancial inspection completed; no initiating action available.", flush=True)
        return 0
    except Exception as error:
        print(json.dumps({"event": "inspection_stopped", "stage": provider.inspection_stage,
                          "category": type(error).__name__,
                          "reason": str(error) if type(error) is FinanceError else ""}), flush=True)
        return 1
    finally:
        provider.close()
        browser.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
