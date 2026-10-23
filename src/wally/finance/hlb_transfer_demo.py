"""Offline HLB transfer acceptance in real Chrome; no external requests or real money.

python -B -m wally.finance.hlb_transfer_demo
The HLB URL is intercepted only to exercise the existing exact-origin picture gate.
Every request is fulfilled locally or aborted, including unknown paths/origins.
"""

import json
import tempfile
from dataclasses import asdict, replace
from html import escape
from pathlib import Path

from wally.adapters.browser.authentication import LoginState, LoginStep
from wally.adapters.browser.hlb import HLB_ORIGIN, HLBSecurityPicture
from wally.adapters.browser.hlb_transfer import (
    TRANSFER_FIELDS,
    HLBTransferProfile,
    HLBTransferProvider,
)
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.finance.payment_demo import CANARIES, PASS_REF, USER_REF, build_demo, prepare_card
from wally.runtime.hlb_authentication_demo import png

LOGIN = """<!doctype html><input id="idLoginId"><button id="idBtnSubmit1">Next</button>
<div id="picture-step" hidden><img id="picture" src="/fixture/picture.png">
<input id="idSBCBConfirmPic" type="checkbox"><input id="idPswd" type="password" readonly>
<button id="idBtnSubmit2">Login</button></div>
<iframe id="dashboard" hidden srcdoc="<a href='/logout'>Logout</a>"></iframe>
<script>
const el = id => document.getElementById(id);
el('idBtnSubmit1').onclick = () => {
 if (el('idLoginId').value === 'SYNTHETIC-LOGIN-CANARY') {
  el('picture-step').hidden = false; el('idLoginId').hidden = true;
 }
};
el('idPswd').onclick = () => {
 if (el('idSBCBConfirmPic').checked) el('idPswd').readOnly = false;
};
el('idBtnSubmit2').onclick = () => {
 if (el('idPswd').value === 'SYNTHETIC-PASSWORD-CANARY') {
  el('picture-step').hidden = true; el('dashboard').hidden = false;
 }
};
</script>"""


def fields_html(fields, *, ids=True):
    return "".join(
        f'<span class="{key}"' + (f' id="{key}"' if ids else "") + f">{escape(value)}</span>"
        for key, value in fields.items()
    )


def fixture_profile(*, submission_enabled=True):
    """Synthetic DOM contract, explicitly NOT a reviewed live HLB selector profile."""
    return HLBTransferProfile(
        id="offline-hlb-existing-beneficiary",
        version="1",
        entry_url=HLB_ORIGIN + "/fixture/entry",
        history_url=HLB_ORIGIN + "/fixture/history",
        beneficiary_url=HLB_ORIGIN + "/fixture/beneficiary",
        funding="HLB current ending 1234",
        funding_option="existing-source",
        beneficiary_option="existing-recipient",
        funding_selector="#source",
        beneficiary_selector="#recipient",
        amount_selector="#amount",
        reference_selector="#reference",
        tracking_selector="#tracking",
        review_selector="#review",
        submit_selector="#submit",
        review_fields=tuple(
            (k, "." + k)
            for k in (
                "bank",
                "destination",
                "payee",
                "amount",
                "currency",
                "reference",
                "funding",
                "tracking",
                "fee",
                "mode",
            )
        ),
        beneficiary_fields=tuple((k, "#" + k) for k in ("bank", "destination", "payee")),
        history_fields=tuple(
            (k, "." + k)
            for k in (
                "bank",
                "destination",
                "payee",
                "amount",
                "currency",
                "reference",
                "funding",
                "tracking",
                "receipt",
                "status",
            )
        ),
        history_rows=".transaction",
        history_complete_selector="#complete",
        scheduled_clear_selector="#no-scheduled",
        mandate_clear_selector="#no-mandates",
        challenge_selector="#app-required",
        result_selector="#bank-result",
        username_ref=USER_REF,
        password_ref=PASS_REF,
        app_timeout_seconds=2,
        sandbox=True,
        submission_enabled=submission_enabled,
        read_requests=(("GET", HLB_ORIGIN + "/fixture/picture.png"),
                       ("POST", HLB_ORIGIN + "/fixture/app-authorize")),
        payment_requests=(("POST", HLB_ORIGIN + "/fixture/submit"),),
        payment_fields=tuple((key, key) for key in TRANSFER_FIELDS),
    )


