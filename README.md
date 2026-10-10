# Wally

Wally is your personal **Chief of Staff** and **AI operating system** — an intelligent orchestrator that reasons over knowledge, manages communications, runs workflows, and supports decisions across your digital life.

Wally is not a chatbot. It is not a home automation platform.

## Status

**Status:** **v0.18.0 — Telegram inbox.** Telegram long-polls from this Mac and reaches Wally through the Gateway. The owner is one numeric Telegram user id in a private chat. Approval buttons carry a server-side nonce. `Not now` dismisses that card and leaves the proposal pending. Approve records a decision and does not execute. ChatGPT v0.17 stays implemented, with `record_decision` disabled in code until hosted action-specific human confirmation is implemented. Act & Verify supports bill portal login/review only, on a separate authorized request, and never pays. The older conversational ToolRegistry path remains callable: financial dispatch now requires authenticated runtime authority, canonical finance metadata and fresh human approval under ADR-043. The Act & Verify limit is not a system-wide no-payment guarantee. Nothing runs on a schedule.

Complete: v0.10 browser automation, v0.11.1 secrets hardening, v0.12 Observe & Brief, v0.13 Assess & Propose, v0.14 Approval Inbox, v0.15 Act & Verify, v0.16 Gateway, v0.17 ChatGPT interface, v0.18 Telegram inbox.

