# Wally — Architectural Decision Records

This document records significant architectural decisions. Each ADR follows a consistent format so future you can understand *why*, not just *what*.

**Statuses:** `proposed` → `accepted` → `deprecated` → `superseded`.
Accepted means accepted at that decision's date/scope, not fully implemented or
still current in every detail. Partial supersession keeps the remaining rationale;
read each current-scope note before treating an older decision as an instruction.
[Current architecture](current-architecture.md), [operations](operations.md) and
[engineering debt](engineering-debt.md) distinguish behavior, intent and gaps.

## Supersession and extension guide

- ADR-008 HA aliases/control: superseded in product scope by ADR-022.
- ADR-009: Notion storage survives; MCP transport/semantic-search assumptions do
  not describe the REST adapter/FTS5 implementation. ADR-019/020/021/023 refine it.
- ADR-012/016: remote delivery/timing superseded by ADR-036–041, not a shipped
  public API or HA approval system.
- ADR-014/032: secret separation survives; implemented 1Password and Telegram
  Keychain mechanisms extend the original future/sole-backend assumptions.
- ADR-017: original v0.8 cron/HA delivery was not implemented; ADR-022 removes HA
  control scope and future Phase 5 owns proactive scheduling.
- ADR-034 -> ADR-035 -> ADR-036 -> ADR-037 adds operational phases without
  collapsing observation, proposal, decision and execution.
- ADR-036/037 literal-origin authorization is superseded by ADR-038.
- ADR-038 is extended by ADR-039 registration and ADR-040/041 interfaces.
- ADR-042 records the preferred direction for new capabilities; it does not
  retroactively remove the legacy provider/tool path or fix its defects.
- ADR-043 supersedes ADR-027/029's legacy bill authority and paid-evidence rules
  with canonical dispatch binding and authenticated human verification.

---

## ADR-001: Python as the implementation language

**Status:** Accepted  
**Date:** 2026-06-27  
**Deciders:** Founding engineer + project owner

### Context

Wally needs a language that supports long-term maintainability, rich ecosystem for API integrations, and runs well on Apple Silicon (MacBook now, Mac Mini later).

### Decision

Use Python 3.12+.

### Consequences

**Positive:**
- Excellent library ecosystem for HTTP, async, and AI integrations.
- Readable by a solo engineer years later.
- Strong MCP client libraries available.
- Runs natively on Apple Silicon without compilation.

**Negative:**
- GIL limits CPU-bound parallelism (not a concern for I/O-bound orchestration).
- Deployment requires dependency management (mitigated by `uv` or `pip` with lock file).
- Slower than Go/Rust for high-throughput scenarios (not a concern for personal use).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| TypeScript/Node | Better for web frontends; weaker for long-running local services and ML ecosystem |
| Go | Excellent for services, but smaller AI/integration ecosystem; steeper prompt for solo scripting |
| Rust | Over-engineered for a personal orchestrator; slower iteration |

---

## ADR-002: Single-process architecture

**Status:** Accepted  
**Date:** 2026-06-27

