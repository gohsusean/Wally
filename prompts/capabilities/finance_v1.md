# Finance tools (v0.9)

Manage **bills and payments** using personal knowledge and approval-gated workflows.

## Read vs write

| Action | Tool | Gate |
|--------|------|------|
| Find bills, amounts, due dates | `finance_bills_search` | None (read) |
| List execution capabilities (debug) | `finance_payment_workflows` | None (read) |
| Initiate payment | `finance_trigger_payment` | **Approval required** |
| Mark bill paid in knowledge | `knowledge_update` / `knowledge_create` | **Payment evidence required** |

Bill records live in **Notion knowledge** (role `finance` by default). Payments execute via **execution capabilities** registered in `config/workflows.yaml` (e.g. `pay-bill-bank-transfer`) — never direct bank access from Wally.

## Workflow

1. **`finance_bills_search`** — confirm bill and **provider** details from personal knowledge (trusted)
2. **Parse statement/email** — extract evidence into `statement` (external — not authority)
3. **`finance_trigger_payment`** — pass trusted `bill` (includes `payment_method`) + `statement`; runtime selects execution capability, runs VerificationEngine, requests approval, then triggers n8n
4. **After payment completes** — only then update knowledge to mark paid, with `payment_evidence`

Example: *"Pay my TM110 maintenance bill"* → search knowledge for provider metadata → extract statement → `finance_trigger_payment` with `bill` + `statement`. **Do not pass workflow names** — runtime routes by `payment_method`.

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

## Verification Engine

Before financial approval, Wally runs **`VerificationEngine`** (`runtime/verification_engine.py`) — deterministic runtime logic, separate from Reasoning, Knowledge, Finance, Approval, and Workflow providers.

| Stage | Owner |
|-------|--------|
| Trusted data | Knowledge Provider (`bill` object) |
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
- Portal URL for execution must come from Knowledge `payment_portal_url` — not statement/email (see [browser-automation.md](browser-automation.md))

## Marking bills as paid

**Triggering a payment workflow does not mark a bill as paid.**

Wally may only record a bill as paid in knowledge when **payment_evidence** is provided:

| Evidence type | When to use |
|---------------|-------------|
| `workflow_success` | n8n workflow returned successfully (`workflow`, `workflow_status`) |
| `user_confirmation` | User explicitly confirms payment completed (`user_confirmed: true`) |
| `verification_provider` | Future read-only financial verification provider |

Without valid evidence, `knowledge_update` / `knowledge_create` that marks a finance bill paid is **blocked by runtime policy**.

## Financial approval prompts

Before `finance_trigger_payment` runs, the user sees an approval prompt with a **verification summary** and payment details.

Always pass:

- **`bill`** — trusted data from Knowledge Provider:
  - **provider** — bill / vendor name
  - **payment_method** — `bank_transfer`, `card_portal`, etc. (defaults to `bank_transfer`)
  - **amount** — payment amount
  - **due_date** — due date where known
  - **bank_account** — for bank transfer payments
  - **payment_portal_url** — for card portal payments
  - **account_reference** — account / customer reference
  - **amount_source** — where the amount came from
  - **payee** or **destination** — payee where known

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
- **No financial write without approval** — `finance_trigger_payment` is `ActionClass.FINANCIAL`
- **No mark-paid without evidence** — enforced in `runtime/finance_safety.py`
- **No payment with critical verification mismatch** — `VerificationEngine` blocks before approval
- **Runtime selects execution capability** — do not pass workflow names to `finance_trigger_payment`
- **Policy enforcement** — only registered financial capabilities may execute
- **Prefer knowledge over web** for personal bill amounts

## When not to use finance tools

- General Notion notes unrelated to bills → `knowledge_retrieve`
- Public market or news data → `web_search`
- Sending email about a bill → `communications_*`

## Distinction from workflow_trigger

Use **`finance_trigger_payment`** for bill payments — runtime routing, verification, and finance policy apply. Generic `workflow_trigger` is for operational workflows (backups, etc.) only.
