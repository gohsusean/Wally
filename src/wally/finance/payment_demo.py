"""Offline payment acceptance: routed Chromium + fake Telegram + Notion HTTP.

Run with the existing interpreter: python -B -m wally.finance.payment_demo.
No .env, production store, credential store, mailbox or external network is used.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from html import escape
from pathlib import Path
from types import SimpleNamespace

import httpx

from wally.adapters.browser.payments import BrowserPaymentProvider, PaymentBrowserProfile
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.notion.payments import NotionPaymentHistory, PaymentHistoryTarget
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.audit.logger import AuditLogger
from wally.finance.payments import (
    PAYMENT_POLICY,
    PAYMENT_WORKER,
    BillPayment,
    SupervisedPayments,
)
from wally.finance.typical_amount import TypicalAmount
from wally.gateway.service import GatewayRuntime
from wally.models.knowledge import KnowledgeClass
from wally.models.principal import Capability
from wally.ops.store import OperationsStore
from wally.runtime.principals import LOCAL_OPERATOR_CHANNELS, PrincipalAuthority
from wally.runtime.secret_resolver import GovernedSecretsResolver
from wally.telegram.notion_approvals import TelegramEditConfirmation
from wally.telegram.payment_approvals import TelegramPaymentApprovals
from wally.telegram.poll import telegram_registration
from wally.telegram.service import TelegramConfig, TelegramService, telegram_policy

ORIGIN = "https://sandbox.wally.invalid"
USER_REF = "op://Sandbox/Utility/username"
PASS_REF = "op://Sandbox/Utility/password"
TOTP_REF = "op://Sandbox/Utility/one-time password"
CANARIES = ("SYNTHETIC-LOGIN-CANARY", "SYNTHETIC-PASSWORD-CANARY", "483921")


def uid(number):
    return f"00000000-0000-0000-0000-{number:012d}"


class DemoSource:
    def __init__(self):
        self.typical_amount = TypicalAmount()
        self.bill = BillPayment(
            "sandbox-instance",
            uid(10),
            "2027-01",
            "Sandbox electricity utility",
            "SANDBOX-CUSTOMER-001",
            "100.00",
            "MYR",
            "SANDBOX-INV-2027-01",
            "2027-01-01",
            "2027-01-31",
            "a" * 64,
            "b" * 64,
            account_reference="SANDBOX-CUSTOMER-001",
            invoice_account_reference="SANDBOX-CUSTOMER-001",
            account_match_binding="c" * 64,
        )

    def read(self, instance_id):
        if instance_id != self.bill.instance_id:
            raise ValueError("Unknown sandbox instance")
        return self.bill

    def read_typical_amount(self, instance_id):
        self.read(instance_id)
        return self.typical_amount


class SandboxBrowser(PlaywrightBrowserAdapter):
    """All browser HTTP is fulfilled in process; the .invalid origin cannot pay."""

    def __init__(self, source):
        super().__init__(headless=True)
        self.source = source
        self.expiry = time.time() + 600
        self.submissions = 0
        self.ledger_reads = 0

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            # Reuse the installed browser binary with a NEW ephemeral profile.
            # Never install dependencies or attach to the owner's banking session.
            self._browser = self._playwright.chromium.launch(channel="chrome", headless=True)

    def restrict_origins(self, session_id, origins):
        super().restrict_origins(session_id, origins)
        self._pages[session_id].context.route(ORIGIN + "/**", self._route)

    def _route(self, route):
        path = route.request.url.removeprefix(ORIGIN)
        if path == "/submit" and route.request.method == "POST":
            self.submissions += 1
            route.fulfill(status=200, content_type="application/json", body='{"submitted":true}')
            return
        fields = {
            key: getattr(self.source.bill, key)
            for key in ("payee", "destination", "amount", "currency", "reference")
        }
        fields.update(transaction="sandbox-prepared-001", funding="Sandbox saved card ending 1234")
        if path == "/ledger":
            self.ledger_reads += 1
            fields.update(
                receipt="SANDBOX-RECEIPT-001" if self.submissions else "",
                status="settled" if self.submissions == 1 else "pending",
            )
        else:
            fields.update(expires_at=str(self.expiry), duplicate_check="clear", fee="0.00")
        html = "".join(f'<div id="{key}">{escape(value)}</div>' for key, value in fields.items())
        if path != "/ledger":
            html += """<input id="username"><input id="password" type="password">
                <button id="login">Login</button><input id="totp">
                <button id="verify-totp">Verify code</button>
                <div id="authenticated" style="display:none">Signed in</div>
                <button id="pay-once">Pay once</button>
                <script>
                let loggedIn = false;
                const el = id => document.getElementById(id);
                el('login').onclick = () => {
                    loggedIn = el('username').value === 'SYNTHETIC-LOGIN-CANARY'
                        && el('password').value === 'SYNTHETIC-PASSWORD-CANARY';
                };
                el('verify-totp').onclick = () => {
                    if (loggedIn && el('totp').value === '483921') {
                        el('authenticated').style.display = 'block';
                    }
                };
                el('pay-once').onclick = () => {
                    if (el('authenticated').style.display === 'block') {
                        fetch('/submit', {method: 'POST'})
                            .then(() => el('pay-once').textContent = 'Submitted');
                    }
                };
                </script>"""
        route.fulfill(status=200, content_type="text/html", body=html)

    def run_actions(self, session_id, actions):
        result = super().run_actions(session_id, actions)
        if any(a.parameters.get("selector") == "#pay-once" for a in actions):
            self._pages[session_id].wait_for_function(
                "document.getElementById('pay-once').textContent === 'Submitted'"
            )
        return result


class DemoNotion:
    def __init__(self):
        self.target = PaymentHistoryTarget(
            uid(1),
            uid(2),
            "title",
            "account",
            "occurrence",
            "amount",
            "currency",
            "receipt",
            "evidence",
        )
        self.pages = {}
        self.creates = 0
        self.patches = 0
        self.reads = 0
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1", transport=httpx.MockTransport(self.handle)
        )

    def handle(self, request):
        path = request.url.path.removeprefix("/v1")
        if request.method == "GET" and path.startswith("/data_sources/"):
            types = {
                "title": "title",
                "account": "relation",
                "occurrence": "rich_text",
                "amount": "number",
                "currency": "rich_text",
                "receipt": "rich_text",
                "evidence": "rich_text",
            }
            data = {
                "parent": {"database_id": uid(1)},
                "properties": {key: {"id": key, "type": value} for key, value in types.items()},
            }
        elif path.endswith("/query"):
            data = {"results": list(self.pages.values()), "has_more": False}
        elif request.method == "POST" and path == "/pages":
            self.creates += 1
            data = json.loads(request.content)
            data["id"] = uid(100)
            data["properties"] = {
                key: {"id": key, **value} for key, value in data["properties"].items()
            }
            self.pages[data["id"]] = data
        elif request.method == "PATCH" and path.startswith("/pages/"):
            self.patches += 1
            data = self.pages[path.split("/")[-1]]
            for key, value in json.loads(request.content)["properties"].items():
                data["properties"][key] = {"id": key, **value}
        elif request.method == "GET" and path.startswith("/pages/"):
            self.reads += 1
            data = self.pages[path.split("/")[-1]]
        else:
            return httpx.Response(400)
        return httpx.Response(200, json=data)

    def history(self):
        registry = SimpleNamespace(
            find_by_key_or_id=lambda _: SimpleNamespace(
                classification=KnowledgeClass.OPERATIONAL, approved_by="sandbox-owner"
            )
        )
        return NotionPaymentHistory(
            self.target,
            self.client,
            registry,
            write_client=lambda: httpx.Client(
                base_url="https://api.notion.com/v1", transport=httpx.MockTransport(self.handle)
            ),
        )


class DemoTelegram:
    def __init__(self):
        self.sent, self.answers = [], []

    def send_message(self, chat_id, text, buttons=None):
        self.sent.append({"text": text, "buttons": buttons})
        return str(len(self.sent))

    def answer_callback(self, callback_id, text):
        self.answers.append(text)


def build_demo(path, *, browser=None):
    source, notion = DemoSource(), DemoNotion()
    browser = browser or SandboxBrowser(source)
    profile = PaymentBrowserProfile(
        "sandbox",
        "1",
        ORIGIN + "/quote",
        ORIGIN + "/ledger",
        (ORIGIN,),
        USER_REF,
        PASS_REF,
        TOTP_REF,
    )
    provider = BrowserPaymentProvider(browser, profile)
    secrets = MemorySecretsProvider(
        dict(zip((USER_REF, PASS_REF, TOTP_REF + "?attribute=otp"), CANARIES, strict=True))
    )
    confirmation = TelegramEditConfirmation(
        worker_channel=PAYMENT_WORKER, execution_capability=Capability.EXECUTE_PAYMENT
    )
    authority = PrincipalAuthority(
        {**LOCAL_OPERATOR_CHANNELS, "telegram": telegram_policy(), PAYMENT_WORKER: PAYMENT_POLICY},
        human_confirmers={"telegram": confirmation, PAYMENT_WORKER: confirmation},
    )
    store = OperationsStore(path / "operations.db")
    payments = SupervisedPayments(
        store,
        authority,
        source,
        provider,
        notion.history(),
        GovernedSecretsResolver(secrets, audit=AuditLogger(path / "audit")),
        enabled=lambda: True,
    )
    approvals = TelegramPaymentApprovals(payments, confirmation, "42")
    transport = DemoTelegram()
    config = TelegramConfig("synthetic-token", "42", "synthetic-gateway", "42")
    runtime = GatewayRuntime(store, authority, (telegram_registration(config),))
    telegram = TelegramService(runtime, store, config, transport, payment_approvals=approvals)
    telegram.set_dispatch_guard(lambda: True)
    return SimpleNamespace(
        source=source,
        notion=notion,
        browser=browser,
        provider=provider,
        secrets=secrets,
        payments=payments,
        approvals=approvals,
        authority=authority,
        telegram=telegram,
        transport=transport,
        store=store,
    )


def prepare_card(world):
    proposal = world.payments.prepare(
        world.source.bill.instance_id, context=world.authority.issue("cli")
    )
    world.telegram.sync()
    world.telegram.deliver_pending()
    card = next(item for item in world.transport.sent if item["buttons"])
    return proposal, {
        "update_id": 1,
        "callback_query": {
            "id": "sandbox-owner-tap",
            "from": {"id": 42, "is_bot": False},
            "data": card["buttons"][0][0]["callback_data"],
            "message": {"message_id": 1, "date": 1, "chat": {"id": 42, "type": "private"}},
        },
    }


def main():
    with tempfile.TemporaryDirectory(prefix="wally-payment-demo-") as folder:
        world = build_demo(Path(folder))
        try:
            proposal, callback = prepare_card(world)
            world.telegram.handle_update(callback)
            execution = world.store.list_executions(proposal_id=proposal.id)[0]
            world.telegram.handle_update(callback)  # Same update cannot pay again.
            callback["update_id"] = 2
            world.telegram.handle_update(callback)  # New update, same button cannot pay again.
            public = {
                "mode": "offline_simulation",
                "owner_interactions": 1,
                "status": execution.status.value,
                "submissions": world.browser.submissions,
                "independent_ledger_reads": world.browser.ledger_reads,
                "notion_creates": world.notion.creates,
                "notion_readbacks": world.notion.reads,
                "secret_resolutions": len(world.secrets.resolve_calls),
                "result": str(world.approvals.result(proposal.id)),
            }
            retained = json.dumps(
                {"messages": world.transport.sent, "execution": asdict(execution)}
            )
            for value in CANARIES:
                files = [Path(folder) / "operations.db", *Path(folder).glob("audit/*.jsonl")]
                if value in retained or any(value.encode() in p.read_bytes() for p in files):
                    raise RuntimeError("Synthetic credential leaked")
            if (
                public["status"] != "verified_success"
                or public["submissions"] != 1
                or public["notion_creates"] != 1
                or public["notion_readbacks"] != 1
            ):
                raise RuntimeError("Sandbox end-to-end acceptance failed")
            print(json.dumps(public, indent=2))
            return public
        finally:
            world.browser.shutdown()
            world.notion.client.close()


if __name__ == "__main__":
    main()
