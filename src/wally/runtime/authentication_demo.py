"""Offline, operator-run Playwright/Chrome auth acceptance; no .env or live provider.

Run: python -B -m wally.runtime.authentication_demo
Uses routed .invalid HTML and synthetic credentials, never a bank or payment UI.
"""

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from wally.adapters.browser.authentication import LoginState, LoginStep
from wally.adapters.browser.payments import BrowserPaymentProvider, PaymentBrowserProfile
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.audit.logger import AuditLogger
from wally.runtime.secret_resolver import GovernedSecretsResolver

ORIGIN = "https://auth.sandbox.wally.invalid"
REFS = ("op://Sandbox/Login/username", "op://Sandbox/Login/password")
VALUES = ("SYNTHETIC-USER-CANARY", "SYNTHETIC-PASSWORD-CANARY")
HTML = """<!doctype html><form id="user-form"><input id="username">
<button>Continue</button></form>
<form id="password-form" hidden><input id="password" type="password">
<button>Login</button></form><button id="logout" hidden>Logout</button>
<script>
const el = id => document.getElementById(id);
el('user-form').onsubmit = event => {
  event.preventDefault();
  if (el('username').value === 'SYNTHETIC-USER-CANARY') {
    el('user-form').hidden = true; el('password-form').hidden = false;
  }
};
el('password-form').onsubmit = event => {
  event.preventDefault();
  if (el('password').value === 'SYNTHETIC-PASSWORD-CANARY') {
    el('password-form').hidden = true; el('logout').hidden = false;
  }
};
</script>"""


class RoutedAuthenticationBrowser(PlaywrightBrowserAdapter):
    def __init__(self):
        super().__init__(headless=True, channel="chrome")

    def restrict_origins(self, session_id, origins):
        super().restrict_origins(session_id, origins)
        self._pages[session_id].context.route(
            ORIGIN + "/**", lambda route: route.fulfill(content_type="text/html", body=HTML)
        )


def main():
    with tempfile.TemporaryDirectory(prefix="wally-auth-offline-") as folder:
        audit = AuditLogger(Path(folder))
        resolver = GovernedSecretsResolver(
            MemorySecretsProvider(dict(zip(REFS, VALUES, strict=True))), audit=audit
        )
        values = resolver.resolve_many(REFS, authorized=True, purpose="offline_auth_demo")
        browser = RoutedAuthenticationBrowser()
        try:
            first = browser.open_session(url=ORIGIN + "/login", allowed_origins=(ORIGIN,))
            username = browser.submit_authentication_step(
                first.session_id,
                LoginStep("#username", ("#password",)),
                values[0],
                allowed_origins=(ORIGIN,),
                before_submit=lambda: None,
            )
            password = browser.submit_authentication_step(
                first.session_id,
                LoginStep("#password", ("#logout",)),
                values[1],
                allowed_origins=(ORIGIN,),
                before_submit=lambda: None,
            )
            assert username.state == password.state == LoginState.ADVANCED
            assert browser.has_selector(first.session_id, "#logout")
            second = browser.open_session(url=ORIGIN + "/login", allowed_origins=(ORIGIN,))
            assert (
                browser._pages[first.session_id].context
                is not browser._pages[second.session_id].context
            )
            assert not browser.has_selector(second.session_id, "#logout")
            challenge = ("#mandatory-bank-challenge",)
            profile = PaymentBrowserProfile(
                "offline-staged-auth",
                "1",
                ORIGIN + "/login",
                ORIGIN + "/history",
                (ORIGIN,),
                *REFS,
                username_step=LoginStep("#username", ("#password",), challenge_selectors=challenge),
                password_step=LoginStep("#password", ("#logout",), challenge_selectors=challenge),
                authenticated_selector="#logout",
            )
            provider = BrowserPaymentProvider(browser, profile)
            _ = profile.fingerprint
            provider.authenticate(resolver, guard=lambda: None, session_id="offline-auth")
            assert browser.has_selector(provider._session.session_id, "#logout")
            provider.close()  # Authenticate only; prepare/submit/verify are never called.
            browser.close_session(first.session_id)
            browser.close_session(second.session_id)
            report = {
                "engine": "playwright_chrome_stable",
                "username": asdict(username),
                "password": asdict(password),
                "authenticated": True,
                "isolated_contexts": True,
                "payment_driver_authentication": True,
                "live_network_or_payment": False,
            }
            records = "".join(path.read_text() for path in Path(folder).glob("*.jsonl"))
            assert all(ref in records for ref in REFS)
            assert not any(value in records + json.dumps(report) for value in VALUES)
            print(json.dumps(report))
        finally:
            values = ()
            browser.shutdown()


if __name__ == "__main__":
    main()
