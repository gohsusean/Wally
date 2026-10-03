# Secrets provider

`SecretsProvider` resolves credentials at **execution time**. Secrets never live in Notion, YAML config, prompts, or the audit log.

Wally does not expose a `secrets_resolve` tool to the LLM. The runtime requests secrets only after approval-gated execution (`authorized=True`).

Secrets stay **disabled by default**. Manual portal login remains the fallback.

## 1Password CLI (`op_cli`)

Default adapter. Requires the [1Password CLI](https://developer.1password.com/docs/cli/) and an active session (`op signin`).

Enable in config (`config/macbook.yaml`):

```yaml
providers:
  secrets:
    adapter: op_cli
    enabled: true
  browser:
    adapter: playwright
    enabled: true
    headless: false
```

References on Knowledge Assets must be pointers, not passwords:

```
op://vault/item/field
```

Example fields on a bill or provider record:

| Field | Purpose |
|-------|---------|
| `payment_portal_url` | Trusted portal URL (required for browser) |
| `portal_username_ref` | 1Password ref for portal username |
| `portal_password_ref` | 1Password ref for portal password |
| `login_username_selector` | CSS selector for the username field |
| `login_password_selector` | CSS selector for the password field |
| `login_submit_selector` | Optional submit button selector |
| `auth_success_selector` | CSS selector that appears only after login |
| `auth_success_url_contains` | Optional substring the post-login URL must contain |
| `workflow_secret_refs` | Map of n8n parameter name → `op://` ref |

If refs or selectors are missing, or secrets are disabled, card-portal flows use **manual login** (`WAIT_FOR_USER`).

Default post-login behaviour with `auth_success_selector` or `auth_success_url_contains` is **VERIFY_AUTH** — confirm login and stop. Wally does not click pay, transfer, or purchase controls.

## Runtime rules

1. Access requires `authorized=True` (set only after the approval gate).
2. Only `op://…` references are accepted — raw passwords are rejected.
3. Audit events record the **reference and purpose**, never the secret value.
4. Model-facing tool results, exceptions, and CLI errors must not contain resolved values.
5. Browser automation remains usable without secrets (ADR-030).

Policy: `runtime/secrets_safety.py`. Resolver: `runtime/secret_resolver.py`.

## Telegram bot token (v0.18)

The deployed poller reads `telegram.bot_token_ref` from config, or `WALLY_TELEGRAM_BOT_TOKEN_REF`. The value must be an `op://` pointer. `wally telegram poll` resolves it through `SecretsProvider` at startup and writes a `secrets_resolve` audit event with the reference and purpose `telegram_bot`. The token value is not stored.

`WALLY_TELEGRAM_BOT_TOKEN` remains an injection path for tests and local development when no reference is set. `telegram.owner_user_id` / `WALLY_TELEGRAM_OWNER_USER_ID` is configuration, not a secret.

This startup read is not an execution approval. Proposal execution still requires `authorized=True` after the approval gate.

## Playwright artifacts (privacy default)

Wally does **not** write screenshots, traces, HAR files, or video. `SCREENSHOT` actions are denied. Playwright tracing is not started.

Authenticated pages and filled password fields must not land on disk. Do not enable Playwright inspector tracing around login.

## User-initiated auth-only portal test

This is **not** a CI test. Do not store a real password in any file or command.

### 1. 1Password

Create (or reuse) an item. Note two references, for example:

```
op://Personal/YourPortal/username
op://Personal/YourPortal/password
```

Confirm locally (the CLI prints the secret to your terminal — do not paste it into Wally, Notion, or chat):

```bash
op signin
op read "op://Personal/YourPortal/username"
```

### 2. Knowledge fields

On the approved provider/bill asset, set:

- `payment_portal_url` — exact trusted login URL
- `payment_method` — `card_portal`
- `portal_username_ref` / `portal_password_ref` — the `op://` pointers
- `login_username_selector` / `login_password_selector` / `login_submit_selector`
- `auth_success_selector` — an element visible only when logged in (account menu, dashboard heading). Prefer this over dumping page text.

Do not put the password on the Notion page.

### 3. Config

- `providers.secrets.enabled: true`
- `providers.browser.enabled: true`
- `providers.finance.enabled: true`
- `uv sync --extra browser && playwright install`
- `op signin` in the same environment that runs Wally

### 4. Command

```bash
# From the repository root
uv run wally
```

Ask Wally to **open the trusted portal and verify login only**. Do not ask it to pay a bill.

Example:

```
Open the [provider] payment portal from knowledge and verify that login works. Do not pay anything.
```

### 5. Approval

You should see a CLI approval prompt for a **financial** action (`finance_trigger_payment`). The summary may list `op://` references and the trusted portal URL. It must not show the password.

Type `y` only if the portal URL matches the Knowledge Asset.

### 6. Success

- Browser opens the trusted URL and fills login fields.
- Tool result includes `authenticated: true` and `status: completed`.
- No `page_text` when `VERIFY_AUTH` ran.
- Session closes; Wally does not click a pay control.

If selectors are missing, Wally waits for **manual login** instead (`waiting_for_user: true`). Complete login in the window, then you may cancel rather than resume if you only wanted an auth check.

### 7. Confirm no secret leak

```bash
rg -F 'your-password-here' data/audit
```

Do not put the real password in the repo. Search the audit JSONL for the **value** you know from 1Password. You should find **only** the `op://` reference in `secrets_resolve` events (`reference` + `purpose`), never the value.

Also check the Wally reply and `/health` output.

## Related docs

- [roadmap.md](roadmap.md) — v0.11 / v0.11.1
- ADR-032 and ADR-033 in [decisions.md](decisions.md)
- [browser-automation.md](browser-automation.md)
