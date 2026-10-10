# Current repository layout

Updated 6 October 2026. Read [current architecture](current-architecture.md) for
system behavior and [operations](operations.md) for machine/private runtime state.
This map replaces the old scaffold/target-tree description; it does not promise
unimplemented files or live integration tests.

## Engineering entry points

- Root `AGENTS.md`: durable engineering instructions and safe checks.
- `README.md`: current milestone, setup and command navigation.
- `docs/current-architecture.md`: operational and legacy conversational paths;
  preferred new-capability direction.
- `docs/operations.md`: state, credentials, LaunchAgent and recovery gaps.
- `docs/engineering-debt.md`: active findings with acceptance criteria.
- `docs/decisions.md`: rationale, scoped supersession and ADR-042 direction.
- `docs/chief-of-staff.md`, `ops-privacy.md`, `browser-automation.md`, `secrets.md`,
  `workflows.md`: focused behavior/policy notes; heed their scope and known gaps.
- `docs/roadmap.md`: milestone history and future delivery, not deployment proof.
- `docs/architecture.md`, `knowledge-layer.md`, `architecture-review-v2.md`:
  historical design material; current behavior lives elsewhere.

## Source responsibilities

- `models/`: observations/matters/proposals/executions, principals, requests,
  actions and provider-domain types.
- `ops/`: observation, reconciliation, priority, proposals, decisions, brief,
  execution/verification and OperationsStore.
- `gateway/`: fixed-channel authenticated adapter boundary and socket library.
- `chatgpt/`: local HTTP/MCP adapter and token/host checks.
- `codex/`: explicit local stdio MCP for default-disabled scoped Notion edits.
- `adapters/macos/`: pinned native review/fresh biometric helper and boundary.
- `telegram/`: owner/private-chat handling, polling, outbox, cursor/lease,
  credential lifecycle and LaunchAgent helper.
- `orchestrator/`: conversational LLM/context/tool/response loop.
- `runtime/`, `safety/`: principals, policy, routing, browser/finance/secrets
  governance and action classification/approval gates.
- `providers/`: contracts; `adapters/`: OpenAI, Notion REST, Google, n8n, local
  conversation/finance, browser and secrets implementations. No HA adapter exists.
- `session/`, `conversation/`, `knowledge/`: SQLite sessions/FTS5, recall/context,
  database registry/classification.
- `config/`: settings loader; `audit/`: append-only JSONL event writer.
- `app.py`, `cli.py`, `cli_knowledge.py`, `__main__.py`: composition/CLI entry points.

Provider contracts describe what a capability does; adapters contain external API
mechanics. Runtime policy owns authority and execution selection, not an adapter
or LLM prompt. Changing `prompts/` or config can change behavior.

## Supporting files

- `pyproject.toml`, `uv.lock`: package/dependency/tool definitions.
- `.env.example`: credential-variable examples, never real credentials.
- `config/macbook.yaml`: selected default development profile.
- `config/macmini.yaml`: future deployment profile, not validated production.
- `config/notion.yaml`: platform defaults/overrides; classifications are in SQLite.
- `config/chatgpt.yaml`: static canonical document/recipient catalog used by both
  current external adapters.
- `config/notion-edits.yaml`: empty/default-disabled target and native-review policy.
- `config/workflows.yaml`: registered execution capabilities/webhook paths.
- `config/proactive.yaml`, `entities.yaml`: dormant historical HA/proactive designs.
- `prompts/`: versioned application prompts, not engineering-agent rules.
- `tests/`: mostly isolated/fake-provider tests, with real local transport tests;
  fixtures/mocks are Python files directly in tests, not a `tests/mocks/` directory.
- `scripts/`: Google/Notion setup and migrations/reset helpers plus a manual n8n
  export inventory. Inspect before running: setup/reset is not read-only inspection.
- `workflows/`: only a placeholder at handover; deployed n8n exports are missing.
- `examples/`: placeholder README; no runnable integrations.
- `.github/workflows/ci.yml`: Ruff/pytest; no configured type/coverage/secret-scan gate.
- Ignored `.env`, `.venv`, `data/` and caches: local state, detailed in operations.

SQLite persistence has existed since session support; there is no separate
`migrations/` directory, and additive store initialization must preserve records.
There is no current frontend, HA integration, scheduler, active secret-scanning
hook, release-note directory, `health_check.py`, `audit_query.py` or alias-sync script.
Adding such features requires actual implementation and updated documentation.