class SimulatedHLBBrowser(PlaywrightBrowserAdapter):
    offline_simulation = True

    def __init__(self):
        super().__init__(headless=True, channel="chrome")
        self.submissions = 0
        self.ledger_reads = 0
        self.authorizations = 0
        self.status = "settled"
        self.app_completes = True
        self.inventory_complete = True
        self.duplicate = False
        self.ledger_changes = {}
        self.review_changes = {}
        self.recipient_changes = {}
        self.drop_submit_response = False
        self.source = None
        self.reference = None
        self.sent_fields = None
        self.entry_reads = 0
        self.submit_clicks = 0
        self.on_entry = lambda: None
        self.retry_review = False
        self.unexpected_navigation = False

    def restrict_origins(self, session_id, origins):
        super().restrict_origins(session_id, origins)
        page = self._pages[session_id]
        if not getattr(page, "_fixture_routed", False):
            page.context.route("**/*", self._route)
            page._fixture_routed = True

    def expected(self):
        return HLBTransferProvider._expected(self.source.bill, "HLB current ending 1234")

    def _route(self, route):
        url = route.request.url
        if not url.startswith(HLB_ORIGIN + "/"):
            route.abort()
            return
        path = url.removeprefix(HLB_ORIGIN)
        if path == "/fixture/picture.png":
            route.fulfill(content_type="image/png", body=png())
            return
        if path == "/fixture/submit":
            self.submissions += 1
            self.sent_fields = route.request.post_data_json
            if self.drop_submit_response:
                route.abort()
            else:
                route.fulfill(content_type="application/json", body="{}")
            return
        if path == "/fixture/app-authorize":
            self.authorizations += 1
            route.fulfill(content_type="application/json", body="{}")
            return
        if path == "/fixture/final-submit":
            self.submit_clicks += 1
            route.fulfill(content_type="application/json", body="{}")
            return
        if path == "/":
            html = LOGIN
        elif path == "/fixture/beneficiary":
            html = fields_html(
                {
                    **{k: self.expected()[k] for k in ("bank", "destination", "payee")},
                    **self.recipient_changes,
                }
            )
        elif path == "/fixture/history":
            self.ledger_reads += 1
            html = '<div id="no-scheduled">None</div><div id="no-mandates">None</div>'
            if self.inventory_complete:
                html += '<div id="complete">All records loaded</div>'
            if self.submissions or self.duplicate:
                row = {
                    **(self.sent_fields or self.expected()),
                    "receipt": "SYNTHETIC-HLB-RECEIPT",
                    "status": self.status if self.authorizations else "pending",
                    **self.ledger_changes,
                }
                html += '<div class="transaction">' + fields_html(row) + "</div>"
        elif path == "/fixture/entry":
            self.entry_reads += 1
            self.on_entry()
            review = {**self.expected(), "fee": "0.00", "mode": "immediate", **self.review_changes}
            html = (
                """<select id="source"><option value="existing-source">Current</option></select>
            <select id="recipient">
            <option value="existing-recipient">Existing payee</option></select>
            <input id="amount"><input id="reference"><input id="tracking">
            <button id="review">Review</button>
            <div id="reviewed" hidden>"""
                + fields_html(review, ids=False)
                + """</div>
            <button id="submit" hidden>Submit once</button><div id="app-required" hidden>App</div>
            <div id="bank-result" hidden>Provider response</div><script>
            const el = id => document.getElementById(id);
            el('review').onclick = () => {
                REFLECT_INPUTS
                el('reviewed').hidden = false; el('submit').hidden = false;
                const data = Object.fromEntries(
                    [...document.querySelectorAll('#reviewed span')]
                    .map(e => [e.className, e.textContent]));
                delete data.fee; delete data.mode;
                fetch('/fixture/submit', {method: 'POST', body: JSON.stringify(data)}).then(() => {
                    el('app-required').hidden = false;
                    APP_AUTHORIZATION
                });
                RETRY_REVIEW
                UNEXPECTED_NAVIGATION
            };
            el('submit').onclick = () => {
                fetch('/fixture/final-submit', {method: 'POST'});
            };
            </script>"""
            )
            app = """setTimeout(() => fetch('/fixture/app-authorize', {method: 'POST'})
                      .then(() => el('bank-result').hidden = false), 600);"""
            html = html.replace("APP_AUTHORIZATION", app if self.app_completes else "")
            html = html.replace(
                "RETRY_REVIEW",
                "fetch('/fixture/submit', {method: 'POST', body: JSON.stringify(data)});"
                if self.retry_review else "",
            ).replace(
                "UNEXPECTED_NAVIGATION",
                "location.href = '/fixture/unreviewed';" if self.unexpected_navigation else "",
            )
            reflected = "\n".join(
                f"document.querySelector('.{key}').textContent = el('{key}').value;"
                for key in ("amount", "reference", "tracking")
                if key not in self.review_changes
            )
            html = html.replace("REFLECT_INPUTS", reflected)
        else:
            route.abort()
            return
        route.fulfill(content_type="text/html", body=html)

    def enroll_fixture(self, reference):
        """Synthetic enrollment in a temporary file; never reads the owner's reference."""
        session = self.open_session(url=HLB_ORIGIN + "/", allowed_origins=(HLB_ORIGIN,))
        try:
            result = self.submit_authentication_step(
                session.session_id,
                LoginStep("#idLoginId", ("#picture",), submit_selector="#idBtnSubmit1"),
                CANARIES[0],
                allowed_origins=(HLB_ORIGIN,),
                before_submit=lambda: None,
            )
            assert result.state == LoginState.ADVANCED
            self._pages[session.session_id].locator("#picture").evaluate("img => img.decode()")
            self.enroll_hlb_security_picture(
                session.session_id,
                HLBSecurityPicture("#picture", "#idSBCBConfirmPic"),
                reference=reference,
                guard=lambda: None,
                confirm_owner=lambda: True,
            )
        finally:
            self.close_session(session.session_id)


