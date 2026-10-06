# Execution capability registry

`config/workflows.yaml` registers execution capability names/webhook paths. This document describes the **legacy conversational workflow/finance path**, not the operational Act & Verify executor. New privileged capabilities follow [ADR-042](decisions.md#adr-042-new-capabilities-converge-on-the-operational-architecture) and [current architecture](current-architecture.md).

**Current boundary:** [ADR-043](decisions.md#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence) resolves D01/D02: payment inputs come from approved canonical finance metadata, caller overrides fail closed, and generic financial workflow calls are blocked. Financial state writes require authenticated, independent human verification. Notion structured metadata remains unmapped (D03), so the payment path cannot fall back to model-supplied fields. No deployed exports are present, and `scripts/deploy_workflows.py` only lists configuration/export presence. Actual n8n effects, authentication and backup behavior are unknown (D08/D09).

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
  → finance_trigger_payment(bill={asset_id: ...}, statement={...})
  → authenticated execute capability + canonical finance asset re-fetch
  → ExecutionCapabilityRouter: payment_method → pay-bill-bank-transfer
  → VerificationEngine (method-specific checks)
  → Fresh approval prompt (verification + exact payload + execution target)
  → Re-fetch/reverify canonical inputs and require the reviewed fingerprint
  → bank_transfer: WorkflowProvider (n8n)
  → card_portal: BrowserAutomationProvider (v0.10+)
```

The router maps the canonical payment method to a registered financial capability. Repeated caller fields must match exactly; unknown/conflicting parameters are refused. Credential injection may populate credential slots only. A configured webhook, `triggered` result or successful portal login is not payment-completion evidence and cannot mark paid or resolve a Matter.

## Provider knowledge fields (bills / providers)

Canonical inputs are listed below. Current Notion conversion does not map these properties into metadata; ADR-043 supersedes ADR-029's interim model-supplied authority. Missing canonical inputs fail closed. This table is not a completed live schema mapping:

| Field | Used for |
|-------|----------|
| `asset_id` | Exact approved finance asset fetched by runtime |
| `currency` | Required currency code, bound to the amount |
| `payment_method` | Routing (`bank_transfer`, `card_portal`, …) |
| `bank_account` | Bank transfer verification + n8n parameters |
| `payment_portal_url` | Card portal verification + n8n parameters |
| `account_reference` | Portal / reference verification |
| `payment_reference_type` | Future reference formatting |
| `payee`, `provider`, `amount`, `due_date` | Verification + parameters |

Default `payment_method` when omitted: `bank_transfer`.

## New payment/action development

The old pattern of adding a model-callable workflow is not the preferred extension point. Define canonical typed inputs, proposal intent/version binding, capabilities, execution-time authorization, verification and uncertain-outcome handling through the operational architecture first. An approved design may then reuse a provider/execution backend; a registry entry alone cannot supply those boundaries. No new payment executor is authorized or implemented by this documentation.

## Non-payment workflows

Operational workflows (`weekly-backup`) omit `capability` metadata. They remain available via `workflow_list` / `workflow_trigger` for explicit user requests.

`workflow_trigger` refuses financial and payment-tagged workflows, including aliases. Bill payments require the authenticated canonical finance path. A new consequential capability must not depend on the model choosing the safer tool.
