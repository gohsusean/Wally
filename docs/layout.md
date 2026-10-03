# Wally — Repository Layout

**Version:** 0.1  
**Last updated:** 2026-06-27

This document explains what each part of the repository is for and why it exists. The layout is intentional — every directory earns its place.

---

## Top level

```
Wally/
├── README.md           # Project overview and entry point for new readers
├── pyproject.toml      # Python packaging, tooling, and version
├── .env.example        # Documented environment variables (no secrets)
├── .gitignore          # Excludes secrets, runtime data, and build artefacts
│
├── docs/               # All design documentation (read before coding)
├── src/wally/          # Application source (stubs only in v0.1)
├── tests/              # Test suite (grows with implementation)
├── scripts/            # Operational scripts (health checks, migrations)
├── config/             # Non-secret configuration profiles
├── prompts/            # Versioned LLM prompts (not embedded in code)
├── examples/           # Runnable integration examples (from v0.2)
├── data/               # Runtime data (gitignored; audit logs live here)
└── .github/            # CI workflows
```

---

## `docs/`

The product's engineering memory. These files are as important as source code.

| File | Purpose |
|------|---------|
| `architecture.md` | System design, components, flows, trade-offs, risks |
| `principles.md` | Non-negotiable engineering values |
| `roadmap.md` | Versioned delivery plan (v0.1 → v1.0) |
| `decisions.md` | Architectural Decision Records (ADRs) |
| `layout.md` | This file — repository structure rationale |

**Rule:** Significant design changes update docs *before* or *alongside* code, never after.

Future additions:
- `docs/releases/v0.x.md` — release notes per version
- `docs/operator-guide.md` — Mac Mini deployment (v1.0)

---

## `src/wally/`

Application source. Organised by responsibility, not by technology.

**v0.1:** Package stubs only (`__init__.py`, `__main__.py`).

**Planned structure (from v0.2):**

```
src/wally/
├── ops/              # Observe, Matters, proposals, Approval Inbox, Act & Verify (not n8n)
├── gateway/          # Local Gateway boundary (v0.16); channel comes from the adapter
├── orchestrator/     # Reasoning loop — knows capabilities, not implementations
├── providers/        # Protocol definitions (interfaces)
├── adapters/         # Concrete integrations (OpenAI, Notion, HA, n8n)
├── safety/           # Action classification and approval gates
├── audit/            # Structured event logging
├── models/           # Domain types (Action, Message, MemoryEntry)
└── config/           # Config loader (reads YAML + env)
```

**Why `providers/` and `adapters/` are separate:**

- `providers/` defines *what Wally can do* (contracts).
- `adapters/` defines *how a specific system does it* (OpenAI, Notion, etc.).

Swapping Notion for Obsidian means writing a new adapter, not touching the orchestrator.

---

## `tests/`

Mirrors `src/wally/` structure. Three test tiers (see architecture.md §10):

1. **Unit** — safety classifier, config loader, prompt assembly
2. **Integration** — adapters against real services (`@pytest.mark.integration`)
3. **End-to-end** — orchestrator with mock LLM returning canned plans

Fixtures and mock providers live in `tests/conftest.py` and `tests/mocks/`.

---

## `scripts/`

Operational tooling, not application logic.

Planned scripts:

| Script | Version | Purpose |
|--------|---------|---------|
| `health_check.py` | v0.2 | Verify all enabled providers before Mac Mini cutover |
| `audit_query.py` | v0.2 | Search audit logs by date, action type, or session |
| `alias_sync.py` | v0.4 | Pull HA entities and suggest alias mappings |

---

## `config/`

Non-secret configuration. Secrets always come from environment variables.

| File | Purpose |
|------|---------|
| `macbook.yaml` | Development profile (MacBook) |
| `macmini.yaml` | Production profile (Mac Mini) |
| `entities.yaml` | HA entity alias mappings (v0.4+) |

Select profile via `WALLY_CONFIG=macbook` or `--config macbook`.

**Why YAML:** Human-readable, diffable, no code execution risk (unlike Python config files).

---

## `prompts/`

Versioned prompt files. Prompts are *data*, not code.

```
prompts/
├── system/v1.md           # Core Wally identity and behaviour
├── capabilities/          # Per-provider tool usage guidance (v0.3+)
└── safety/v1.md           # Safety instructions for the LLM (v0.2+)
```

**Why files, not constants:** Version control, diffing, rollback, and A/B testing without redeploying code. Audit log records which prompt version was used.

---

## `examples/`

Runnable sketches demonstrating integrations. Not part of the test suite — intended for manual exploration and documentation.

---

## `data/`

Runtime data directory. **Gitignored.** Created at first run.

```
data/
└── audit/
    ├── 2026-06-27.jsonl
    └── ...
```

Audit logs are append-only JSON Lines, one file per day.

---

## `.github/`

CI runs on every push to `main`:

- `ruff check` — linting
- `pytest` — tests

No deployment automation in v0.1. Mac Mini deployment is manual until v1.0.

---

## What is deliberately absent

| Not included | Why |
|--------------|-----|
| `docker/` | Single-process Python on bare metal is simpler for solo ops |
| `frontend/` | CLI first; HA and web clients for remote access (v0.9) |
| `migrations/` | No database in v0.1–v0.6; SQLite migrations added in v0.7 if needed |
| `requirements.txt` | `pyproject.toml` is the single source of dependency truth |
| Monorepo structure | One product, one repo, one engineer |

---

## Adding new directories

Before creating a new top-level directory, ask:

1. Does it serve a distinct responsibility?
2. Can it live inside an existing directory?
3. Will it still make sense in five years?

If the answer to (2) is yes, don't create it.
