# Execution capability registry

`config/workflows.yaml` registers **how** Wally executes actions — not **what** is being paid, emailed, or processed.

## Philosophy

| Layer | Responsibility |
|-------|----------------|
| **Knowledge Provider** | Describes the world — providers, payment methods, bank accounts, portal URLs |
| **Runtime** | Routes provider metadata → execution capability → workflow |
| **Verification Engine** | Payment-method-aware evidence checks |
| **Workflow Provider (n8n)** | Executes the capability with provider-specific parameters |

Business logic stays in Wally. n8n workflows should be generic executors.

## Recommended long-term structure

Group capabilities by domain. Each entry maps a **method** to a workflow name and n8n webhook:

```yaml
workflows:
  pay-bill-bank-transfer:
    description: Pay a bill via bank transfer
    webhook_path: pay-bill-bank-transfer
    action_class: financial
    capability:
      domain: payment
      method: bank_transfer

  pay-bill-card-portal:
    description: Pay a bill via card portal
    webhook_path: pay-bill-card-portal
    action_class: financial
    capability:
      domain: payment
      method: card_portal
    # Execution: BrowserAutomationProvider (v0.10) — not n8n business logic

  send-email:
    description: Send an email via communications workflow
    webhook_path: send-email
    action_class: irreversible
    capability:
      domain: communication
      method: send_email
```

Future domains (`document`, `reconciliation`) follow the same pattern.

### Why not nest YAML by domain?

Flat workflow entries with `capability.domain` + `capability.method` keep the n8n adapter simple (one name → one webhook) while allowing the runtime router to index by `(domain, method)`. Nesting can be added later as a documentation view; the loader should stay flat.

## Payment routing flow

```
User: "Pay my TM110 maintenance bill"
  → finance_bills_search (Knowledge)
  → LLM extracts statement evidence
  → finance_trigger_payment(bill={...payment_method, bank_account...}, statement={...})
  → ExecutionCapabilityRouter: payment_method → pay-bill-bank-transfer
  → VerificationEngine (method-specific checks)
  → Approval prompt (verification summary + payment method)
  → bank_transfer: WorkflowProvider (n8n)
  → card_portal: BrowserAutomationProvider (v0.10+)
```

The LLM does **not** choose workflow names for payments.

## Provider knowledge fields (bills / providers)

Store on Knowledge assets (Notion properties → `bill` object):

| Field | Used for |
|-------|----------|
| `payment_method` | Routing (`bank_transfer`, `card_portal`, …) |
| `bank_account` | Bank transfer verification + n8n parameters |
| `payment_portal_url` | Card portal verification + n8n parameters |
| `account_reference` | Portal / reference verification |
| `payment_reference_type` | Future reference formatting |
| `payee`, `provider`, `amount`, `due_date` | Verification + parameters |

Default `payment_method` when omitted: `bank_transfer`.

## Adding a new payment method

1. Add workflow entry with `capability.domain: payment` and `capability.method`.
2. Implement generic n8n workflow (no provider-specific logic).
3. Add verification rules in `VerificationEngine` if checks differ from bank transfer.
4. Store `payment_method` on provider Knowledge assets.

## Non-payment workflows

Operational workflows (`weekly-backup`) omit `capability` metadata. They remain available via `workflow_list` / `workflow_trigger` for explicit user requests.

Financial payments should use `finance_trigger_payment` — not `workflow_trigger` — so routing, verification, and finance policy apply.