Future: scheduling and proactive triggers, notification UX hardening, more supported action types, and a validated hosted ChatGPT connection. Current service implementations are not proof of live provider readiness; D03 now supplies a typed certified financial catalog with local-owner certification under [ADR-044](docs/decisions.md#adr-044-certified-financial-identities-and-local-owner-certification). Live chain validation and other acceptance gaps remain in [engineering debt](docs/engineering-debt.md). Remote execution from ChatGPT is not part of this release.

The [interface-neutral approval slice](docs/interface-neutral-approvals.md) adds a
restricted local Codex stdio adapter, exact scoped batches, a native biometric
confirmation provider and independently verified Notion metadata edits. It ships
with no edit targets, no configured confirmer and writes disabled. Synthetic tests
and Swift type-checking do not establish installed/live readiness. ChatGPT cannot
approve merely through a bearer, pinned subject or the old confirmation flag.

## Engineering handover

Read [AGENTS.md](AGENTS.md), [current architecture](docs/current-architecture.md), [runtime operations](docs/operations.md), the relevant [ADRs](docs/decisions.md), and affected tests. Check [active engineering debt](docs/engineering-debt.md) before feature work; [roadmap](docs/roadmap.md) owns future delivery.

**New capabilities should converge on the operational proposal/capability/provenance architecture, rather than add privileged behavior through the legacy conversational tools.** [ADR-042](docs/decisions.md#adr-042-new-capabilities-converge-on-the-operational-architecture) records this direction; [ADR-043](docs/decisions.md#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence) records the focused legacy finance stabilization.

## Getting started

```bash
# From the repository root
uv pip install -e ".[dev]"   # skip uv venv if .venv already exists

# Secrets in .env (auto-loaded):
# OPENAI_API_KEY=sk-...
# NOTION_API_KEY=secret_...

# If upgrading from a full config/notion.yaml databases: block:
uv run python scripts/migrate_notion_yaml.py

uv run wally
```

### CLI commands

| Command | Action |
|---------|--------|
| `/help` | Show available commands |
| `/new` | Start a new session |
| `/health` | Check provider status |
| `/sessions` | List recent session IDs |
| `/knowledge list` | List registered databases |
| `/knowledge pending` | Show databases awaiting classification |
| `/knowledge review <id>` | Show classification recommendation |
| `/knowledge approve <id> operational\|governance` | Approve database classification |
| `/brief` | Generate an operational brief, including decisions waiting for you |
| `/approvals` | Show the Approval Inbox |
| `/approve <id>` | Approve a pending proposal. Does not execute it |
| `/reject <id>` | Reject a pending proposal |
| `/defer <id> --until <date>` | Hide a proposal until a date |
| `/execute <proposal-id>` | Act on an approved, supported proposal. Asks again before anything runs |
| `/executions` | List execution attempts |
| `/execution <execution-id>` | Show one attempt |
| `/verify <execution-id> [--confirm success\|failure]` | Re-check stored evidence (read-only), or record your own check |
| `/exit` | Quit |

One-shot brief (no REPL):

```bash
uv run wally brief
uv run wally brief --json --no-refresh
uv run wally approvals
uv run wally approvals --json
uv run wally approve <proposal-id>
uv run wally reject <proposal-id>
uv run wally defer <proposal-id> --until 2026-10-03
uv run wally execute <proposal-id>
uv run wally executions
uv run wally execution <execution-id>
uv run wally verify <execution-id>
```

See [docs/chief-of-staff.md](docs/chief-of-staff.md).

### Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required for reasoning |
| `NOTION_API_KEY` | Required for knowledge (v0.3+) |
| `N8N_WEBHOOK_BASE_URL` | Required for workflows (v0.5+) — e.g. `https://n8n.example.com/webhook/` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID (v0.6+) |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret (v0.6+) |
| `GOOGLE_REFRESH_TOKEN` | Google OAuth refresh token — run `scripts/google_auth.py` once |
| `WALLY_CONFIG` | Config profile (`macbook` or `macmini`) |
| `WALLY_DRY_RUN` | `true` to log writes without executing (v0.5+) |
| `WALLY_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Resume a previous session:

```bash
uv run wally --session <session-id>
```

### Notion setup

1. Create an [internal integration](https://www.notion.so/my-integrations) in Notion.
2. Add `NOTION_API_KEY` to `.env`.
3. Share databases with the integration (⋯ → Connections).
4. Start Wally — databases are auto-discovered into the **knowledge registry**.
5. Approve new databases before Wally can write to them:

```
/knowledge pending
/knowledge review tasks
/knowledge approve tasks operational
```

`config/notion.yaml` holds platform defaults and schema overrides only — not classifications.

```yaml
defaults:
  type_property: "Type of Knowledge Asset"

exclude: []

overrides:
  "your-database-id":
    title_property: Task Name
    content_property: Notes
```

Legacy `databases:` blocks are imported once via `scripts/migrate_notion_yaml.py`.

### Workflow setup (v0.5)

1. Create webhook workflows in n8n matching paths in `config/workflows.yaml`.
2. Add `N8N_WEBHOOK_BASE_URL` to `.env`.
3. Enable workflows in `config/macbook.yaml`: `providers.workflow.enabled: true`
4. List configured workflows: `uv run python scripts/deploy_workflows.py`

The registry can route "Run the weekly backup" to a `weekly-backup` webhook, but no exports or proven backup procedure are in the repo. The helper lists configuration; it does not deploy. Actual downstream behavior must be verified separately. Legacy conversational bill-payment routing now binds canonical inputs and authenticated human review (D01/D02 resolved); missing live metadata fails closed (D03), and downstream effects remain unknown (D08/D09). See [workflow scope](docs/workflows.md) and the backlog.

### Communications setup (v0.6)

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Gmail API** and **Google Calendar API**.
2. Create an OAuth 2.0 **Desktop** client ID and copy the client ID and secret.
3. Run `uv run python scripts/google_auth.py --client-id ... --client-secret ...` and add the refresh token to `.env`.
4. Enable communications in `config/macbook.yaml`: `providers.communications.enabled: true`

Example: "Summarise unread email from the property manager" searches Gmail. Sending email or creating calendar events requires CLI approval.

## Documentation

| Document | Purpose |
|----------|---------|
| [Current architecture](docs/current-architecture.md) | Both current action paths, authority and preferred engineering direction |
| [Operations](docs/operations.md) | Local state, LaunchAgent, credentials, prerequisites and recovery gaps |
| [Engineering debt](docs/engineering-debt.md) | Active defects/deployment gaps with acceptance criteria |
| [Architecture history](docs/architecture.md) | Historical baseline and milestone notes; not the current specification |
| [Principles](docs/principles.md) | Engineering values and non-negotiables |
| [Roadmap](docs/roadmap.md) | Versioned delivery plan from scaffold to v1.0 |
| [Decisions](docs/decisions.md) | Architectural decision records (ADRs) |
| [Layout](docs/layout.md) | Repository structure and rationale |
| [Browser automation](docs/browser-automation.md) | Trusted portal URLs and Playwright execution |
| [Secrets](docs/secrets.md) | 1Password, login Keychain, runtime authorization and credential safety |
| [Knowledge layer](docs/knowledge-layer.md) | Earlier draft — superseded by [Architecture Review v2](docs/architecture-review-v2.md) |
| [Architecture Review v2](docs/architecture-review-v2.md) | Historical review; vocabulary/policy adopted, broader proposals deferred |

Read these before writing code.

## Repository layout

```
wally/
├── README.md
├── docs/                 # Architecture, roadmap, ADRs
├── src/wally/            # Application source
├── tests/                # Test suite
├── scripts/              # Operational and dev scripts
├── config/               # Non-secret configuration profiles
├── prompts/              # Versioned system and capability prompts
├── examples/             # Usage examples and integration sketches
└── .github/              # CI and contribution workflows
```

## Technology direction

| Concern | Choice |
|---------|--------|
| Language | Python |
| Primary LLM | OpenAI Responses API (via adapter) |
| Knowledge | Notion REST (via KnowledgeProvider + registry) |
| Workflows | n8n |
| Communications | Google Workspace (Gmail + Calendar) |
| Browser | Playwright (local) |
| Secrets | 1Password CLI (`op`) plus macOS login Keychain dispatch |
| Dev machine | MacBook |
| Future runtime | Mac Mini (Apple Silicon), not a verified deployment |

## Principles (summary)

1. Simplicity over cleverness.
2. Modular architecture over monolithic design.
3. Every capability is replaceable.
4. Business logic never depends on a specific LLM.
5. External systems sit behind interfaces.
6. Security and auditability are first-class.
7. Understandable by one engineer.
8. Maintainability before performance.
9. Document every architectural decision.
10. Ask when human judgement is required.

See [docs/principles.md](docs/principles.md) for the full list.

## License

Private project. All rights reserved.
