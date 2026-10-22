"""Offline HLB picture/login acceptance in real Chrome; all requests are fulfilled locally.

Run: python -B -m wally.runtime.hlb_authentication_demo
Only synthetic gradients/credentials are used. No screenshots or real bank calls.
"""

import json
import struct
import tempfile
import zlib
from pathlib import Path

from wally.adapters.browser.authentication import LoginState, LoginStep
from wally.adapters.browser.hlb import HLB_ORIGIN, HLBSecurityPicture
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.audit.logger import AuditLogger
from wally.runtime.secret_resolver import GovernedSecretsResolver

HTML = """<!doctype html><form id="user-form"><input id="username"><button>Next</button></form>
<div id="picture-step" hidden><img id="picture" src="/picture.png">
<input id="confirm" type="checkbox"><form id="password-form">
<input id="password" type="password" readonly><button>Login</button></form></div>
<button id="logout" hidden>Logout</button>
<script>
const el = id => document.getElementById(id);
el('user-form').onsubmit = e => { e.preventDefault();
  el('user-form').hidden = true; el('picture-step').hidden = false; };
el('password').onclick = () => { if (el('confirm').checked) el('password').readOnly = false; };
el('password-form').onsubmit = e => { e.preventDefault();
  if (el('password').value === 'SYNTHETIC-PASSWORD') {
    el('picture-step').hidden = true; el('logout').hidden = false;
  }
};
</script>"""


def png():
    def chunk(kind, data):
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    rows = b"".join(
        b"\0" + bytes(v for x in range(32) for v in (x * 7, y * 7, 90)) for y in range(32)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class OfflineHLBBrowser(PlaywrightBrowserAdapter):
    def __init__(self):
        super().__init__(headless=True, channel="chrome")

    def restrict_origins(self, session_id, origins):
        super().restrict_origins(session_id, origins)

        def fulfill(route):
            if route.request.url.endswith("/picture.png"):
                route.fulfill(content_type="image/png", body=png())
            else:
                route.fulfill(content_type="text/html", body=HTML)

        # Every request is fulfilled or aborted; the official URL is a routed
        # fixture to exercise the exact-origin check, not a TLS/bank acceptance.
        self._pages[session_id].context.route("**/*", fulfill)


def main():
    with tempfile.TemporaryDirectory(prefix="wally-hlb-offline-") as folder:
        reference = Path(folder).resolve() / "private/picture.sha256"
        refs = ("op://Sandbox/HLB/username", "op://Sandbox/HLB/password")
        resolver = GovernedSecretsResolver(
            MemorySecretsProvider(
                dict(zip(refs, ("SYNTHETIC-USER", "SYNTHETIC-PASSWORD"), strict=True))
            ),
            audit=AuditLogger(Path(folder) / "audit"),
        )
        values = resolver.resolve_many(refs, authorized=True, purpose="offline_hlb_auth")
        browser = OfflineHLBBrowser()
        picture = HLBSecurityPicture("#picture", "#confirm")
        password_step = LoginStep("#password", ("#logout",), timeout_ms=1000)
        try:
            for case in ("match", "mismatch", "missing", "ambiguous", "unloaded", "cross_origin"):
                session = browser.open_session(url=HLB_ORIGIN + "/", allowed_origins=(HLB_ORIGIN,))
                result = browser.submit_authentication_step(
                    session.session_id,
                    LoginStep("#username", ("#picture",)),
                    values[0],
                    allowed_origins=(HLB_ORIGIN,),
                    before_submit=lambda: None,
                )
                assert result.state == LoginState.ADVANCED
                page = browser._pages[session.session_id]
                page.locator("#picture").evaluate("img => img.decode()")
                if case == "match":
                    browser.enroll_hlb_security_picture(
                        session.session_id,
                        picture,
                        reference=reference,
                        guard=lambda: None,
                        confirm_owner=lambda: True,
                    )
                elif case == "mismatch":
                    reference.write_text("0" * 64 + "\n")
                elif case == "missing":
                    page.locator("#picture").evaluate("img => img.remove()")
                elif case == "ambiguous":
                    page.locator("#picture").evaluate("img => img.after(img.cloneNode())")
                elif case == "unloaded":
                    page.locator("#picture").evaluate("img => img.removeAttribute('src')")
                else:
                    page.locator("#picture").evaluate(
                        "img => img.src = 'https://attacker.invalid/picture.png'"
                    )
                if case != "mismatch":
                    # Restore the enrolled fingerprint without re-learning any fixture.
                    if case == "match":
                        enrolled = reference.read_bytes()
                    else:
                        reference.write_bytes(enrolled)
                result = browser.submit_hlb_password(
                    session.session_id,
                    picture,
                    password_step,
                    values[1],
                    reference=reference,
                    guard=lambda: None,
                )
                if case == "match":
                    assert result.state == LoginState.ADVANCED
                    assert page.locator("#logout").is_visible()
                else:
                    assert result.state == LoginState.FAILED
                    assert page.locator("#password").input_value() == ""
                    assert not page.locator("#confirm").is_checked()
                browser.close_session(session.session_id)
            records = "".join(p.read_text() for p in Path(folder).rglob("*.jsonl"))
            assert not any(value in records for value in values)
            assert set(p.suffix for p in Path(folder).rglob("*") if p.is_file()) <= {
                ".sha256",
                ".jsonl",
            }
            print(
                json.dumps(
                    {
                        "matching_login": True,
                        "mismatch_missing_ambiguity_stops": True,
                        "unloaded_cross_origin_stops": True,
                        "image_files_written": False,
                        "live_network_or_payment": False,
                    }
                )
            )
        finally:
            values = ()
            browser.shutdown()


if __name__ == "__main__":
    main()
