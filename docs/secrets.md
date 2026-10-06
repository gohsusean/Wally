# Secrets provider

`SecretsProvider` resolves action credentials after execution authorization. Telegram separately resolves bot authentication at startup. Secret values must not live in Notion, tracked YAML, prompts, databases or logs; environment/ignored `.env` and credential stores remain local secret sources. See [operations](operations.md) for their lifecycle.

Wally does not expose a `secrets_resolve` tool to the LLM. The runtime requests secrets only after approval-gated execution (`authorized=True`).

The tracked MacBook profile enables secrets/browser providers; inspect the selected profile rather than assuming a disabled default. Legacy card-portal flows may fall back to manual login when refs are absent; canonical Act & Verify requires its configured refs/success conditions and fails preflight when missing. Notion metadata mapping remains unresolved (D03).

## 1Password CLI (`op_cli`)

The configured `op_cli` factory dispatches `op://` to [1Password CLI](https://developer.1password.com/docs/cli/) and `keychain://` to macOS Keychain. Interactive 1Password resolution needs CLI/app authorization; the deployed Telegram Keychain read does not depend on a terminal `OP_SESSION`.

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

1. Action credential access requires `authorized=True` after the execution approval gate. Telegram startup authentication and explicit credential lifecycle commands are separate documented purposes, not action approval.
2. Only `op://…` and `keychain://service/account` references are accepted — raw passwords are rejected.
3. Audit events record the **reference and purpose**, never the secret value.
4. Model-facing tool results, exceptions, and CLI errors must not contain resolved values.
5. Browser automation remains usable without secrets (ADR-030).

Policy: `runtime/secrets_safety.py`. Resolver: `runtime/secret_resolver.py`.

## Telegram bot token (v0.18)

The deployed poller reads `telegram.bot_token_ref`. On this Mac that pointer is `keychain://com.wally.telegram/bot-token`, a generic password in the login keychain. `wally telegram poll` resolves it through `SecretsProvider` at startup and writes a `secrets_resolve` audit event with the reference and purpose `telegram_bot`. The token value is not stored.

`telegram.bot_token_source_ref` stays the 1Password item, `op://Private/Wally Telegram Bot/password`. `wally telegram credential install` reads that item with the 1Password provider and writes the value into the keychain item. The command prints success or failure only. `wally telegram credential check` resolves the keychain item the same way and does not print it. `wally telegram credential remove` deletes the keychain copy and leaves the 1Password item. `wally telegram uninstall` removes the LaunchAgent and leaves the keychain item.

The keychain access list trusts the Wally virtualenv's Python executable. macOS binds that trust to the binary's code signature, which is uv's ad-hoc-signed CPython, so another script run with that same interpreter can also read the item while the login keychain is unlocked. `/usr/bin/security` is not on the list. A different binary is prompted, and the LaunchAgent cannot answer a prompt. After the Python binary changes, run `credential install` again. Rotation is: update the token, update 1Password, run `credential install`, restart the poller.

`WALLY_TELEGRAM_BOT_TOKEN` remains an injection path for tests and local development when no reference is set. `telegram.owner_user_id` / `WALLY_TELEGRAM_OWNER_USER_ID` is configuration, not a secret.

This startup read is not an execution approval. Proposal execution still requires `authorized=True` after the approval gate. The 1Password provider remains the interactive path for `op://` references.

## Playwright artifacts (privacy default)

Wally does **not** write screenshots, traces, HAR files, or video. `SCREENSHOT` actions are denied. Playwright tracing is not started.

Authenticated pages and filled password fields must not land on disk. Do not enable Playwright inspector tracing around login.

## User-initiated auth-only portal test

This is **not** a CI test and must be separately operator-authorized. Do not store a real password in any file or command. This legacy runbook requires an approved canonical finance asset with mapped metadata, including amount/currency and portal fields. Under [ADR-043](decisions.md#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence), callers supply its `asset_id`; a model bill dictionary cannot substitute for missing metadata. The current Notion adapter does not populate those fields (D03), so this procedure currently fails closed. It is not a validated live Notion or Act & Verify acceptance procedure.

### 1. 1Password

Create (or reuse) an item. Note two references, for example:

```
op://Personal/YourPortal/username
op://Personal/YourPortal/password
```

Authorize the CLI/app locally without displaying credential values. Do not use `op read` output as handover evidence; the runtime resolver and canary-secret tests cover the mechanism without printing real values.

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

Use existing synthetic canary-secret tests to verify that records/results/audit omit values and retain only references/purpose. For an explicitly authorized live leak review, compare values privately in memory and report aggregate matches only. Never put a real secret in `rg` arguments, shell history, terminal output or a transcript. `/health` can contact providers; it is not a default read-only audit command.

## Related docs

- [roadmap.md](roadmap.md) — v0.11 / v0.11.1
- ADR-032 and ADR-033 in [decisions.md](decisions.md)
- [browser-automation.md](browser-automation.md)
