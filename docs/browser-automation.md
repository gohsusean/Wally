# Browser automation

`BrowserAutomationProvider` executes deterministic browser actions after runtime governance. Business logic stays in Wally; Playwright is an adapter only.

## Trusted portal URL rule (mandatory)

**Browser automation may only navigate to portal URLs retrieved from approved Knowledge Assets.**

Navigation targets must come from trusted fields on the Knowledge record (e.g. `payment_portal_url` on a provider/bill asset). The runtime enforces this **before** calling the Playwright adapter.

### Never use as navigation targets

| Source | Why |
|--------|-----|
| Email body / links | Untrusted external content |
| Fetched web pages | Low-trust (`WebProvider`) |
| PDFs / attachments | Untrusted external content |
| User-provided unverified text | Not an approved Knowledge Asset |
| LLM-generated URLs | Reasoning output is not authority |

Statement/email evidence may be used for **verification** (compare against Knowledge) — not for choosing where the browser goes.

### Missing trusted URL

If `payment_portal_url` (or equivalent) is **not** on the Knowledge Asset:

1. **Stop** — do not open a browser session
2. Tell the user to **add and approve** the portal URL in knowledge first
3. Re-run only after the trusted URL exists on the Knowledge Asset

Policy: `runtime/browser_safety.py` → `evaluate_trusted_portal_navigation`, `resolve_trusted_portal_url`.

### Playwright adapter requirements (v0.10)

Before `open_session` or any `NAVIGATE` action, the adapter (or runtime wrapper) must:

1. Call `resolve_trusted_portal_url(knowledge)` or `evaluate_trusted_portal_navigation`
2. Use **only** the returned trusted URL for the initial session
3. Call `evaluate_browser_session_actions` for action batches containing `NAVIGATE`

### Resume after manual login

When `WAIT_FOR_USER` is returned (manual auth — no secrets or missing refs):

1. Complete login in the browser window.
2. Call **`finance_browser_resume`** with the `session_id` to run post-login steps in the **same** session (e.g. `READ_PAGE`).
3. Or call **`finance_browser_cancel`** to close without completing.

Sessions expire after `providers.browser.session_timeout_seconds` (default 3600). Expired or cancelled sessions are closed cleanly.

Policy: `runtime/browser_executor.py` — `resume_card_portal_session`, `cancel_card_portal_session`.

## Related docs

- [architecture.md](architecture.md) — Browser Automation Provider
- [roadmap.md](roadmap.md) — v0.10 milestone
- [secrets.md](secrets.md) — 1Password CLI, credential injection, auth-only runbook
- ADR-030, ADR-031, ADR-033 in [decisions.md](decisions.md)
