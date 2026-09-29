# Wally

Wally is your personal **Chief of Staff** and **AI operating system** — an intelligent orchestrator that reasons over knowledge, manages communications, runs workflows, and supports decisions across your digital life.

Wally is not a chatbot. It is not a home automation platform.

## Status

**Status:** Shipped through **v0.13.0 — Assess & Propose.** The current milestone is **v0.14 — Approval Inbox** (not yet shipped). v0.10 browser automation, v0.11.1 secrets hardening, v0.12 Observe & Brief, and v0.13 Assess & Propose are complete. Suggestions are advice only. Wally still does not send, pay, submit, or write on its own. Act & Verify and daily-driver hardening are future work.

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
| `/brief` | Generate an operational brief, including suggestions |
| `/exit` | Quit |

One-shot brief (no REPL):

```bash
uv run wally brief
uv run wally brief --json --no-refresh
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

Example: "Run the weekly backup" triggers the `weekly-backup` webhook. Bill payments use execution capabilities (e.g. `pay-bill-bank-transfer`) selected at runtime and require CLI approval.

### Communications setup (v0.6)

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Gmail API** and **Google Calendar API**.
2. Create an OAuth 2.0 **Desktop** client ID and copy the client ID and secret.
3. Run `uv run python scripts/google_auth.py --client-id ... --client-secret ...` and add the refresh token to `.env`.
4. Enable communications in `config/macbook.yaml`: `providers.communications.enabled: true`

Example: "Summarise unread email from the property manager" searches Gmail. Sending email or creating calendar events requires CLI approval.

## Documentation

| Document | Purpose |
|----------|---------|
| [Architecture](docs/architecture.md) | System design, components, data flows, and boundaries |
| [Principles](docs/principles.md) | Engineering values and non-negotiables |
| [Roadmap](docs/roadmap.md) | Versioned delivery plan from scaffold to v1.0 |
| [Decisions](docs/decisions.md) | Architectural decision records (ADRs) |
| [Layout](docs/layout.md) | Repository structure and rationale |
| [Browser automation](docs/browser-automation.md) | Trusted portal URLs and Playwright execution |
| [Secrets](docs/secrets.md) | 1Password CLI, runtime authorization, credential injection |
| [Knowledge layer](docs/knowledge-layer.md) | Earlier draft — superseded by [Architecture Review v2](docs/architecture-review-v2.md) |
| [Architecture Review v2](docs/architecture-review-v2.md) | Platform governance philosophy and minimal refactor plan |

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
| Secrets | 1Password CLI (`op`) |
| Dev machine | MacBook |
| Future runtime | Mac Mini (Apple Silicon) |

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