def build_hlb_demo(path, *, browser=None, submission_enabled=True):
    browser = browser or SimulatedHLBBrowser()
    world = build_demo(path, browser=browser)
    world.source.bill = replace(world.source.bill, destination="TESTBANK:0001234567")
    browser.source = world.source
    reference = path.resolve() / "private/picture.sha256"
    browser.enroll_fixture(reference)
    world.provider = HLBTransferProvider(
        browser,
        fixture_profile(submission_enabled=submission_enabled),
        world.payments.resolver,
        picture_reference=reference,
    )
    world.payments.provider = world.provider
    return world


def main():
    with tempfile.TemporaryDirectory(prefix="wally-hlb-transfer-offline-") as folder:
        path = Path(folder)
        world = build_hlb_demo(path)
        try:
            proposal, callback = prepare_card(world)
            world.telegram.handle_update(callback)
            execution = world.store.list_executions(proposal_id=proposal.id)[0]
            world.telegram.handle_update(callback)
            callback["update_id"] += 1
            world.telegram.handle_update(callback)
            assert execution.status.value == "verified_success"
            assert world.browser.submissions == world.browser.authorizations == 1
            assert world.notion.creates == world.notion.reads == 1
            retained = json.dumps(
                {"messages": world.transport.sent, "execution": asdict(execution)}
            )
            files = [path / "operations.db", *path.glob("audit/*.jsonl")]
            assert not any(
                v in retained or any(v.encode() in f.read_bytes() for f in files) for v in CANARIES
            )
            result = {
                "mode": "offline_hlb_simulation",
                "status": execution.status.value,
                "submissions": world.browser.submissions,
                "bank_app_authorizations_simulated": world.browser.authorizations,
                "independent_history_reads": world.browser.ledger_reads,
                "notion_creates": world.notion.creates,
                "notion_readbacks": world.notion.reads,
                "progress_notified": any(
                    "Bank app" in str(m["text"]) for m in world.transport.sent
                ),
                "replays_submitted_nothing": True,
                "live_bank_requests": 0,
            }
            print(json.dumps(result, indent=2))
            return result
        finally:
            world.provider.close()
            world.browser.shutdown()
            world.notion.client.close()


if __name__ == "__main__":
    main()
