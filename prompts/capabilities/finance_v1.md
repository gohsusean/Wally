# Finance tools (v0.9)

Manage **bills and payments** using personal knowledge and approval-gated workflows.

## Read vs write

| Action | Tool | Gate |
|--------|------|------|
| Find bills, amounts, due dates | `finance_bills_search` | None (read) |
| List execution capabilities (debug) | `finance_payment_workflows` | None (read) |
| Initiate payment | `finance_trigger_payment` | **Authenticated runtime authority + fresh approval required** |
| Write finance records / mark paid | `knowledge_update` / `knowledge_create` | **Authenticated independent human verification required** |

Bill records live in **Notion knowledge** (role `finance` by default). Payments execute via **execution capabilities** registered in `config/workflows.yaml` (e.g. `pay-bill-bank-transfer`) — never direct bank access from Wally.

## Workflow

1. **`finance_bills_search`** — find the approved bill's **asset ID**
2. **Parse statement/email** — extract evidence into `statement` (external — not authority)
3. **`finance_trigger_payment`** — pass `bill: {"asset_id": "..."}` + `statement`; runtime fetches canonical finance metadata, selects the capability, verifies the exact payload and requests fresh approval. It re-fetches/reverifies after approval and refuses drift.
4. **Financial state writes** — request independent human verification through the runtime. Tool payload claims are never completion evidence. Workflow acceptance or portal login cannot establish payment completion.

Example: *"Pay my TM110 maintenance bill"* → search knowledge for the bill's asset ID → extract statement → `finance_trigger_payment` with that ID + `statement`. **Do not pass workflow names or execution overrides** — runtime routes by canonical `payment_method`. If canonical metadata is missing, report that payment is unavailable; do not reconstruct it from prose.

## Execution capability routing

| Step | Owner |
|------|--------|
| Provider + payment method | Knowledge Provider (`bill.payment_method`, `bank_account`, `payment_portal_url`, …) |
| Capability selection | `ExecutionCapabilityRouter` (runtime) |
| Verification | `VerificationEngine` (payment-method-aware) |
| Approval | Approval Engine |
| Execution | Workflow Provider → n8n |

**Payment methods:** `bank_transfer` (default), `card_portal`, `api`, `direct_debit` (future).

Provider records should include:

- `payment_method` — how this provider is paid
- `bank_account` — for bank transfer
- `payment_portal_url` — for card portal
- `account_reference`, `payment_reference_type` — reference fields
- `payee`, `provider`, `amount`, `due_date` — verification + parameters
- `currency` — required three-letter code bound to the amount

These fields must exist in approved operational finance metadata. Model-supplied fields cannot replace missing canonical metadata; repeated fields must match exactly. Current Notion metadata mapping remains a deployment gap.

## Verification Engine

Before financial approval, Wally runs **`VerificationEngine`** (`runtime/verification_engine.py`) — deterministic runtime logic, separate from Reasoning, Knowledge, Finance, Approval, and Workflow providers.

| Stage | Owner |
|-------|--------|
| Trusted data | Knowledge Provider (runtime fetch by `bill.asset_id`) |
| External evidence | Statement/email (`statement` object — LLM may extract fields) |
| Comparison | VerificationEngine (runtime) |
| Gate | Approval Engine (summary includes verification) |
| Execution | Workflow Provider (n8n) after approval |

**Statuses:** `verified`, `missing`, `mismatch`, `unknown`.

**Bank account rule:**

- Statement includes account → compare (after normalizing digits) to Knowledge Asset `bank_account`
  - Match → verified
  - Mismatch → **payment blocked**
- Statement omits account but Knowledge has verified account → proceed with warning: *not shown on statement; using verified account from Knowledge Asset*
- No verified account in Knowledge → **payment blocked**

Normalize bank accounts by removing spaces, hyphens, dots, slashes, and other non-digits before comparing.

**Card portal** (when `payment_method: card_portal`, v0.10+):

- Verify payment portal URL, provider, amount, account reference
- Bank account verification is **not** required
- Execution via `BrowserAutomationProvider` (manual authentication initially)
- After `finance_trigger_payment` returns `waiting_for_user`, complete login in the browser, then call **`finance_browser_resume`** with the `session_id`
- Use **`finance_browser_cancel`** to close a pending session without paying
- Portal URL for execution must come from canonical Knowledge `payment_portal_url` — not statement/email (see [browser-automation.md](../../docs/browser-automation.md))
- Current portal scripts log in/read/verify only. `authenticated` / `completed` refers to that script, not a completed payment.

## Marking bills as paid

**Triggering a payment workflow does not mark a bill as paid.**

All finance creates/updates require an authenticated runtime verify capability and a fresh human prompt to independently check the exact record/write. If the write claims payment occurred, the person must personally verify payment. The runtime binds that confirmation to the target/write fingerprint and provenance, then rechecks the current record before writing.

Do not supply `payment_evidence`, `user_confirmed`, workflow status or provider/source names as proof. Payload evidence is ignored; conversational assertions alone cannot authorize a write. There is no automated trusted payment-completion provider in this path. A dispatch or login result leaves paid state and Matters unchanged.

## Financial approval prompts

Before `finance_trigger_payment` runs, the user sees an approval prompt with a **verification summary** and payment details.

Always pass:

- **`bill`** — `asset_id` of the approved operational finance record. The runtime obtains provider, payment method, amount/currency, account/bill identity and destination from that record; model fields are exact assertions only.

- **`statement`** — external evidence from bill statement or email (not authoritative):
  - **payee**, **amount**, **due_date**, **bank_account**, **source**

Example verification lines:

```
Verification:
  Amount: RM356.40 — verified against statement
  Payee: TM110 Management Corporation — matches Knowledge Asset
  Bank account: 123456789 — matches Knowledge Asset
```

If the statement omits a bank account but Knowledge has one:

```
  Bank account: not shown on statement; using verified account from Knowledge Asset (123456789)
```

Use `unknown` only when a field is genuinely unavailable.

## Security rules

- **No secrets in Notion** — store `op://vault/item/field` pointers only; values come from `SecretsProvider` at execution time
- **Card portal execution** — `card_portal` uses Browser Automation; automatic login when secret refs + selectors exist, otherwise manual login
- **No financial dispatch without authenticated authority and fresh approval** — the reviewed canonical payload/target must remain unchanged
- **No finance write without independent authenticated human verification** — tool payload evidence grants nothing
- **No payment with critical verification mismatch** — `VerificationEngine` blocks before approval
- **Runtime selects execution capability** — do not pass workflow names to `finance_trigger_payment`
- **Policy enforcement** — only registered financial capabilities may execute
- **Prefer knowledge over web** for personal bill amounts
- **Uncertain outcomes require review** — do not automatically retry a failed/ambiguous dispatch

## When not to use finance tools

- General Notion notes unrelated to bills → `knowledge_retrieve`
- Public market or news data → `web_search`
- Sending email about a bill → `communications_*`

## Distinction from workflow_trigger

Use **`finance_trigger_payment`** for bill payments — runtime routing, verification, and finance policy apply. Generic `workflow_trigger` is for operational workflows (backups, etc.) only.
