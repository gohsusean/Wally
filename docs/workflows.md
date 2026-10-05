# Execution capability registry

`config/workflows.yaml` registers execution capability names/webhook paths. This document describes the **legacy conversational workflow/finance path**, not the operational Act & Verify executor. New privileged capabilities follow [ADR-042](decisions.md#adr-042-new-capabilities-converge-on-the-operational-architecture) and [current architecture](current-architecture.md).

**Current limitations:** Notion structured metadata is not mapped; the legacy bill dictionary can be model-supplied; extra parameters can override verified fields; generic workflow triggering does not enforce the finance verification pipeline. D01/D02/D03 in [engineering debt](engineering-debt.md) record these gaps. No deployed exports are present, and `scripts/deploy_workflows.py` only lists configuration/export presence. Actual n8n effects, authentication and backup behavior are unknown (D08/D09).

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

The router maps the supplied payment method to a registered capability. That does not establish canonical input provenance: current callers can supply bill fields/overrides. A configured webhook or `triggered` result is not independently verified payment completion.

## Provider knowledge fields (bills / providers)

Required design inputs are listed below. Current Notion conversion does not map these properties into metadata; ADR-029 documents the interim model-supplied `bill` fields. Do not describe this table as a completed live schema mapping:

| Field | Used for |
|-------|----------|
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

The legacy intended convention is to use `finance_trigger_payment` for financial checks. It is guidance, not an enforced prohibition on `workflow_trigger`; D02 requires equivalent checks on every reachable financial route. A new consequential capability must not depend on the model choosing the safer tool.