**Current scope / supersession:** Single-node simplicity remains the rationale, not a guarantee of one OS process. Current Telegram and ChatGPT entry points create separate restricted processes sharing local operations SQLite; see [ADR-039](#adr-039-local-gateway-channel-bound-and-continuity-aware) and [current architecture](current-architecture.md).

### Context

Wally will run on a single Mac Mini operated by one person. The architecture must be simple to deploy, debug, and maintain.

### Decision

Run Wally as a single Python process. Providers are modules within the process, not separate services.

### Consequences

**Positive:**
- One `launchd` service to manage.
- No inter-service networking, service discovery, or message queues (initially).
- Simple debugging with standard Python tools.
- Low memory footprint on Apple Silicon.

**Negative:**
- A crash in one adapter could bring down the whole process (mitigated by error isolation within adapters).
- Cannot scale individual providers independently (not needed for personal use).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| Microservices | Operational overhead unjustified for solo personal use |
| Docker Compose multi-container | Adds complexity without benefit at this scale |
| Serverless | Wrong model for a always-on personal assistant |

### Review trigger

Revisit if Wally needs to run providers on separate machines (e.g. GPU workload on a different host).

---

## ADR-003: Provider pattern for all external integrations

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Wally integrates with many external systems (OpenAI, Notion, Home Assistant, n8n) that will change over a ten-year horizon. Business logic must not depend on any specific implementation.

### Decision

Define a Python `Protocol` (structural typing) for each capability domain. Concrete adapters implement these protocols. The orchestrator depends only on protocols.

### Consequences

**Positive:**
- Any provider swappable without touching orchestrator code.
- Easy to mock for testing.
- Clear boundary for integration tests.

**Negative:**
- Interface design requires upfront thought.
- Some adapter boilerplate per integration.

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| Direct API calls in orchestrator | Tight coupling; untestable; unmaintainable |
| Plugin system with dynamic loading | Over-engineered for ~5–10 providers |
| MCP for everything | Not all services have MCP servers; adds protocol dependency |

---

## ADR-004: OpenAI Responses API as initial LLM

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Wally needs a capable reasoning engine. The project owner has chosen OpenAI as the primary LLM provider.

### Decision

Use the OpenAI Responses API via a thin `LLMProvider` adapter. All orchestrator code interacts with the protocol, not OpenAI SDK types.

### Consequences

**Positive:**
- Responses API supports tool calling natively.
- Strong reasoning capability.
- Well-documented, stable API.

**Negative:**
- Cloud dependency — Wally cannot reason offline.
- API cost scales with usage.
- Vendor lock-in risk (mitigated by adapter pattern).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| Direct Chat Completions API | Responses API is the forward-looking interface with built-in tool support |
| Local model (Ollama) | Insufficient capability for v0.2; revisit at post-v1.0 |
| Anthropic Claude | Valid alternative; can be added as second adapter later |

### Review trigger

Revisit when Apple Silicon local inference reaches parity for orchestration tasks, or if API costs become significant.

---

## ADR-005: Rule-based safety classification (not LLM-only)

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Wally will execute real-world actions including financial transactions and device control. Safety gates must be reliable and auditable.

### Decision

Action risk classification is rule-based: action type + provider + parameters determine the classification. The LLM may *suggest* actions, but rules *enforce* gates. The LLM never has sole authority to bypass approval.

### Consequences

**Positive:**
- Deterministic, testable safety behaviour.
- Cannot be prompt-injected into executing financial actions.
- Audit trail shows rule that triggered gate.

**Negative:**
- Rules must be maintained as new action types are added.
- May classify conservatively (more approvals than necessary).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| LLM decides if approval needed | Non-deterministic; vulnerable to prompt injection |
| No classification (approve everything) | Unacceptable for financial and destructive actions |
| Hard-coded per-action approvals | Does not scale; rules generalise better |

---

## ADR-006: JSON Lines audit log

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Every Wally action must be reconstructable. The audit system must be simple, append-only, and not require a database.

### Decision

Write audit events as JSON Lines (`.jsonl`) files, one file per day, stored in `data/audit/`.

### Consequences

**Positive:**
- Trivial to implement and inspect (`cat`, `jq`).
- Append-only — no corruption from concurrent writes if single-process.
- Easy to back up (rsync).
- No database dependency.

**Negative:**
- Querying across large time ranges requires scanning files (acceptable at personal scale).
- No built-in retention policy (add later).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| SQLite | Valid for v0.7+ if query needs grow; overkill for v0.2 |
| Structured logging to syslog | Harder to query and replay |
| Cloud logging service | Violates local-first principle |

---

## ADR-007: Configuration profiles for environment separation

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Wally will be developed on a MacBook and deployed on a Mac Mini. The same codebase must run in both environments with different endpoints and settings.

### Decision

Use YAML configuration profiles in `config/` (e.g. `macbook.yaml`, `macmini.yaml`). Secrets via environment variables. Select profile via `WALLY_CONFIG` env var or CLI flag.

### Consequences

**Positive:**
- Clean separation of dev and prod settings.
- No secrets in config files or git.
- Trivial migration — copy config, set env vars, run.

**Negative:**
- Must keep profiles in sync when new settings are added.

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| `.env` files only | Mixes secrets and config; easy to commit accidentally |
| Single config with overrides | Less clear what differs between environments |
| Environment detection (hostname) | Implicit magic; explicit is better |

---

## ADR-008: Entity aliases for home automation

**Status:** Superseded in product scope by ADR-022
**Date:** 2026-06-27

**Current scope / supersession:** The alias/control design below is historical and unimplemented. [ADR-022](#adr-022-product-scope--chief-of-staff-not-home-automation) supersedes the home-control product scope; only optional future read-only HA context remains.

### Context

Home Assistant uses opaque entity IDs (`light.study_desk_lamp`). Wally and the user should refer to human-friendly names (`study_lights`).

### Decision

Maintain an alias mapping in `config/entities.yaml`. Wally's `HomeAutomationProvider` accepts aliases; the HA adapter resolves them to entity IDs.

### Consequences

**Positive:**
- Orchestrator and LLM never see HA internals.
- User can rename entities in HA without breaking Wally (update mapping).
- Aliases can map to groups (one alias → multiple entities).

**Negative:**
- Mapping file must be maintained manually (or via a setup script).
- Stale mappings cause "entity not found" errors.

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| LLM uses raw entity IDs | Leaks implementation; fragile |
| HA area/room names directly | Not all entities belong to areas; less precise |
| Auto-discovery | Complex; aliases are more reliable for voice ("study lights") |

---

## ADR-009: Notion as initial memory provider via MCP

**Status:** Partially superseded — storage choice retained; original transport/retrieval assumptions historical
**Date:** 2026-06-27  
**Updated:** 2026-06-27 — REST adapter shipped in v0.3

**Current scope / supersession:** Notion remains the knowledge backend, but the implemented adapter uses REST, not Cursor MCP. Vocabulary/policy/registry and contextual recall are refined by [ADR-019](#adr-019-knowledge-layer-vocabulary), [ADR-020](#adr-020-runtime-governance-policy-layer-1), [ADR-021](#adr-021-pending-classification-and-knowledge-registry) and [ADR-023](#adr-023-conversation-recall-is-contextual-memory-not-authoritative-knowledge). The MCP and semantic/vector-search claims below are historical assumptions, not implementation prerequisites.

### Context

Wally needs persistent memory. Notion is already used by the project owner. MCP provides a standard integration path.

### Decision

Implement `KnowledgeProvider` with a Notion adapter as the first implementation.

**v0.3:** Shipped as REST adapter.  
**v0.3.1:** Renamed to `KnowledgeProvider`; see ADR-019.

### Consequences

**Positive:**
- Notion is accessible from any device.
- Rich structured data (databases, pages, properties).
- MCP standardises the integration.

**Negative:**
- Notion API rate limits (3 requests/second).
- Cloud dependency for memory.
- MCP protocol may change.
- Not ideal for semantic/vector search (addressed in v0.7).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| Local Markdown files | No structured query; no mobile access |
| Obsidian | Local-first but weaker API for orchestration |
| PostgreSQL + pgvector | Over-engineered for v0.3; good v0.7 option |
| Notion REST API directly | MCP is cleaner if available; REST as fallback |

### Open question

~~**Notion schema design** — requires project owner input before v0.3 implementation.~~

**Resolved:** Use existing Notion workspace with relational databases. Database mapping configured in `config/notion.yaml` during v0.3 setup.

---

## ADR-011: Session persistence from v0.2

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Conversation history could be in-memory until v0.7, or persisted from the first working runtime.

### Decision

Persist sessions in SQLite from v0.2. Store session ID, messages, and timestamps. v0.7 adds semantic search and consolidation — not basic persistence.

### Consequences

**Positive:**
- Conversations survive restarts during development.
- Audit trail can correlate with session history.
- Simple SQLite — no external dependency.

**Negative:**
- Adds ~100 lines to v0.2 scope.
- v0.7 may refactor schema for embeddings (migration script required).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| In-memory until v0.7 | Loses context on every restart; frustrating during dev |
| JSON files per session | Harder to query; SQLite is standard library friendly |

---

## ADR-012: CLI approval now; messaging channels later

**Status:** Partially superseded — original channel timing replaced by ADR-036–041
**Date:** 2026-06-27

**Current scope / supersession:** CLI tool prompts remain on the legacy path. The HA/Telegram delivery dates below are superseded by operational decisions/execution in [ADR-036](#adr-036-proposal-approval-is-not-execution-chief-of-staff-phase-3), [ADR-037](#adr-037-guarded-execution-of-the-exact-approved-version-chief-of-staff-phase-4), [ADR-038](#adr-038-authenticated-principals-not-channel-name-literals) and interfaces [ADR-040](#adr-040-chatgpt-authenticates-the-connection-then-the-subject)/[ADR-041](#adr-041-telegram-decides-through-a-server-side-nonce). Telegram decides but cannot execute; HA approval was not delivered.

### Context

Consequential actions need human approval. Surface must work locally first, then remotely.

### Decision

v0.2–v0.5: CLI y/n prompts via `ApprovalProvider`.  
v0.9: HA mobile notifications for remote approval.  
Post-v1.0: Telegram and WhatsApp as additional approval/messaging channels.

### Consequences

**Positive:**
- Simplest path to working approval gates.
- Messaging adapters are independent additions later.

**Negative:**
- Remote approval limited until v0.9 (acceptable — financial workflows arrive in v0.5 but CLI approval works on Mac Mini via SSH if needed).

---

## ADR-013: Wally-owned n8n workflow definitions

**Status:** Accepted  
**Date:** 2026-06-27

**Current scope / supersession:** Ownership/reproducibility remains the goal. The current registry contains webhook paths, `workflows/` has no exports, and the deploy helper does not push definitions. These claims below are unfulfilled deployment intent; [D08](engineering-debt.md#d08--n8n-definitionsdeployment-not-reproducible-high-deployment-gap) defines acceptance. Do not infer real backup/payment effects from a registry name.

### Context

Workflows could trigger ad-hoc n8n workflows by name, or Wally could own version-controlled definitions.

### Decision

Workflow definitions live in `workflows/` in the repository. A deploy script pushes them to n8n. Wally triggers workflows by stable ID defined in repo.

### Consequences

**Positive:**
- Workflows versioned alongside Wally code.
- Reproducible across MacBook and Mac Mini.
- Reviewable in git before deployment.

**Negative:**
- Deploy step required when workflows change.
- n8n JSON export format must be managed.

---

## ADR-014: Notion for memory; 1Password for secrets

**Status:** Accepted separation — future timing superseded; mechanisms extended by ADR-032/033/041
**Date:** 2026-06-27

**Current scope / supersession:** The separation remains. The future timing is superseded by implemented [ADR-032](#adr-032-secretsprovider-via-1password-cli) and [ADR-033](#adr-033-secret-safe-execution-artifacts-and-auth-verification); Telegram additionally uses a login-Keychain runtime copy under [ADR-041](#adr-041-telegram-decides-through-a-server-side-nonce) and [operations](operations.md). API/OAuth keys can still be local environment/ignored .env secrets.

### Context

Financial and personal data needs storage. Highly sensitive credentials (passwords, cards, bank accounts) need stricter handling than general memory.

### Decision

- **Notion** (`KnowledgeProvider`): bills, notes, relational knowledge, financial *metadata*.
- **1Password** (future `SecretsProvider`): passwords, credit card numbers, bank account credentials — retrieved only at execution time, never stored in Notion or Wally.

Wally never persists 1Password secrets. Audit log records that a secret was *used*, not its value.

### Consequences

**Positive:**
- Clear separation of "what I know" vs "how I authenticate."
- 1Password is designed for credential storage.

**Negative:**
- Requires 1Password Connect or CLI integration (likely v0.5 with workflows).
- Two systems to configure.

### Review trigger

Evaluate 1Password integration timing when first workflow needs credentials (v0.5).

---

## ADR-015: Explicit failure when providers unreachable

**Status:** Accepted  
**Date:** 2026-06-27

### Context

When OpenAI or Notion is down, Wally could silently degrade, use stale cache, or hallucinate.

### Decision

Wally must explicitly tell the user when a required provider is unreachable. It must not proceed as if it has information it cannot retrieve. No silent fallback to general knowledge for memory-dependent queries.

### Consequences

**Positive:**
- User always knows reliability state.
- Prevents dangerous actions based on missing data.

**Negative:**
- More "I can't help right now" responses during outages.

---

## ADR-016: Remote access required (v0.9)

**Status:** Original delivery plan superseded by ADR-039–041
**Date:** 2026-06-27

**Current scope / supersession:** The original v0.9 public HTTP/TLS API and Mac Mini deployment were not delivered. Current access is a local Gateway ([ADR-039](#adr-039-local-gateway-channel-bound-and-continuity-aware)), loopback ChatGPT adapter with hosted deployment gated ([ADR-040](#adr-040-chatgpt-authenticates-the-connection-then-the-subject)), and private Telegram polling ([ADR-041](#adr-041-telegram-decides-through-a-server-side-nonce)). These supersede this implementation schedule, not authentication requirements.

### Context

Wally runs on Mac Mini at home. User needs access from phone and laptop when away.

### Decision

v0.9 delivers a minimal authenticated HTTP API on the Mac Mini. TLS required. API key auth initially.

### Consequences

**Positive:**
- Interact with Wally from anywhere.
- Foundation for Telegram/WhatsApp bridges later.

**Negative:**
- Security surface area increases — auth and TLS are mandatory, not optional.

---

## ADR-017: Proactive intelligence — both triggers, conservative default

**Status:** Historical unimplemented delivery plan — scope refined by ADR-022; scheduling future
**Date:** 2026-06-27

**Current scope / supersession:** The v0.8 cron/event delivery below was not implemented; config/proactive.yaml is dormant. HA control/event scope is superseded by [ADR-022](#adr-022-product-scope--chief-of-staff-not-home-automation). Scheduling and preferences belong to future Phase 5 in [roadmap](roadmap.md); the current outbox does not provide scheduled reminders.

### Context

Proactive Wally can use scheduled checks, HA events, or both. Aggressiveness must be configurable.

### Decision

v0.8 implements both cron-based and HA event triggers. Default aggressiveness: **conservative**. User configures quiet hours and per-category opt-in/out in `config/proactive.yaml`.

### Consequences

**Positive:**
- Flexible without being annoying by default.
- Security events can bypass quiet hours.

**Negative:**
- More configuration surface in v0.8.

---

## ADR-010: No implementation in v0.1

**Status:** Accepted historical scope — v0.1 only
**Date:** 2026-06-27

**Current scope / supersession:** This prohibition applied only to the original v0.1 scaffold task. It is not a current no-code instruction. See the implemented phases and [ADR-042](#adr-042-new-capabilities-converge-on-the-operational-architecture).

### Context

The project owner explicitly requested architecture and documentation before any code.

### Decision

v0.1 delivers only documentation and repository scaffold. No Python source beyond package stubs.

### Consequences

**Positive:**
- Architectural mistakes caught before code exists.
- Clear agreement on direction before sunk cost.
- Documentation sets the standard for all future work.

**Negative:**
- No working demo yet (expected).

---

## ADR-019: Knowledge layer vocabulary

**Status:** Accepted  
**Date:** 2026-06-27  
**Implemented:** v0.3.1

### Context

"Memory" conflated conversation history with personal knowledge and page-shaped storage. Wally is a platform that orchestrates knowledge assets, not a memory retrieval app.

### Decision

Rename `MemoryProvider` → `KnowledgeProvider`. Introduce `KnowledgeAsset` with `KnowledgeAssetType` (metadata) and `KnowledgeClass` (operational | governance). Rename tools to `knowledge_*`.

### Consequences

**Positive:**
- Correct vocabulary for a long-lived platform.
- Clear separation from session/conversation state.

**Negative:**
- One-time rename churn across ~25 files.

---

## ADR-020: Runtime governance policy (Layer 1)

**Status:** Accepted  
**Date:** 2026-06-27  
**Implemented:** v0.3.1

### Context

Governance knowledge (SOPs, principles, playbooks) defines how Wally operates. It must never be modified automatically — not even with user approval.

### Decision

`runtime/policy.py` evaluates write actions before provider calls. Writes targeting `knowledge_class: governance` are **rejected** (not approval-gated). Unmarked databases default to `operational`.

### Consequences

**Positive:**
- Deterministic enforcement independent of LLM behaviour.
- Defense in depth before provider permissions (Layer 2).

**Negative:**
- Requires `knowledge_class` on each database in `config/notion.yaml`.

**Superseded:** Database classification moved to knowledge registry in ADR-021 (v0.3.2).

---

## ADR-021: Pending classification and knowledge registry

**Status:** Accepted  
**Date:** 2026-06-28  
**Implemented:** v0.3.2

### Context

Requiring full per-database entries in `config/notion.yaml` does not scale as Notion workspaces grow. Defaulting unknown databases to `operational` (ADR-020) grants write access before explicit human review — violating the principle that unknown resources should never become more privileged.

### Decision

1. **Pending classification** — Newly discovered Notion databases enter `pending` state: readable, not writable.
2. **Knowledge registry** — SQLite store (`data/knowledge_registry.db`) is the authoritative source for database classifications after explicit user approval via `/knowledge approve`.
3. **Heuristic recommendations** — On discovery, Wally suggests `operational` or `governance` with reasoning (advisory only; LLM does not enforce).
4. **Runtime policy** — Writes are allowed only when classification is `operational`. `pending` and `governance` are rejected before provider calls.
5. **Minimal `notion.yaml`** — Platform defaults, excludes, and schema overrides only.

### Consequences

**Positive:**
- Unknown databases never gain write access automatically.
- Classifications are auditable (`approved_by`, `approved_at`, audit events).
- New Notion databases work for read after integration share; write after approval.

**Negative:**
- Extra step to approve each new database.
- One-time migration from legacy `notion.yaml` database blocks.

---

## ADR-022: Product scope — Chief of Staff, not home automation

**Status:** Accepted  
**Date:** 2026-06-29

### Context

Early roadmap placed Home Assistant as v0.4 — implying Wally is a home automation platform. The product vision is a **personal Chief of Staff**: knowledge, communications, workflows, finance, and decision support. The physical home is operated by Home Assistant and Alexa independently.

### Decision

1. **Wally is not a home automation platform** for the foreseeable future.
2. **No device control** (lights, AC, blinds) in the roadmap through v1.0.
3. **Alexa** remains the primary voice interface for home automation.
4. **Home Assistant** remains the authoritative home OS. Wally does not compete with it.
5. **Future HA integration** (if any) is a **low-priority optional provider** — read-only context first.
6. **Roadmap resequenced:** knowledge → runtime platform → workflows → communications → conversation intelligence → finance → proactive Chief of Staff.

### Consequences

**Positive:**
- Clear product identity: personal AI OS / Chief of Staff.
- Architecture effort focuses on provider platform before domain expansion.
- No coupling between Wally releases and home infrastructure.

**Negative:**
- ADR-008 (entity aliases) deferred until optional HA provider is prioritised.
- Early architecture docs referenced HA as nearer-term than reality.

---

## ADR-023: Conversation recall is contextual memory, not authoritative knowledge

**Status:** Accepted  
**Date:** 2026-06-29

### Context

v0.7 adds cross-session conversation recall via SQLite FTS5. Prior chat content could be mistaken for ground truth — especially when it conflicts with Notion knowledge, governance policy, or the user's current instruction.

### Decision

1. **Conversation recall is contextual memory** — continuity aid, not a source of truth.
2. **Fixed precedence on conflict** (highest wins):
   - Policy Assets (governance knowledge, safety prompts, `runtime/policy.py`)
   - Knowledge Assets (operational Notion knowledge)
   - Current explicit user instruction
   - Conversation recall
3. Conversation tool results are annotated `authoritative: false` in `runtime/authority.py`.
4. **FTS5 remains the v0.7 search mechanism** — no embeddings in this milestone.

### Consequences

**Positive:**
- Clear boundary between "what we once discussed" and "what is true / allowed".
- Policy and knowledge enforcement remain deterministic regardless of chat history.

**Negative:**
- Precedence in reasoning is prompt-guided for reads; write policy is already hard-enforced for knowledge.

---

## ADR-024: Reasoning Router owns profile selection

**Status:** Accepted  
**Date:** 2026-06-29

### Context

Reasoning profiles (`fast`, `balanced`, `deep`) map to models and control how much inference a task receives. Initially the profile was chosen at startup via config, env, or CLI — requiring the user to think about models.

Wally's architecture principle: AI provides reasoning; the runtime provides governance and routing.

### Decision

1. Introduce **`ReasoningRouter`** (`runtime/reasoning_router.py`) — sole responsibility: given a `Task`, return a reasoning profile.
2. Introduce **`Task`** abstraction (`models/task.py`) with intent, tools, workflow, and metadata for routing without inspecting natural language.
3. **Override hierarchy:** explicit CLI/env override → task-specific routing rules → default (`balanced`).
4. **Deterministic rules** in config (`providers.llm.routing`) — no LLM-based routing in v0.7.
5. **Reasoning Provider** receives profile per request, maps to model, calls API — does not choose profile.
6. `--profile` and `WALLY_REASONING_PROFILE` remain for development and debugging only.

### Consequences

**Positive:**
- Users never choose models during normal operation.
- Router can evolve (complexity, cost, escalation) without changing orchestrator or provider contracts.
- Clear separation: routing (runtime) vs reasoning (provider).

**Negative:**
- Per-request model selection requires profile map at provider init.
- Task taxonomy must grow as new capabilities need distinct routing.

### Alternatives considered

- **LLM-based routing** — rejected for v0.7; non-deterministic and adds latency/cost.
- **Startup-only profile** — rejected; cannot match task complexity without per-turn selection.

### Review trigger

Revisit when adding intent classification from user messages (e.g. architecture review → deep without tool invocation).

---

## ADR-025: WebProvider for external knowledge (provider-agnostic)

**Status:** Accepted  
**Date:** 2026-06-29

### Context

Wally needs current public information when internal sources (Notion, Gmail, Calendar, conversation recall) are insufficient or stale. This must remain distinct from internal knowledge and follow the External Source Rule — web content is untrusted evidence, not authority.

### Decision

1. Introduce **`WebProvider`** (`providers/web.py`) with `search()` and `fetch()` — provider-agnostic interface.
2. Expose **`web_search`** and **`web_fetch`** tools via the orchestrator; the main LLM does not browse outside this interface.
3. **First adapter:** OpenAI Responses API `web_search` hosted tool inside `adapters/web/adapter.py` (reuses `OPENAI_API_KEY`). URL fetch uses `httpx` with size limits.
4. Classify web output as **`EXTERNAL_WEB`** in `runtime/authority.py` — lowest precedence, `authoritative: false`, `trust: low`.
5. Configure via `providers.web` in YAML; future adapters (Tavily, Brave, etc.) swap via `adapter` key.

### Consequences

**Positive:**
- Clean separation of internal vs external knowledge.
- Runtime governance preserved — web cannot override policy or trigger privileged actions.
- Swappable search backends without orchestrator changes.

**Negative:**
- OpenAI-first adapter couples initial web search to OpenAI billing/models.
- Fetch uses basic HTML stripping — not a full readability pipeline.

### Alternatives considered

- **OpenAI `web_search` as hosted tool on main LLM** — rejected; bypasses Wally's provider boundary and governance.
- **Tavily first** — viable; deferred to keep v0.8 minimal with existing OpenAI credentials.

### Review trigger

Add Tavily or Brave adapter when OpenAI web search cost/latency or citation format becomes a constraint.

---

## ADR-026: Retrieval Router (planned) and Content Sanitizer

**Status:** Accepted  
**Date:** 2026-06-29

### Context

v0.8 ships `WebProvider` with `web_search` / `web_fetch` tools the Reasoning Provider may invoke. This matches existing knowledge and communications patterns but leaves **retrieval policy** with the LLM. Wally's principle is that the runtime owns routing and governance.

External web content also introduces adversarial input risk. Basic HTML stripping in the adapter is insufficient as a documented security layer.

### Decision

1. **Retrieval Router (planned, not v0.8):** Future `runtime/retrieval_router.py` will deterministically select which knowledge sources to consult before reasoning (Notion, conversation, Gmail, calendar, web) based on task type, freshness, authority, and internal availability. Document v0.8 LLM tool choice as **temporary**.

2. **Content Sanitizer (v0.8):** Implement `runtime/content_sanitizer.py` — deterministic, no LLM. Pipeline for external content: `Web Provider → Content Sanitizer → Reasoning Provider`.

3. **Defense in depth:** Sanitizer removes boilerplate and obvious injection phrases. Primary security remains runtime policy, trust model, External Source Rule, approval engine, least privilege, and provider isolation.

4. **`web_fetch` constraints** documented and enforced by design: GET only, no JS, no forms, no auth, no private context leakage, no instruction execution.

### Consequences

**Positive:**
- Clear long-term separation: retrieval routing, sanitization, reasoning, execution.
- Runtime remains the authoritative control plane.
- External content path is explicit and auditable.

**Negative:**
- Retrieval Router deferred — interim reliance on prompts for tool selection.
- Sanitizer cannot catch all adversarial content; must not be over-trusted.

### Review trigger

Implement Retrieval Router when finance and web providers are stable and retrieval overlap (e.g. bill in Notion vs web) needs deterministic resolution.

### Future enhancement

Add Tavily or Brave adapter when OpenAI web search cost/latency or citation format becomes a constraint.

---

## ADR-027: FinanceProvider composes knowledge and workflows

**Status:** Accepted  
**Date:** 2026-06-29

**Current scope / supersession:** Composition remains. [ADR-043](#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence) replaces payload-based paid evidence, config-optional financial prompts and generic financial workflow access with authenticated canonical dispatch/human verification. The evidence-type list below is historical.

### Context

v0.9 needs bill awareness and approval-gated payments without duplicating Notion storage or direct bank access.

### Decision

1. **`FinanceProvider`** (`providers/finance.py`) — read bills from Knowledge, trigger payments via Workflow only.
2. **Local adapter** composes existing `KnowledgeProvider` + `WorkflowProvider` — no separate finance datastore.
3. **`evaluate_finance_policy`** — `finance_trigger_payment` limited to workflows with `action_class: financial`.
4. **`SecretsProvider` stub** — protocol only; 1Password adapter deferred.
5. Financial tools use `ActionClass.FINANCIAL` → approval gate via existing `require_approval: financial`.
6. **Bill paid rule** — `evaluate_bill_paid_write_policy` blocks marking finance bills paid without `payment_evidence` (workflow success, user confirmation, or future verification provider). Approval summaries include full bill context via `format_finance_approval_summary`.

### Consequences

**Positive:**
- Read-first finance without new integrations.
- Payments stay in n8n; Wally orchestrates with policy + approval.

**Negative:**
- Bill data quality depends on Notion knowledge organisation (`bills_role` config).
- Full credential flow awaits SecretsProvider implementation.

---

## ADR-028: VerificationEngine for finance payment evidence checks

**Status:** Accepted  
**Date:** 2026-06-27

### Context

v0.9 finance payments need evidence checks before approval — comparing bill statements/emails against trusted Knowledge Assets — without embedding policy in the LLM or Finance provider.

### Decision

1. **`VerificationEngine`** (`runtime/verification_engine.py`) — lightweight runtime component comparing external `statement` evidence to trusted `bill` data from Knowledge. Produces structured `VerificationReport` with statuses `verified`, `missing`, `mismatch`, `unknown`.
2. **Deterministic comparison** — LLM may extract statement fields; normalization and mismatch/block logic are runtime-only.
3. **Bank account rule** — normalize digits before compare; mismatch or missing trusted account blocks payment; statement omitting account is a non-blocking warning when Knowledge has a verified account.
4. **`verify_finance_payment` / `evaluate_finance_verification_policy`** in `finance_safety.py`; `ToolRegistry` blocks `finance_trigger_payment` before approval when `report.blocked`.
5. Approval prompts include verification summary via `format_finance_approval_summary`.

### Consequences

**Positive:**
- Reusable verification primitive for future high-trust actions.
- Clear separation: evidence (statement) vs authority (Knowledge Asset).

**Negative:**
- Quality depends on LLM extraction of `statement` fields and Knowledge Asset completeness (e.g. `bank_account`).

---

## ADR-029: Execution capabilities replace business-specific workflows

**Status:** Accepted  
**Date:** 2026-06-27

**Current scope / supersession:** Legacy conversational routing remains callable. [ADR-043](#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence) supersedes the interim model-supplied bill/parameter authority and payload evidence; D01/D02 are resolved in code. Missing live metadata remains D03 and now fails closed. [ADR-042](#adr-042-new-capabilities-converge-on-the-operational-architecture) keeps the operational path as the preferred extension point.

### Context

`config/workflows.yaml` registered business-specific workflows (e.g. per-vendor payment flows). Business logic should live in Wally; n8n should execute generic capabilities. Users should say *"Pay my TM110 bill"* without knowing workflow names.

### Decision

1. **Workflow registry** describes execution capabilities (`pay-bill-bank-transfer`, future `pay-bill-card-portal`) with `capability.domain` + `capability.method` metadata.
2. **`ExecutionCapabilityRouter`** (`runtime/execution_router.py`) maps provider `payment_method` from Knowledge → workflow. LLM does not select payment workflows.
3. **`finance_trigger_payment`** requires trusted `bill` only; runtime injects `workflow` and merged provider parameters.
4. **Canonical capability** — `pay-bill-bank-transfer` is the generic bank-transfer execution capability (workflow name and n8n webhook path align).
5. **VerificationEngine** extended with payment-method-specific rules (bank transfer vs card portal).
6. Documentation in `docs/workflows.md`.

### Consequences

**Positive:**
- Scales to new providers without new workflow YAML entries per bill type.
- Clear separation: Knowledge (what/how to pay) vs n8n (execute transfer/portal).

**Negative:**
- Provider Knowledge assets need structured metadata (`payment_method`, etc.) — Notion adapter does not populate these yet; LLM supplies `bill` fields interim.

### Review trigger

Populate provider metadata from Notion properties when finance bill schema is defined.

---

## ADR-030: BrowserAutomationProvider as execution layer

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Finance v0.9 routes payments by `payment_method` to execution capabilities. Bank transfer uses n8n; card portal and statement download need governed browser interaction. Business logic must stay in Wally; browsers must not be controlled by ad-hoc LLM tool calls for payments.

### Decision

1. **`BrowserAutomationProvider`** (`providers/browser.py`) — first-class provider for deterministic browser actions (navigate, fill, click, upload, download, read page, wait for user).
2. **Not a CapabilityProvider for finance** — runtime invokes browser actions after verification and approval; LLM does not choose browser steps for payments.
3. **Playwright** as default adapter in `adapters/browser/playwright/` — runtime depends on protocol only.
4. **Independent from SecretsProvider** — v0.10 supports manual authentication; v0.11+ may inject runtime-resolved credentials.
5. **`card_portal`** maps to `pay-bill-card-portal` → Browser Automation; `bank_transfer` remains n8n.
6. Milestone **v0.10** immediately after finance v0.9; **SecretsProvider** deferred to **v0.11**.
7. **Trusted portal URLs only** — navigation targets from Knowledge Assets; see ADR-031 and `runtime/browser_safety.py`.

### Consequences

**Positive:**
- Clear execution layer for portal flows without polluting n8n or LLM tools.
- Progressive automation: manual auth → secrets → fuller portal completion.
- Governance chain unchanged (verify → approve → route → execute).

**Negative:**
- Playwright adds deployment weight (browser binaries, headful sessions on Mac Mini).
- Portal selectors require maintenance per provider (kept in capability scripts, not LLM).

### Review trigger

Revisit if a SaaS browser API (Browserbase, etc.) better fits Mac Mini ops than local Playwright.

---

## ADR-031: Trusted portal URLs for browser automation

**Status:** Accepted  
**Date:** 2026-06-27

### Context

Browser automation (v0.10) opens payment portals. URLs in emails, web pages, PDFs, or LLM output are untrusted and could enable phishing or prompt-injection driven navigation.

### Decision

1. **`BrowserAutomationProvider` may only navigate to portal URLs from approved Knowledge Assets** (e.g. `payment_portal_url` on provider/bill records).
2. **Runtime enforcement** in `browser_safety.py` before Playwright — not prompt-only.
3. **Blocked sources for navigation targets:** email, web fetch, PDFs, unverified user text, LLM-generated URLs. These may inform verification, not browser destinations.
4. **Missing trusted URL** → stop execution; instruct user to add/approve portal URL in knowledge.
5. Playwright adapter integration tests must verify policy enforcement (v0.10).

### Consequences

**Positive:**
- Clear anti-phishing boundary aligned with Knowledge authority hierarchy.
- Testable policy before Playwright ships.

**Negative:**
- Sub-path navigation requires the full trusted URL in Knowledge (strict match).

---

## ADR-032: SecretsProvider via 1Password CLI

**Status:** Accepted  
**Date:** 2026-08-15

**Current scope / supersession:** 1Password action-secret handling remains. The provider now dispatches Keychain references too; Telegram startup authentication and credential copying are separate purposes documented in [ADR-041](#adr-041-telegram-decides-through-a-server-side-nonce), [secrets](secrets.md) and [operations](operations.md). The original sole-backend/execution-only description is extended, not a removal of authorization for action credentials.

### Context

Finance and browser automation need credentials at execution time. Storing passwords in Notion or config would violate the knowledge-layer security model. v0.10 supports manual portal login; v0.11 must resolve secrets without giving the LLM a secrets tool.

### Decision

1. **`SecretsProvider`** with a **1Password CLI** adapter (`op read` / `op whoami`). Connect Server is not required for a personal Mac.
2. **Runtime-only access** — `GovernedSecretsResolver` requires `authorized=True`, set only after approval-gated execution. No `secrets_*` LLM tools.
3. **References only in Knowledge** — `op://vault/item/field`. Raw credential strings are rejected by `evaluate_secret_reference`.
4. **Optional injection** — browser FILL/CLICK when refs and selectors exist; n8n parameters from `workflow_secret_refs`. Otherwise manual auth remains the path.
5. **Audit references, never values.**

### Consequences

**Positive:**
- Credentials stay in 1Password; Wally holds them only in memory during an approved step.
- Browser automation still works with secrets disabled.

**Negative:**
- Depends on local `op` session (or `OP_SERVICE_ACCOUNT_TOKEN` if the operator configures it outside Wally).
- Portal selectors remain per-provider maintenance.

### Review trigger

Revisit if 1Password Connect (or another vault) is a better fit for headless Mac Mini operation than CLI sign-in.

---

## ADR-033: Secret-safe execution artifacts and auth verification

**Status:** Accepted  
**Date:** 2026-08-15

### Context

v0.11 resolves passwords into Playwright FILL actions and n8n payloads. Secondary paths (exceptions, webhook error bodies, screenshots, traces, dataclass repr, tool results, `page_text`) can leak values even when the audit log is clean. Operators also need an auth-only check that does not click pay.

### Decision

1. **`authorized=False` by default** on `trigger_payment` / `run_card_portal_payment`. Only `finance_trigger_payment` after the approval gate passes `authorized=True`.
2. **Scrub resolved values** from model-facing n8n responses and from `page_text`. `VERIFY_AUTH` results omit `page_text`.
3. **No Playwright traces, HAR, video, or screenshots.** `SCREENSHOT` is policy-denied. Fill/click failures return generic messages without exception chaining that could include stdout.
4. **n8n errors** do not include webhook response bodies. Non-JSON bodies are not forwarded raw.
5. **Knowledge-configured `VERIFY_AUTH`** (`auth_success_selector`, `auth_success_url_contains`) confirms login and stops. Payment clicks are not part of the default post-auth script.
6. **Recording adapter and `BrowserAction.__repr__`** redact FILL values.

### Consequences

**Positive:** Auth-only acceptance is possible on the existing card-portal path. Leak tests can search for canary values.

**Negative:** Operators must maintain portal selectors. Full page dumps are no longer the default when verify fields are set.

---

## ADR-034: Observation and Matter model for Observe & Brief

**Status:** Accepted  
**Date:** 2026-08-16  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** The Observation/Matter/read-only ingestion boundary remains. [ADR-035](#adr-035-durable-proposed-actions-chief-of-staff-phase-2), [ADR-036](#adr-036-proposal-approval-is-not-execution-chief-of-staff-phase-3) and [ADR-037](#adr-037-guarded-execution-of-the-exact-approved-version-chief-of-staff-phase-4) extend the loop separately. D04 removes financial receipt auto-resolution: all receipts remain FYI until canonical obligation binding exists, under the owner-approved clarification below. Live metadata remains D03 in [engineering debt](engineering-debt.md).

### Context

v1.0 should make Wally a proactive Chief of Staff. The first increment must persist operational understanding (what changed, what is still open) without autonomous writes. Email, calendar, and Notion already exist as providers. n8n must not become the brain.

### Decision

1. Add Wally-owned SQLite state (`OperationsStore`) for **Observations** (facts) and **Matters** (open loops).
2. Observe incrementally via checkpoints over existing `CommunicationsProvider` and `KnowledgeProvider` reads.
3. Reconcile Matters with deterministic rules; LLM is not required for bookkeeping or tests.
4. Generate an on-demand CLI brief from Matters, not from a raw inbox dump.
5. Treat connector payloads as untrusted data. Prompt-injection text cannot authorize actions or become policy.
6. Version this increment as **0.12.0** (v1.0 Phase 1). Do not declare production 1.0.0.

**v0.12.1 (quality patch):** Strip Google Calendar auto-event boilerplate from brief/observation text; unmatched receipts are FYI not “resolved”; render brief datetimes in the configured or system-local timezone. Still read-only.

**D04 clarification (owner-approved, 6 October 2026):** Uniqueness of candidate
is not proof of identity. Financial receipt evidence cannot resolve or materially
advance a Matter until bound to its canonical obligation. Knowledge obligations
carry asset and recurrence-period identity; Gmail message/thread IDs are provenance
and context, not canonical bill identity. Matching amounts, invoice-thread history,
observation trust labels and claimed confirmation cannot substitute for that bind.

No canonical financial receipt association exists today. All financial receipts
therefore remain separate FYI evidence, including receipts in the exact thread
of a single invoice. No invoice-thread exception is retained. A thread reply
cannot close or advance a financial Matter either; nonfinancial communications
and canonical calendar reconciliation remain unchanged. Replay, later invoices
and disappearance of competitors do not promote a note into proof. Obligation
attention, proposal versions and decisions remain intact; legacy FYI history is
preserved without migration or automatic repair of past financial closures.

No human association operation is added. Future canonical matching (including
live metadata work under D03) or explicit human association needs reviewed design;
any human operation must use PrincipalAuthority-issued context/capability checks,
never payload booleans or source strings. Portal execution confirmation remains
review evidence, not payment completion.

### Consequences

**Positive:** Persistent open loops without expanding write surface area. Reuses Gmail/Calendar/Notion adapters.

**Negative:** Semantic classification is heuristic (invoice/receipt/reply). Live mailbox quality depends on snippet quality and Knowledge metadata (`cadence`, `due_date`).

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| n8n cron + workflow state | Moves business logic out of Wally |
| LLM-only inbox dump each morning | No durable state; noisy; unsafe with untrusted mail |
| Reuse SessionStore messages as matters | Sessions are conversation logs, not operational loops |

### Review trigger

Revisit when Phase 2 proposals need a durable Proposed Action model.

---

## ADR-035: Durable proposed actions (Chief of Staff Phase 2)

**Status:** Accepted — implemented in v0.13.0  
**Date:** 2026-08-16  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** The non-executable advice/reference model remains. Item 5/no approval and initial two-intent limits are superseded by [ADR-036](#adr-036-proposal-approval-is-not-execution-chief-of-staff-phase-3) and document-delivery support in [ADR-040](#adr-040-chatgpt-authenticates-the-connection-then-the-subject). [ADR-037](#adr-037-guarded-execution-of-the-exact-approved-version-chief-of-staff-phase-4) adds separate execution; it does not make proposals dispatchable. Live counts below are historical checkpoint evidence.

### Context

Phase 1 (v0.12) gives Wally durable Observations and Matters plus an explainable brief. The brief states what is open but not what the user might do about it. Phase 2 should turn Matters into durable, explainable suggestions while keeping Wally read-only against every external system.

The repository already has an execution path: `PlannedAction` → `ToolRegistry.execute` → runtime policy → `ApprovalGate` → provider. It also has an established trust boundary in which email, calendar, and arbitrary Notion text are untrusted data that cannot authorize actions (ADR-034). Phase 2 must add a persistent advice layer without weakening either.

The risk is not that a proposal is wrong. The risk is that a persisted record derived from untrusted content becomes something a later phase can dispatch without human review.

Live evidence from the v0.12.1 acceptance database shaped the scope: of 52 observations, 50 are generic `email_received`, one is a receipt, and one is a calendar event. There are zero `invoice`, `email_sent`, `email_reply`, and `knowledge_obligation` observations. Only the calendar path reliably produces Matters today, and `email_sent` never appears because `observe_email` queries Gmail with `after:` alone, which returns inbox mail rather than sent mail.

### Decision

1. **Add `ProposedAction`** to the operational domain, defined in `src/wally/models/ops.py` beside `Observation` and `Matter`, and persisted in a new `proposals` table in `operations.db`. It is advice addressed to the user, never an authorization. Generation, persistence, and lifecycle logic live in `wally/ops/`.
2. **Keep it out of `models/actions.py`.** The operational domain and the executable domain stay in separate modules: `ProposedAction` shares no fields with `PlannedAction`, so no proposal can be passed to the tool registry by accident.
3. **Store provider-independent intent.** A closed `ProposalIntent` enum plus reference-only parameters (matter, observation, knowledge, event, thread identifiers). No tool names, no provider arguments, no amounts, no addresses.
4. **Generate deterministically.** Rules over trusted Matter state select intent, parameters, risk class, and lifecycle. The schema reserves provenance fields so a later milestone may add LLM-authored prose confined to display-only strings; that path is not implemented in this milestone and no test may depend on a model.
5. **No approval concept exists.** The status enum has no `approved` value, the model has no authorization or token field, and the proposal service constructor accepts no capability provider, no secrets provider, and no browser executor.
6. **Suppress proposals for injection-flagged matters** in the first slice, rather than lowering their confidence.
7. **Additive persistence only.** The `proposals` table is created with `CREATE TABLE IF NOT EXISTS` at startup. Existing v0.12.1 rows are left unmodified — generation reads Matters, but writes nothing back to them — and no reset is required.
8. **Ship two intents:** `PREPARE_FOR_EVENT` and `REVIEW_BILL`.
9. **Timed expiry is calendar-only.** Event-preparation proposals expire at event start. Bill-review proposals carry no expiry and close only when the Matter resolves, changes materially, or a successor supersedes them, so an overdue unresolved bill is never hidden by an arbitrary window.
10. **Presentation is inline.** Proposals render under their Matter with a distinct `↳ Suggested:` prefix and a global footer stating that Wally has taken no action.

### Consequences

**Positive:** Wally gains durable, explainable next steps with no new external write surface. Proposals are derivable from Matters, so losing the table costs nothing permanent. Phase 4 inherits an explicit, reviewable translation step instead of replaying stored arguments. Calendar-only expiry removes an arbitrary grace-window constant and makes `expired` a single-intent transition.

**Negative:** Phase 4 must write an intent-to-`PlannedAction` translator that would have been unnecessary had tool calls been stored. Deterministic prose is more repetitive than model-authored prose. Proposal quality depends on Matter quality, which for bills is currently unproven live: no `invoice` observation has been classified yet, so `REVIEW_BILL` acceptance waits on a real bill email.

### Alternatives considered

| Alternative | Why rejected |
|-------------|-------------|
| Store `tool_name` + arguments | Creates a persisted dispatchable payload derived from untrusted content; couples durable rows to renameable tool names |
| Reuse `PlannedAction` as the proposal type | Structural compatibility with the executor is exactly the failure mode to prevent |
| LLM generates proposals directly | Gives untrusted email influence over which action is suggested; breaks deterministic testing |
| Extend `Matter` with proposal fields | Conflates "what is true" with "what to do"; blocks multiple proposals per matter and supersession |
| Separate `proposals.db` | Loses transactional locality with matters and doubles backup surface |
| Include a `FOLLOW_UP` intent now | Depends on `email_sent` observations, of which the live database has none; would ship a path that cannot be exercised in practice |
| Fixed expiry for bill proposals | Would hide an overdue unresolved bill after an arbitrary window |

### Review trigger

Revisit when Phase 3 introduces the approval inbox and needs an `approved` transition, when observe is extended to sent mail (enabling `FOLLOW_UP`), or if deterministic proposal text proves too rigid in daily use.

---

## ADR-036: Proposal approval is not execution (Chief of Staff Phase 3)

**Status:** Accepted — implemented in v0.14.0  
**Date:** 2026-09-30  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** Inert approval/version semantics remain. The always-false helper/no executor boundary is superseded by [ADR-037](#adr-037-guarded-execution-of-the-exact-approved-version-chief-of-staff-phase-4). Literal user_cli/user_repl authorization is superseded by [ADR-038](#adr-038-authenticated-principals-not-channel-name-literals). Telegram notification dismissal is not proposal dismissal. Delivery defer release is a current defect, D06 in [engineering debt](engineering-debt.md).

### Context

Phase 2 persists `ProposedAction` rows and states that none of them is an authorization (ADR-035). The runtime already has a separate execution-time gate: `ApprovalProvider.request_approval` plus `ApprovalGate`, used when a tool call is about to run. Phase 3 needs a durable decision on a proposal — approve, reject, or defer — without creating a path from `status == approved` to Gmail, Calendar, browser automation, n8n, payment, secret resolution, or a shell.

The failure mode is treating the new status as a token the executor can trust. A second failure mode is letting untrusted source text, or model prose, write that status.

### Decision

1. **Extend `ProposalStatus`** with `approved`, `rejected`, and `deferred`. Keep the Phase 2 closures (`superseded`, `invalidated`, `expired`, `dismissed`). `dismissed` stays terminal. Reject is the user-facing refusal. There is no separate dismiss command in this milestone.
2. **Store the decision on the proposal row** that was reviewed: `decision`, `decided_at`, `decision_origin`, `decision_note`, `defer_until`, and `decision_fingerprint`. The fingerprint is copied from the stored row inside `record_decision`, so the caller cannot bind the decision to a different version.
3. **Trusted origins only.** `user_cli` and `user_repl`. `record_decision` rejects every other origin, including text taken from a message or a model reply.
4. **One writer.** `save_proposal` cannot enter a user-decision status or rewrite a row that already has one. System closure (`close_proposal`) cannot enter a user-decision status either. It can invalidate or supersede an approved row when the facts change.
5. **Material change voids the approval for the new version.** The previous row keeps its decision and becomes `superseded` or `invalidated`. The successor is `proposed`. A normalized amount on `observation.extra["amount"]` is part of a bill fingerprint when present. Prose in a snippet is not. Snippet-only edits do not churn an approval. A new invoice observation still changes `observation_ids`, which already changes the fingerprint.
6. **Rejection sticks for that fingerprint.** Deferred proposals return to `proposed` when `defer_until` passes, with the decision fields cleared. The defer remains in the audit log. An invalidated proposal may reopen as `proposed` when the same facts return, with the decision cleared so a prior approval is not inherited. Superseded rows are not reopened.
7. **Keep `ApprovalProvider` for execution time.** It is not the inbox. The inbox does not call it. v0.14 does not call it as a side effect of approving a proposal.
8. **`execution_allowed` always returns false.** Orchestrator, runtime, adapters, and safety code do not read `ProposalStatus`. Act & Verify must replace that function later. It must not gain a true branch in this milestone.
9. **Additive migration.** New columns are `ALTER TABLE ... ADD COLUMN` with empty defaults. The open-proposal unique index is widened to `proposed`, `approved`, and `deferred`. Existing v0.13 rows stay pending and undecided. `operations.db` is not wiped.

### Consequences

**Positive:** Sean can decide on a proposal and the decision survives a refresh. A changed bill cannot keep an old approval. Rejected items do not pop back from the same evidence. Nothing in this milestone can treat approval as permission to send, pay, or resolve a secret.

**Negative:** Approved proposals sit inert until Act & Verify exists. A reverted proposal version that matches a superseded fingerprint still produces no active row, matching the Phase 2 rule that superseded history is not resurrected. Amount changes that never land in `extra["amount"]` or a new observation id do not by themselves change the fingerprint.

### Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Reuse `ApprovalProvider.request_approval` as the inbox | That API returns a boolean for a live tool call. It does not persist a proposal version, a defer date, or a rejection |
| Let `status == approved` satisfy `ApprovalGate` | Collapses authorization intent into execution and lets a later bug skip the human check at act time |
| Store approval in n8n | Moves policy out of Wally and gives an external system the decision of record |
| Trust "approved" text in the source | Untrusted data would authorize itself |
| Overwrite the approved row when the amount changes | The user would no longer be able to see which version they approved |

### Review trigger

Revisit when Act & Verify needs to translate an approved fingerprint into a `PlannedAction`, and when that translation must fail closed unless `decision_fingerprint` still matches and `execution_allowed` is replaced by a real check.

---

## ADR-037: Guarded execution of the exact approved version (Chief of Staff Phase 4)

**Status:** Accepted — implemented in v0.15.0  
**Date:** 2026-09-30  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** Guarded review-only execution, fresh confirmation and uncertainty handling remain. Literal-origin checks in the original decision are superseded by [ADR-038](#adr-038-authenticated-principals-not-channel-name-literals). Live Notion plan prerequisites are not yet mapped (D03); synthetic service coverage does not prove portal readiness. This no-payment guarantee applies to Act & Verify, not every legacy conversational provider action.

### Context

ADR-036 made approval durable and kept it inert. Phase 4 needs a way to act on an approved proposal and to report what really happened. The risks are the ones ADR-036 guarded against, now with a live executor behind them:

- An approval is treated as a standing permission.
- An approval is carried over to a version the user never saw.
- The proposal names its own tool, URL, or credential.
- A browser step that merely returned is reported as a completed obligation.
- A crash or timeout leads to a silent second submission.

### Decision

1. **Execution is a separate user request.** `wally execute <proposal-id>` and `/execute` are the only entry points. `ActVerifyService.execute` accepts only `user_cli` and `user_repl` origins. Observe, brief, inbox, reconciliation, the orchestrator, and model output do not import it. Approving a proposal never runs it.
2. **Exact approved version.** Execution requires `status == approved`, `decision == approved`, a trusted decision origin, and `decision_fingerprint == fingerprint`. The Matter must be open and the proposal unexpired. The proposal is recomputed from current evidence and must produce the same fingerprint. A mismatch is audited as `execution_blocked_stale_approval` and requires a fresh approval. All of this is checked again after the runtime prompt and immediately before the executor runs. The plan digest must also be unchanged.
3. **Code-owned whitelist.** `SUPPORTED_EXECUTION_INTENTS = {REVIEW_BILL}`. The intent maps to one typed `PortalReviewPlan` executor, `browser.portal_review_login`. The plan is built only from whitelisted metadata keys on the single trusted Knowledge asset the proposal references: portal URL, secret refs, selectors, and success condition. The asset must not be pending and must belong to the bills role. The URL must be https. No proposal field names a tool, provider, URL, argument, or secret. `PREPARE_FOR_EVENT` and email-only bills are unsupported and show "Approved, but execution is not supported yet."
4. **REVIEW_BILL is not PAY_BILL.** A review logs in to the trusted portal, checks the login, and stops. Only FILL and CLICK login actions and a read-only VERIFY_AUTH are allowed. The card-payment path, `finance_trigger_payment`, and n8n are unreachable from this executor. There is no PAY_BILL intent.
5. **Both approvals are kept.** After preflight, `ApprovalGate` is evaluated (dry-run denies without prompting). `ApprovalProvider.request_approval` is then always called with a summary built from the typed plan, even when the gate would allow. A denial or prompt error records `authorization_denied`.
6. **Secrets after authorization only.** Preflight sees refs. Approval, preflight failure, and denial resolve nothing. The executor resolves both refs through `GovernedSecretsResolver` after authorization and before opening a session, so a secrets failure is known not executed. Values do not reach records, audit, reports, or the model.
7. **Durable attempts and idempotency.** Each request writes an `executions` row with a status of `pending`, `preflight_failed`, `authorization_denied`, `running`, `executed_unverified`, `verified_success`, `verified_failure`, or `failed`. A partial unique index allows one `pending`, `running`, `executed_unverified`, or `verified_success` row per proposal fingerprint. The executor runs only after a compare-and-set from `pending` to `running`.
   - A leftover `pending` row is known not executed; it is retired as `failed`, and a retry is allowed.
   - `running` and `executed_unverified` are uncertain. They block new attempts until reviewed.
   - `verified_success` is never repeated.
   - An executor exception after the session opens is recorded as uncertain and never auto-retried.
8. **Independent verification.** Success requires an explicit authenticated result from the Knowledge-configured success condition. A missing signal is inconclusive and reported as "Executed, but verification could not confirm completion." `wally verify` re-assesses stored evidence without calling the executor. For uncertain attempts, it can record the user's own check with `--confirm success|failure`.
9. **No optimistic resolution.** A verified portal review does not resolve the Matter or change the proposal. Only Observe evidence resolves a bill. Settled attempts are audited as `matter_unchanged_after_execution`.
10. **Additive migration.** `CREATE TABLE IF NOT EXISTS executions` plus indexes. Observations, matters, proposals, decisions, and checkpoints are untouched.

### Consequences

**Positive:** An approved bill with a trusted portal can be opened and logged into on explicit request. The result is recorded and checked independently. Stale approvals, untrusted targets, denials, and uncertain outcomes all fail closed with an audit trail.

**Negative:** Only one action type is executable. Execution still needs two human confirmations. An uncertain attempt needs manual review before anything else can run for that version. `wally verify` cannot reopen the browser session, so a later check relies on stored evidence or the user's confirmation.

### Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Map proposal fields to a tool name and arguments | The proposal would choose its own capability; untrusted evidence could steer it |
| Treat proposal approval as the runtime approval | Removes the last human check at act time; one stale approval becomes a standing grant |
| Add PAY_BILL to exercise the executor | Invents a consequential action without a design; a review approval could drift into payment |
| Auto-retry on timeout | A login or submission may already have happened; duplicates are worse than a manual review |
| Resolve the Matter when login verifies | Login is not payment evidence |

### Review trigger

Revisit when a second action type is proposed, when scheduling or notifications want to start execution, or when the double confirmation proves too costly in daily use.

---

## ADR-038: Authenticated principals, not channel-name literals

**Status:** Accepted — implemented in v0.15.0  
**Date:** 2026-09-30  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** Authority/provenance separation remains. The initial local-channel set is extended by [ADR-039](#adr-039-local-gateway-channel-bound-and-continuity-aware) registration and [ADR-040](#adr-040-chatgpt-authenticates-the-connection-then-the-subject)/[ADR-041](#adr-041-telegram-decides-through-a-server-side-nonce) restricted interfaces. This architecture does not retroactively wrap all legacy ToolRegistry actions; see [ADR-042](#adr-042-new-capabilities-converge-on-the-operational-architecture).

### Context

ADR-036 and ADR-037 authorized decisions and execution by comparing a string origin to `{user_cli, user_repl}`. That couples the application services to two CLI-shaped identities. A later ChatGPT, Telegram, or Home Assistant channel would have to edit Act & Verify and the approval inbox, or worse, pass a forged origin string.

The CLI and REPL must not be the authorization boundary. They should only authenticate the local operator and call the same services.

### Decision

1. **`Principal` + `RequestContext`.** A channel adapter asks `PrincipalAuthority.issue(channel)` for a context carrying subject, channel, authentication method, a per-process HMAC grant, a correlation id, and optional external session/request refs. Application services call `authority.authorize(context, capability)` before deciding, executing, or verifying.
2. **Capabilities, not name lists.** `DECIDE_PROPOSAL`, `EXECUTE_PROPOSAL`, `VERIFY_EXECUTION`. Core logic never compares `user_cli` or `cli` itself. Adding a channel is a registration on the authority with an explicit capability set.
3. **Adapters issue; services check.** `wally execute` and `/execute` call `ActVerifyService.execute(context=...)`. Approvals call `ObserveBriefService.decide(context=...)`. A hand-built `Principal(channel="cli")`, a channel named in email or model text, or a context from another authority instance fails closed.
4. **Provenance is not authorization.** `RequestProvenance` (channel, principal, external refs, correlation id) is stored on proposals and executions for audit. It is never part of the proposal fingerprint. A future ChatGPT turn may supply selected conversational context as evidence; Wally still constructs the ProposedAction from canonical facts.
5. **Correlation is optional and additive.** The same correlation id can be reused across brief → decide → execute → verify. Each CLI invocation may also use its own. `executions.correlation_id` and `proposals.decision_correlation_id` plus `store.correlated()` make the lineage queryable. This is not an active-matters system.
6. **v0.14 rows.** Decisions recorded as `user_cli` / `user_repl` keep that origin string and gain `decision_principal = owner` on open. They remain valid approvals.

### Consequences

**Positive:** A later remote channel can be registered without changing Act & Verify. Forged channel names grant nothing. Provenance and correlation exist without changing what was approved.

**Negative:** HMAC grants are per-process and not persisted, so a context cannot be replayed across processes. That is intended. CLI and REPL still share the local-operator policy; remote channels will need a narrower policy when they exist.

### Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Keep `{user_cli, user_repl}` sets in every service | Every new channel edits Act & Verify |
| Trust a channel string on the request | Email and model text would approve themselves |
| Store the HMAC grant | A leaked database row would become a bearer token |
| Put correlation in the fingerprint | The same bill would look like a new action per interface |

### Review trigger

Revisit when the first remote channel is added, and when that channel's authentication is stronger than "this process issued the context."

---

## ADR-039: Local Gateway, channel-bound and continuity-aware

**Status:** Accepted — implemented in v0.16.0  
**Date:** 2026-10-03  
**Deciders:** Founding engineer + project owner

### Context

ADR-038 lets a channel adapter issue a request context and call the same decide / execute / verify services. The next interface, ChatGPT, must not sit inside the Wally process and must not be able to name its own channel. A single correlation id is also the wrong identity for an ongoing issue: one electricity problem can contain an investigation, a payment, a retry, and a verification, each with its own request lineage, and it can show up in more than one conversation.

### Decision

1. **One local boundary.** An external client talks to a channel-specific adapter. The adapter authenticates to the Gateway. The Gateway asks the runtime to issue a principal. Wally's database remains the only durable state. Interfaces do not sync with each other.
2. **The channel is the registration.** `AdapterRegistration` binds an adapter id and credential to one channel that `PrincipalAuthority` already knows. `cli` and `repl` cannot be registered this way. Payload keys `channel`, `principal`, `grant`, `capability`, `capabilities`, and `authentication` are rejected, including when nested. The effective channel is the registration. Credentials are compared as SHA-256 digests with `hmac.compare_digest`. The HMAC signing key stays inside `PrincipalAuthority`.
3. **Existing services.** `decide`, `execute`, and `verify` call `ObserveBriefService` and `ActVerifyService` with a runtime-issued context. There is no generic tool or executor interface. If the adapter is not an approval adapter, execute returns before Act & Verify and resolves nothing.
4. **Correlation is a lineage. ActiveMatter is the issue.** `submit_request` mints a new correlation id unless `continue_correlation_id` names a lineage that already exists on a gateway request, proposal, or execution. A request may also carry `active_matter_id`. One ActiveMatter accumulates many correlation ids in `active_matter_correlations`. A correlation already linked to a different handle cannot be moved.
5. **Many sessions.** `active_matter_sessions` is unique on `(active_matter_id, channel, external_session_ref)` and stores first-seen and last-seen. A new session updates nothing else. No transcript is copied between sessions.
6. **Bounded untrusted evidence.** A request may carry at most eight items of kind `latest_user`, `prior_user`, `assistant_summary`, or `external_ref`. Each is length-capped, hashed, and stored `untrusted`. Evidence does not change approval, executor choice, secrets, verification, or the proposal fingerprint. `get_context` and the audit log omit the text.
7. **No second Matter.** ActiveMatter visibility is `active` or `archived`, meaning whether Wally should keep offering the handle. The canonical Matter status is the state of the underlying issue.
8. **Additive tables.** `gateway_requests`, `active_matters`, `active_matter_correlations`, and `active_matter_sessions` are created with `CREATE TABLE IF NOT EXISTS`. v0.15 rows stay. The Gateway does not listen unless a process calls `GatewaySocket`. `create_app` attaches an empty runtime.

### Consequences

**Positive:** A later adapter can be registered to a narrow channel without editing Act & Verify or exposing the signing key. One ongoing issue can keep every request lineage and every session ref. Forged channel claims write nothing.

**Negative:** There is no remote approval UX, so a Gateway execute without an approval adapter cannot run. CLI and REPL remain a separate in-process path. Evidence text is stored on the request row for the capsule, but it is not a conversation history and it is not echoed by the read APIs.

### Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Let the payload choose `channel` | A client could claim `cli` and inherit the local operator |
| Share the HMAC key with the Gateway process | A compromised adapter could mint principals |
| Use one correlation id as the ActiveMatter id | A later payment or retry would look like a different issue, or overwrite the first lineage |
| `UNIQUE(active_matter_id, channel)` | A second conversation on the same channel would erase the first session |
| Store one excerpt only | A future adapter needs a small typed capsule, not a single truncated line |
| Copy Matter status onto the handle | Two fields would disagree about whether the bill was resolved |

### Review trigger

Revisit when the first real adapter (ChatGPT) is registered, when that adapter needs an approval UX, or when evidence needs a retention limit shorter than the request row.

---

## ADR-040: ChatGPT authenticates the connection, then the subject

**Status:** Accepted — implemented in v0.17.0  
**Date:** 2026-10-04  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** Hosted authentication/confirmation remains unvalidated. Subject/org metadata is supplied by the bearer caller, not an independently authenticated OpenAI assertion, and the confirmation flag is operator attestation rather than per-call proof. Keep decisions disabled pending D15 in [engineering debt](engineering-debt.md). [ADR-045](#adr-045-interface-neutral-approval-centralized-authorization) supersedes flag-based enablement and now enforces disabled decisions in code. Execute/verify remain absent.

### Context

ADR-039 left the first external adapter unspecified. ChatGPT is that adapter, and it is the primary place to ask what needs attention, continue a Matter, and submit work. Two shortcuts are not acceptable. A copied `openai/subject` string must not become the Wally owner. A model calling `record_decision` must not count as the owner confirming that decision.

ChatGPT's custom MCP connectors expect OAuth 2.1 on a reachable HTTPS resource. This deployment is one person, one machine. The adapter therefore uses the strongest connection check that stays on that machine, and it leaves decision tools off when the live host cannot show an explicit confirmation.

### Decision

1. **Authenticated connection, then subject, then the fixed adapter.** `wally chatgpt serve` listens on `127.0.0.1` only. `POST /oauth/token` checks the owner secret and a PKCE S256 code verifier, then returns a random bearer. `initialize` may run without a token and returns no private state. `tools/list` and `tools/call` require `Authorization: Bearer`. The bearer digest is compared with `hmac.compare_digest`. The OpenAI subject is not accepted as the owner secret and cannot mint a token.
2. **Why this is enough for the current single-user deployment.** The owner secret never leaves the operator's environment. The token endpoint is loopback, so a remote client cannot complete the grant by possessing a subject string. The Gateway credential is a third secret, checked by the Gateway, and the HMAC signing key stays inside `PrincipalAuthority`. The adapter registration fixes the channel at `chatgpt` with `approval_adapter` false. Together: authenticated localhost connection + pinned subject, when a decision is involved + the fixed adapter = the Wally owner context.
3. **Fail closed when the host cannot authenticate.** ChatGPT cannot dial `127.0.0.1`, and its connector OAuth does not present the owner secret. Publishing `/oauth/token` through a Secure MCP Tunnel would turn the owner secret into a network credential. Until a connector can complete an authenticated grant without that shortcut, decision-capable tools stay unregistered. Reads and lower-risk writes still require the bearer when the local server is used.
4. **`record_decision` is a separate confirmation.** It is registered only when writes are enabled, `WALLY_CHATGPT_DECISION_CONFIRMATION` is set, the subject allowlist is non-empty, and both secrets are set. The default is off, because the tool handler cannot see a distinct "the user clicked confirm" bit. The tool is annotated `destructiveHint=true` and `readOnlyHint=false`. When it is registered, the handler still requires the bearer, a subject in the allowlist, optional organization pin, `decide_proposal`, a proposal that exists and is still `proposed`, and an exact fingerprint. Host confirmation does not replace those checks. `submit_request`, session linking, and handle visibility stay ordinary writes.
5. **Annotations match behavior.** Read tools use `readOnlyHint=true`. State-changing tools use `readOnlyHint=false`. There is no generic operation tool. `execute` and `verify` are not registered.
6. **Wally builds the proposal.** A delivery request carries bounded untrusted evidence and title hints. Grounding requires one exact normalized document title and one exact recipient title from the operator's trusted catalog. The existing proposal path stores `deliver_document` at status `proposed`. The fingerprint covers matter id, intent, document id, and recipient id. The utterance is not hashed. Ambiguous, unknown, or invented ids write nothing. `deliver_document` is not in `SUPPORTED_EXECUTION_INTENTS`.
7. **Continuity and schema.** Session refs come from host metadata (`openai/session`), not from the model. One session may link many Matters, and one Matter may link many sessions. No transcript is synced. No new tables. The subject is audit metadata.

### Consequences

**Positive:** Possession of a subject string does not read private state or record a decision. A model invocation of `record_decision` does not approve a proposal while the confirmation flag is off. Delivery proposals stay on the same reconcile and decide path as other proposals, and Act & Verify will not run them.

**Negative:** ChatGPT's hosted connector cannot complete this grant. `record_decision` ships disabled. A later public OAuth resource, with its own login and no owner secret on the tunnel, is required before that tool is turned on. The trusted document catalog is a local list, not a live Notion query.

### Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Treat `openai/subject` as the credential | Anyone who learns the string could act as the owner |
| Register `record_decision` because the model called a write tool | The call is not the owner's confirmation of that proposal |
| Put the owner-secret grant on a public tunnel | The secret would travel to ChatGPT's OAuth client |
| Hard-code a ChatGPT plan name | Availability is configuration, and plan docs disagree |
| Add `execute` once a decision exists | Execution is a later, separate authorization |

### Review trigger

Revisit when a ChatGPT connector completes an authenticated grant without sending the owner secret, or when a live session shows the host confirming `record_decision` before that tool is enabled.

---

## ADR-041: Telegram decides through a server-side nonce

**11 October extension:** [ADR-046](#adr-046-telegram-authorizes-eligible-scoped-notion-edits)
retains this channel's restricted capabilities and established decision-only intents.
Eligible scoped metadata cards additionally authorize a separate trusted worker;
the blanket no-execution-interaction description below is historical for that class.

**Status:** Accepted — implemented in v0.18.0  
**Date:** 2026-10-04  
**Deciders:** Founding engineer + project owner

**Current scope / supersession:** Owner/button/no-execution rules remain. Since the initial release, SecretsProvider resolution and login-Keychain deployment replaced the environment-only bot-token mechanism (commits 8200e33/ce47fcd). [Operations](operations.md) records LaunchAgent and interpreter access scope. D05/D10/D11 track recovery/reliability gaps; at-least-once send duplication remains an accepted limitation.

### Context

ChatGPT remains the ad-hoc place to think with Wally. Telegram is the private chat Wally can open. A button payload is size-capped and untrusted. Long polling redelivers updates until the offset advances. A send can succeed at Telegram and still be lost locally if the process dies before it stores the message id.

### Decision

1. **Owner pin.** Channel `telegram`, `approval_adapter` false. The owner is one numeric user id in a private chat. Groups, other users, edits, display names, and message text do not authorize. The deployed bot token is resolved from its configured Keychain reference at startup; 1Password remains the source copy. Environment injection is only the local/test fallback when no reference is set.
2. **Opaque callback.** `callback_data` is a random nonce plus `a`, `r`, or `n`. `notification_outbox` binds the nonce to the proposal id, fingerprint, chat, owner, and allowed actions. The handler authenticates the user, loads the nonce, and only then calls the existing decide path with the stored fingerprint.
3. **Ingress.** `telegram_updates` records each `update_id` before `telegram_cursor` advances. Gateway submit is idempotent on `(channel, external_request_ref)`. A replay does not open a second request or proposal. A repeated decision finds the proposal no longer `proposed` and does not write a second one.
4. **Delivery.** One `decision_required` row per proposal fingerprint. Reactive and proactive proposals share that path. Semantics are at-least-once with best-effort duplicate suppression. `sending` rows carry a lease. Durable `delivered` rows are not resent. Attempt count, retry time, and a terminal `failed` state cover the rest. A rare second card after an ambiguous crash is accepted. A second decision is not.
5. **Not now.** The button dismisses that notification. The proposal stays `proposed`. The reply says no reminder is scheduled. "approve it" may show the card again and does not decide.
6. **One poller.** `telegram_lease` is a single-row lock for this Mac. There is no webhook and no reasoner. Execute and verify stay off the channel.

### Consequences

**Positive:** A copied callback cannot name a different proposal. A redelivered update cannot mint a second proposal. Approve still ends at the v0.15 decision service.

**Negative:** An ambiguous `sendMessage` can show two cards. `Not now` does not come back on its own. Proactive cards wait until this process is polling and a private chat id is known.

### Alternatives considered

| Alternative | Why rejected |
|-------------|--------------|
| Put the proposal id and fingerprint in `callback_data` | The field is small and untrusted |
| Advance the offset before the update is stored | A crash would drop the update |
| Claim exactly-once Telegram delivery | The crash window after `sendMessage` cannot be closed |
| Send a direct card and an outbox card | The owner would see two prompts for one proposal |
| Treat Not now as a scheduled snooze | No scheduler; no Later/defer button is implemented |

### Review trigger

Revisit when a reminder should be scheduled, or when Telegram should hold a second fingerprint-bound execution authorization.

---

## ADR-042: New capabilities converge on the operational architecture

**Status:** Accepted engineering direction — documentation only; no path migration implemented
**Date:** 2026-10-06
**Deciders:** Project owner, following the engineering handover audit

### Context

Wally has both a current conversational ToolRegistry/provider action path and an
operational proposal/decision/Act & Verify path. The latter adds authenticated
capabilities, canonical version binding, authority-free provenance, durable
attempts and uncertainty handling. The legacy path remains callable with its own
risk/approval gates and weaker financial input/evidence provenance. Documentation
must not present them as equally governed or silently remove existing behavior.

### Decision

1. New capability development converges on the operational proposal/capability/
   provenance architecture rather than adding privileged behavior through legacy
   conversational tools. Provider backends may be reused behind reviewed boundaries.
2. Keep proposal advice, authenticated decision, fresh execution authorization,
   execution and independent verification separate. Bind action to the reviewed
   fingerprint/canonical target and revalidate after the prompt.
3. Preserve Telegram/current ChatGPT execute/verify restrictions. Intentional
   trust-boundary changes need explicit rationale and updated/new ADRs.
4. Maintain current behavior in this handover. Migration/retirement, new executors,
   defects, schemas, service/credential operations and dependency changes require
   separate implementation work; this decision claims none of those fixes.
5. Root AGENTS.md owns concise durable instructions. Current architecture owns
   both flows, operations owns deployment/recovery, engineering debt owns findings
   with acceptance criteria, and roadmap owns future delivery. Historical designs
   remain clearly marked with scoped supersession.

### Consequences

**Positive:** new work has one preferred authority/lifecycle foundation, while
readers can still understand the actual legacy behavior and deployment limits.
Important reasoning survives without recovering whole chat histories.

**Negative:** existing paths retain their current limitations. Live metadata,
n8n reproducibility and recovery still need external artifacts/operator decisions.
The direction is not a certification of financial safety or a completed migration.

### Alternatives considered

- Add more privileged model-callable tools: duplicates weaker trust/provenance
  handling and permits model tool choice to steer safety boundaries.
- Remove legacy tools in the handover: changes behavior outside documentation scope.
- Treat historical Accepted ADRs as the current specification: obscures superseded
  assumptions and unmet deployment criteria.

### Review trigger

Revisit when planning path migration, a new consequential executor, or a change to
interface authority. Verify the design against actual code/tests and current
external prerequisites before claiming readiness.

---

## ADR-043: Canonical legacy finance dispatch and authenticated human evidence

**Status:** Accepted — focused D01/D02 stabilization

**Date:** 2026-10-06

### Context

The legacy finance path verified a caller-supplied bill but merged execution
overrides, permitting verify(A)/authorize(A)/execute(B). Paid-write policy accepted
model payload claims as evidence. Generic financial workflow calls could bypass
bill checks. The existing operational authority/provenance distinction provides
a smaller fix than inventing another grant system or migrating unrelated tools.

### Decision

1. Require a canonical asset ID; fetch approved operational finance metadata.
   Model fields are exact assertions only. Unknown/conflicting execution keys
   fail closed. Amount, currency, financial identities and targets come from the
   canonical record. Live metadata mapping remains D03; never fall back to prose.
2. Bind the reviewed digest to canonical bill, exact payload and registered
   workflow definition. Use the process PrincipalAuthority execute capability and
   always obtain fresh ApprovalProvider authorization, then re-fetch/reverify and
   require the same digest. Secret refs are canonical and may populate credential
   slots only. Model-facing direct finance dispatch and generic financial workflow
   calls (including aliases) cannot bypass this path.
3. Reuse the verify capability for an explicitly authorized human financial-state
   check. All finance creates/updates require independent human verification of
   the exact record/write because free text cannot reliably distinguish payment
   state from benign edits. Detected paid claims/evidence elsewhere also require
   it. Revalidate the target/current asset after the prompt and audit a bound write
   fingerprint plus provenance. Payload booleans/status/provider names grant nothing.
4. No automatic completion evidence is added: webhook acceptance and portal
   login cannot mark paid or resolve Matters. An eventual trusted payment executor/
   verification integration needs separate design. Act & Verify, existing proposals,
   Telegram/ChatGPT capability sets and persistent schemas are unchanged.

### Consequences

Old model-supplied bills, numeric/string substitutions, arbitrary parameters and
generic payment workflow calls can now be refused. Only exact canonical assertions
are accepted; caller values never supply dispatch fields. Direct `trigger_payment`
is no longer a public FinanceProvider operation; the adapter's private transport
helper is reached by authenticated runtime review. Old session text/evidence does
not become authorization. No records are migrated or credentials/config changed.

The stricter path is unavailable without canonical live metadata. Deployed n8n
effects/completion remain unknown (D08); this fix does not certify a payment flow.
Legacy ambiguous outcomes still require operator review and must not be retried
automatically. Financial human confirmation is not a new remote permission.

### Validation and review trigger

Synthetic regressions exercise the real registry/router/safety abstractions for
overrides, post-prompt drift, workflow aliases, secret collisions, forged/foreign/
restricted contexts, human refusal/verification, dry-run and secret redaction.
Baseline probes reproduce the original flaws. Revisit for a trusted completion
provider, new consequential executor or migration to the operational lifecycle.

---

```markdown
## ADR-NNN: Title

**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-NNN
**Date:** YYYY-MM-DD

### Context
What is the issue that we're seeing that is motivating this decision?

### Decision
What is the change that we're proposing and/or doing?

### Consequences
What becomes easier or more difficult because of this change?

### Alternatives considered
What other options were evaluated and why were they rejected?

### Review trigger (optional)
When should this decision be revisited?
```

## ADR-044: Certified financial identities and local owner certification

**Status:** Accepted and implemented, 6 October 2026. Owner-approved D03 decisions.
Live source designation, document validation, certification and auth-only acceptance
remain a separately authorized pilot. See [catalog operations](finance-catalog.md).

### Context

Generic knowledge metadata and fixture-only portal configuration cannot establish
financial identity or reviewed authority. An approved operational classification
alone is insufficient. Recurring Bills must also represent irregular,
statement-driven and one-off streams while preserving its database/pages.

### Decision

Wally owns immutable IDs for subjects/properties, providers, accounts, obligation
definitions and occurrences. Source database/data-source/page IDs are provenance.
Typed stable-property mappings, complete pagination/relation reads, deterministic
normalization and duplicate detection register provisional candidates. Only the
certified projection establishes canonical financial identity. Existing Notion
rows remain provisional; additive schema changes never certify or rewrite them.

Introduce `CERTIFY_FINANCIAL_DATA` for the authenticated local owner on CLI/REPL.
No remote adapter or conversational tool gains this authority. Certification and
chain enablement require fresh confirmation of the exact candidate/version,
evidence and dependency bindings, followed by source/dependency rereads. Persist
certificates, immutable revisions, provenance, timestamp, revocation/invalidation
history and chain enablement in additive operational tables. Material changes,
missing/partial sources and dependency changes invalidate readiness; reverting
facts never resurrects invalidated certificates.

Separate identity/tracking from portal review. Reviewed runtime profiles own
selectors, exact origins, supported intent and auth conditions. Certified provider
and account records supply URL and secret references only. Successful governed
auth-only acceptance of the exact portal configuration precedes portal enablement.
Approval remains inert; execution needs a separate authenticated capability check,
fresh prompt and canonical revalidation before secret resolution.

Manually validated primary evidence creates expected or issued instances using a
stable occurrence discriminator. Expected instances contain no amount or invoice
facts. Issued facts use exact decimal money and explicit amount basis. Corrections
retain occurrence IDs, append revisions and explicitly replace invoice references.
Email threads/amount similarity/receipts do not establish occurrence identity.
Receipts remain FYI even when a payload claims owner confirmation.

General knowledge serialization masks restricted financial sources. Customer IDs
stay in the restricted typed catalog; credential values remain exclusively in the
existing SecretsProvider. Technical Notion prose and Runtime Action fields never
choose privileged behavior. D03 introduces no payment executor or generic payable
payload; D01/D02 checks remain independently required on the legacy path.

### Consequences

All seven gates—classification, explicit designation, known kind, typed mapping,
current certification, certified dependencies and chain enablement—must pass.
Generic metadata no longer drives obligation observations or portal review plans.
Existing fixture coverage is migrated to real certified catalog fixtures and
Notion-shaped transport tests. Full safe tests and adversarial review are required.
Finance revisions address the applicable part of D07; calendar debt remains.
Snapshot reads cannot prove remote transactional immutability or document truth.
The owner must validate one live chain before operational readiness is claimed.

---

## ADR-045: Interface-neutral approval, centralized authorization

**11 October extension:** [ADR-046](#adr-046-telegram-authorizes-eligible-scoped-notion-edits)
makes Telegram the default owner interaction for eligible metadata edits. Separate
decision/execution confirmations remain, but share one exact button interaction.
The native path remains dormant and hosted ChatGPT restrictions remain unchanged.

**Status:** Accepted — implemented default-disabled scoped metadata slice

**Date:** 2026-10-10

**Deciders:** Project owner request + implementation review

### Context

The owner should be able to review Notion corrections in ChatGPT or Codex without
using Terminal. Existing proposal decisions and PrincipalAuthority can be reused,
but an authenticated agent call or a pinned subject does not prove that a human
approved the exact change. ADR-040's operator confirmation flag is insufficient
as per-action evidence. Financial certification and uncertain execution outcomes
must remain distinct from record editing.

### Decision

1. **Approvals are interface-neutral; authorization is centralized in Wally.**
   Extend the operational proposal lifecycle with reference-only
   `edit_notion_record` advice and separate immutable reviewed specifications.
   Existing proposal rows remain the decision of record; there is no second
   approval inbox or external decision store.
2. **Action-specific human interaction is a separate authority requirement.**
   PrincipalAuthority constructs an immutable exact review, calls a trusted
   registered provider, and issues a process-local single-use confirmation.
   Bind owner, purpose, context, selected IDs/fingerprints, target and old/new values,
   expiry and nonce. Persist attribution only. An interface/tool cannot submit a
   boolean, model assertion or self-issued receipt as human authorization.
3. **Approval remains inert.** Scoped batches may approve/reject/defer individual
   exact versions, leaving omitted proposals pending. Revalidate after the prompt
   and commit the selected batch atomically. Execution is a separate authenticated
   request with another fresh review; verification is an independent provider read.
4. **Use narrow capabilities.** `EXECUTE_NOTION_EDIT` and `VERIFY_NOTION_EDIT`
   grant this metadata workflow without granting broad proposal execution,
   financial verification/certification, payments or portal access. Initially
   permit only existing select options for amount policy and frequency.
5. **Fail closed for hosted ChatGPT.** This supersedes ADR-040's opt-in flag-based
   decision path: even a configured confirmation flag and pinned subject cannot
   enable `record_decision`. No verified hosted human attestation exists in the
   inspected integration. Keep hosted decisions and execution/verification disabled.
6. **Implement an opt-in local Codex path.** A restricted stdio MCP adapter invokes
   the same Gateway/service. A pinned macOS native helper presents the exact review
   and requires fresh biometrics. Its owner UID/enrollment binding and installed
   client behavior need operator validation. This is a local owner trust model,
   not protection against arbitrary same-owner code. The foundation installed no
   helper or connection. The later isolated rollout installs a signed/pinned helper
   and stdio configuration but has not validated biometric approval or live writes;
   see the [dated rollout record](local-codex-rollout.md). No listener is exposed.
7. **Constrain writes and uncertainty.** Re-read classification, source/schema,
   material state, approved version, Matter and target policy after confirmation
   and after action credential acquisition. Atomically claim proposal and normalized
   page identity before attempting a write. Never retry an uncertain outcome,
   including by substituting a new proposal. Report success only after comparing
   expected edits and all protected business properties. Ignore only system audit
   metadata and explicitly registered nonbusiness `Audit History` text.
8. **Certification is independent.** Invalidate canonical financial certification
   before potentially material writes; never certify from update or verification.
   Preserve history and existing dependency checks. Receipts remain FYI, and no
   obligation, invoice, payment chain or scheduler is created/enabled.
9. **Default disabled and additive.** New edit/review/claim tables share the
   operational database; never reset state. Empty targets, no confirmer, and writes
   off ship in a new reviewed policy. Existing Terminal/Telegram behavior remains;
   Telegram gets no new execution/verification authority. Live rollout requires
   demonstrated confirmation, controlled external writers and reviewed recovery.

### Consequences

The shared service and local stdio interaction are exercised with synthetic Notion
records; the Swift helper type-checks and its Python boundary has failure tests.
Actual biometric enrollment/UX, Codex MCP installation and live provider behavior
remain unvalidated. Hosted confirmation and broader edits remain unimplemented.
Notion's inspected PATCH contract supplies no demonstrated atomic conditional
write: immediate semantic checks cannot fence external writers. Keep writes off
until a controlled writer/access policy or adequate provider concurrency is proven.

See the [implementation and rollout guide](interface-neutral-approvals.md) for exact
component boundaries, investigated platform mechanisms and remaining limitations.

### Review trigger

A trustworthy hosted per-action attestation, a new property/type or payment-related
capability, a different authentication method, external concurrent writers, or a
manual uncertain-attempt recovery API requires review and updated rationale.

### Local rollout extension, 10 October 2026

Separate launch-time policy/state paths keep installation and isolated acceptance
out of production state. A runtime status read reports only registered scope and
the authority-issued principal. Read-only inspection may reconcile RUNNING state
but cannot unlock it, mark it complete or authorize retries. The native helper
independently verifies the full canonical review digest before displaying its bound
scope. Known pre-PATCH failures are distinguished from ambiguous dispatched writes;
conservative certification invalidation remains. These operational extensions add
no interface capability or automatic recovery authority. Actual owner hardware,
Desktop discovery and isolated Notion acceptance remain rollout prerequisites.

---

## ADR-046: Telegram authorizes eligible scoped Notion edits

**Status:** Accepted — implemented default-disabled; live owner acceptance pending

**Date:** 2026-10-11

**Deciders:** Project owner's explicit Telegram-first architecture request

### Context

Routine registered metadata corrections should be reviewed on the owner's phone or
Mac in Telegram without Terminal or biometrics. The existing exact-review, principal,
proposal, scoped executor and independent verifier already supply runtime boundaries.
This is an intentional, bounded extension of ADR-041/045, not a broad Telegram
execution grant. Telegram account identity is weaker than biometric presence.

### Decision

1. Telegram is the default review channel for explicitly allowlisted existing
   `amount_policy` and `frequency` select changes, independent of proposal origin.
   A complete immutable card presents record/page, property IDs, old/new values,
   version and expiry. Oversized scopes split into complete individual cards or
   fail closed. Individual and full-scope batch buttons support partial approval.
2. Approve & Execute is one human interaction that authorizes separate decision
   and execution stages. Ordinary approval is inert. Existing PrincipalAuthority
   issues/consumes distinct exact-purpose confirmations; an in-process provider
   accepts only presentations bound to the authenticated callback. Model text,
   persisted provenance or copied receipts grant no authority.
3. Authenticate owner/private chat through the trusted Bot API polling transport;
   validate server nonce, recorded delivered message, immutable scope, allowed action
   and expiry. Durably claim a review before effects. Store owner ID, selected scope,
   action, callback ID, timestamp, expiry and outcome as attribution. Restart never
   reconstructs executable authority. A fresh press is necessary for an unstarted
   write; any existing execution attempt blocks another PATCH.
4. Telegram's capability set stays unchanged. An internal worker with no exposed
   adapter registration holds only read/scoped Notion execute/verify. Runtime policy
   is reloaded before each action and after credential resolution. The separate
   Telegram gate and explicit target subset do not enable the dormant local gate.
5. Reuse existing immutable proposals, atomic decisions/page claims, source drift
   checks, minimal PATCH, independent semantic verification and certification
   invalidation. No generic executor, payment, registration, certification,
   secret/security/destructive or unregistered operation qualifies.
6. Rejection/defer are actual proposal decisions. Deferral releases to pending,
   requiring another review. Consumed/stale/duplicate/concurrent callbacks cannot
   repeat execution. Poll leases renew during slow work and lost ownership fences
   scoped dispatch. Processed update replay repairs the cursor. Delivery remains
   at-least-once, with durable dedupe where remote acceptance is known.
7. Uncertain/interrupted/pre-dispatch failures remain explicit and retain record
   claims. Read-only inspection and independent re-verification never repeat PATCH.
   RUNNING requires operator process-liveness reconciliation. No automatic recovery
   write, lock clearing, certification restoration or obligation resolution is added.
8. Codex defaults to proposal/read/reconciliation tools with Telegram handoff using
   shared operations/registry/policy paths. Preserve the native seven-tool path,
   helper and tests as opt-in dormant infrastructure. Hosted ChatGPT stays restricted;
   its shipped HTTP composition still lacks the edit service.
9. Ship empty registrations and both gates false. Sandbox creation needs explicit
   owner approval; actual button/PATCH/GET acceptance remains a live deployment gate.
   Never use the four Phase 6 records or staged September invoice for testing.

### Consequences and limitations

The eligible UX needs no Terminal, Touch ID or password. Telegram account takeover,
accidental presses and bot-token compromise can authorize eligible actions, so this
mechanism cannot satisfy stronger human-presence requirements. Bot cloud chats are
not end-to-end encrypted; cards omit secrets and unnecessary financial information.
The Mac must remain awake, logged in and connected; no hosting/scheduler is added.

Wally cannot prevent external Notion writers or arbitrary same-owner code from
bypassing it. GET/PATCH has no demonstrated atomic compare-and-set. Production needs
validated effective permissions on a dedicated managed subset, a dedicated writer,
read-only other connections/users and an accepted runtime trust model. Ordinary
Notion use elsewhere can remain unrestricted. No permission change is made here.
See [implementation, UX, recovery and acceptance](telegram-notion-approvals.md).
