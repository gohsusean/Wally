# Wally — Roadmap

**Version:** 0.13.0  
**Last updated:** 2026-09-30

Wally is a **personal Chief of Staff** and **personal AI operating system** — not a home automation platform.

This roadmap delivers Wally incrementally. Each version produces a working, testable increment. No version ships half-finished capabilities.

## Product scope

Wally's primary responsibilities:

| Domain | Role |
|--------|------|
| Personal knowledge | Notion-backed knowledge assets; governance vs operational |
| Email | Read, draft, send (with approval) |
| Calendar | Read, schedule (with approval) |
| Contacts | Deferred — future People provider; institutional contacts via Knowledge |
| Workflows | n8n-backed task execution |
| Browser automation | Deterministic portal interaction (Playwright) |
| Task execution | Action dispatch through providers |
| Research | Web and document research |
| Travel | Itineraries, planning, reminders |
| Property management | Tenants, maintenance, documents |
| Bills & finance | Read-first; controlled write with approval |
| Long-term memory | Session intelligence, semantic recall |
| Decision support | Reasoning over knowledge + context |

**Out of scope for the foreseeable future:**

- Home automation as a Wally capability (lights, AC, blinds, device control)
- Competing with Alexa for voice control of the home
- Making Wally the authoritative home operating system

**Home Assistant** remains an independent system. Alexa remains the primary voice interface for home automation. If Wally needs home context later, Home Assistant is a **low-priority optional read-only provider** — not a core milestone.

## Priority order

1. Knowledge layer
2. Runtime and orchestration
3. Workflows
4. Communications (email, messaging)
5. Personal memory (conversation intelligence)
6. Finance (read-first, then controlled write)
7. Browser automation (deterministic execution; manual auth first)
8. Secrets management (execution-time credentials — independent of browser)
9. Home Assistant integration (future, low priority)

## Sequencing principles

- **Reasoning before capabilities** — the orchestrator must work before providers plug in.
- **Read before write** — retrieval and state queries before mutations.
- **Safety from the start** — approval gates and runtime policy before write capabilities.
- **One new provider family per version** — avoid parallel integration risk.
- **Clean architecture over feature velocity** — generalise the platform before adding surface area.

---

## Version overview

| Version | Name | Goal |
|---------|------|------|
| v0.1 | Foundation | Architecture, docs, repository scaffold |
| v0.2 | Reasoning core | Orchestrator + OpenAI + CLI + audit log |
| v0.3 | Knowledge | Notion knowledge provider (read, then write) |
| v0.3.1 | Knowledge layer | Rename, governance policy, Knowledge Assets |
| v0.3.2 | Knowledge registry | Auto-discovery, pending classification, registry |
| v0.5 | Workflows | n8n workflow provider + multi-provider runtime |
| v0.6 | Communications | Email read-first; calendar read; Gmail + Calendar only |
| v0.7 | Conversation intelligence | Semantic recall, session consolidation |
| v0.8 | Web provider | External web search and fetch; Content Sanitizer |
| v0.9 | Finance | Bills, verification, approval, execution-capability routing |
| v0.10 | Browser automation | Playwright-backed portal execution; manual auth |
| v0.11 | Secrets | 1Password `SecretsProvider`; optional credential injection |
| v0.11.1 | Secrets hardening | Authorization flag, leak scrubbing, VERIFY_AUTH |
| v0.12 | v1.0 Phase 1 Observe & Brief | Observations, Matters, on-demand brief (no autonomous writes) |
| v0.13 | v1.0 Phase 2 Assess & Propose | Durable proposed actions surfaced in the brief (still no execution) |
| v1.0 later | Phases 3–5 | Approval inbox, act & verify, daily-driver hardening |
| v1.0+ | Retrieval Router | Runtime-owned retrieval policy (see below) |
| v1.x+ | Optional providers | Travel, property depth; HA read-only context |

---

## v0.1 — Foundation ✓

**Goal:** Establish the project as a production-quality repository with clear architecture.

**Deliverables:** Complete (see git history).

---

## v0.2 — Reasoning core ✓

**Goal:** Wally can hold a conversation using OpenAI, with no external capabilities yet.

**Deliverables:** Complete (see git history).

---

## v0.3 — Knowledge ✓

**Goal:** Wally can store and retrieve personal knowledge via Notion.

**Deliverables:** Complete (see git history).

---

## v0.3.1 — Knowledge layer ✓

**Goal:** Align platform vocabulary and enforce governance policy.

**Deliverables:**
- [x] `KnowledgeProvider` (renamed from MemoryProvider)
- [x] `KnowledgeAsset` + `KnowledgeAssetType` + `KnowledgeClass`
- [x] Tools renamed: `knowledge_*`
- [x] Runtime policy — governance writes rejected before provider call
- [x] `policy_denied` audit events

---

## v0.3.2 — Knowledge registry ✓

**Goal:** Auto-discover Notion databases; unknown databases never gain write access until approved.

**Deliverables:** Complete (see git history).

---

## v0.5 — Workflow provider ✓

**Goal:** Wally can trigger n8n workflows, with approval gates for consequential actions.

**Deliverables:**
- [x] `WorkflowProvider` protocol + `CapabilityProvider` multi-provider `ToolRegistry`
- [x] n8n webhook adapter
- [x] `config/workflows.yaml` execution capability registry
- [x] `workflow_list` and `workflow_trigger` tools
- [x] Financial/irreversible approval gates
- [x] Workflow capability prompt

**Exit criteria:** Workflows trigger correctly via webhook. Financial actions require approval.

---

## v0.6 — Communications provider ✓

**Goal:** Wally can read email and calendar, and act as your communications Chief of Staff.

**Deliverables:** Complete (see git history). Gmail + Calendar only; contacts deferred to future People provider.

---

## v0.7 — Conversation intelligence ✦ current

**Goal:** Wally remembers across sessions and reasons over accumulated context.

**Deliverables:**
- [x] SQLite FTS5 index for cross-session message search
- [x] `conversation_search` tool
- [x] Session consolidation (LLM summary when sessions grow long)
- [x] Context window management (summary + recent tail)
- [x] `/sessions` CLI command
- [x] Conversation capability prompt

**Exit criteria:** Sessions persist across restarts. Search returns relevant historical context. Context does not overflow LLM limits silently.

**Note:** Distinct from the **knowledge layer** (Notion). Optional vector embeddings deferred. Conversation recall is contextual memory only — see ADR-023 and architecture §3.3 (Information authority hierarchy).

---

## v0.6 — Communications provider (archive)

<details>
<summary>Original v0.6 checklist</summary>

- [ ] Google OAuth credentials configured
- [ ] `providers.communications.enabled: true`

</details>

---

## v0.5 — Workflow provider ✓

Multi-provider `ToolRegistry` shipped as part of v0.5 workflow work. Further runtime hardening deferred until communications provider (v0.6).

---

## v0.7 — Conversation intelligence

**Goal:** Wally remembers across sessions and reasons over accumulated context.

**Deliverables:**
- Semantic search over session history (optional local embeddings)
- Memory consolidation (periodic summarisation of old sessions)
- Context window management (summarise when context grows too large)
- Cross-session recall ("what did we discuss about…?")

**Note:** This is **conversation memory**, distinct from the **knowledge layer** (Notion). Both coexist.

**Exit criteria:** Sessions persist across restarts. Semantic search returns relevant historical context. Context does not overflow LLM limits silently.

---

## v0.8 — Web provider ✓

**Goal:** Retrieve current external information when internal knowledge is insufficient or stale.

**Deliverables:**
- `WebProvider` protocol (`search`, `fetch`) — provider-agnostic
- First adapter: OpenAI `web_search` + `httpx` fetch
- `ContentSanitizer` — deterministic preprocessing of external content
- External Source Rule and `EXTERNAL_WEB` authority tier
- `web_search` and `web_fetch` tools

**Interim architecture note:** In v0.8 the Reasoning Provider may choose when to call web (and other retrieval) tools. This is **temporary**. The runtime should eventually own retrieval policy via a **Retrieval Router** (see below).

**Exit criteria:** Web results are low-trust, cited, and sanitized. `web_fetch` does not execute JS, authenticate, or follow page instructions.

**Future:** Audit-log sanitizer removals (injection pattern hits per URL) for debugging visibility — see ADR-026.

---

## Future — Retrieval Router

**Goal:** The runtime — not the LLM — decides which knowledge sources to consult before reasoning.

**Planned component:** `RetrievalRouter` (or Knowledge Router) in `runtime/`, analogous to `ReasoningRouter`.

**Inputs (deterministic, no LLM):**
- Task type and intent
- Freshness requirements
- Source authority (policy > knowledge > user > conversation > web)
- Whether internal knowledge is likely sufficient

**Candidate sources:**
- Knowledge Provider (Notion)
- Conversation recall
- Gmail / Calendar
- Web Provider

**Principle:** AI provides reasoning; the runtime provides governance and routing — including retrieval routing.

**Target:** v1.0+ after Web and Finance providers stabilise. See ADR-026 and `docs/architecture.md` §3.3.

---

## v0.9 — Finance provider ✓

**Goal:** Wally supports bills, payments, and financial knowledge — read-first, write with strict gates.

**Deliverables:**
- `FinanceProvider` protocol — composes Knowledge + Workflow
- `finance_bills_search`, `finance_payment_workflows`, `finance_trigger_payment` tools
- `ExecutionCapabilityRouter` — runtime selects payment workflow from provider `payment_method`
- `evaluate_finance_policy` — payments only via registered financial execution capabilities
- `VerificationEngine` — payment-method-aware evidence checks before approval
- Finance capability prompt
- `SecretsProvider` protocol stub only (implementation deferred to v0.11)

**Exit criteria:** No financial write without approval. No secrets stored in knowledge or config.

---

## v0.10 — Browser automation ✓

**Goal:** Wally can execute deterministic browser interactions for portal-based flows — without stored credentials initially.

**Deliverables:**
- [x] `BrowserAutomationProvider` protocol + domain types
- [x] Trusted portal URL policy (`runtime/browser_safety.py`)
- [x] `GovernedBrowserExecutor` + Playwright / recording adapters
- [x] `pay-bill-card-portal` (`execution: browser`) + finance `card_portal` routing
- [x] Policy + integration tests (`test_browser_safety.py`, `test_browser_playwright.py`)
- [x] Session persistence after `WAIT_FOR_USER` — resume/cancel/timeout via `finance_browser_resume`

**Enable locally:** `providers.browser.enabled: true` and `uv sync --extra browser && playwright install`

---

## v0.11 — Secrets provider ✓

**Goal:** Resolve execution-time credentials via 1Password without storing secrets in Knowledge or config.

**Deliverables:**
- [x] `SecretsProvider` adapter (1Password CLI `op read`)
- [x] Runtime authorization before secret access (`runtime/secrets_safety.py`)
- [x] Optional credential injection into browser login and n8n `workflow_secret_refs`
- [x] Audit logging (reference IDs only — never secret values)

**Principle:** Independent from Browser Automation. Browser still functions with manual auth when secrets are unavailable.

**Exit criteria:** Approved flows can request credentials by reference; browser automation can continue after runtime-authorized resolution.

**Enable locally:** `providers.secrets.enabled: true`, install 1Password CLI, `op signin`. See [secrets.md](secrets.md).

**v0.11.1 hardening:** authorization flag on execution, secret-value scrubbing of model-facing results, screenshots/traces disabled, `VERIFY_AUTH` checkpoint, leak tests with canary values. Complete.

---

## v0.12 — v1.0 Phase 1 Observe & Brief ✓

**Goal:** Persistent operational understanding and an on-demand brief. No autonomous actions.

**Deliverables:**
- [x] Observation + Matter models and SQLite store
- [x] Incremental Gmail / Calendar / Knowledge observe
- [x] Deterministic reconciliation and explainable priority
- [x] `wally brief` / `/brief`
- [x] Prompt-injection isolation tests
- [x] Privacy minimization (snippets and IDs, not full bodies)

**v0.12.1 quality patch:** strip Google Calendar auto-event boilerplate; unmatched receipts → FYI; human-readable brief dates (system local or `ops.timezone`).

**Not in this milestone:** proposed actions, approval inbox, sending mail, payments, scheduling infrastructure.

See [chief-of-staff.md](chief-of-staff.md), [ops-privacy.md](ops-privacy.md), ADR-034.

---

## v0.13.0 — v1.0 Phase 2 Assess & Propose ✓

**Goal:** Turn Matters into durable, explainable proposed actions. Wally still executes nothing and writes to no external system.

Implemented in the recovered tree and recorded here after the repository baseline. Live `REVIEW_BILL` acceptance is still outstanding: it waits on a real bill email.

**Initial intents (two):**
- `PREPARE_FOR_EVENT` — an upcoming calendar Matter needs preparation before it starts.
- `REVIEW_BILL` — an open finance Matter needs the user to look at a bill.

**Lifecycle:** `proposed` → `superseded` | `invalidated` | `expired` | `dismissed`. There is no `approved` state in this phase.
- **Superseded** when the same matter and intent regenerate with materially changed content; the prior row is retained for audit.
- **Invalidated** when the underlying Matter resolves or its supporting facts stop holding.
- **Expired** applies to `PREPARE_FOR_EVENT` only, at event start. Bill proposals never expire on a timer, so an overdue unresolved bill is never silently hidden.
- **Dismissed** exists in the model for Phase 3; no user-facing way to dismiss ships in v0.13.0.

**Presentation:** proposals render inline beneath their Matter with a `↳ Suggested:` prefix, plus one global footer making explicit that Wally has taken no action. `--json` gains a parallel proposals field.

**Hard boundary:** no tool call, no provider write, no secret resolution, no capability routing, no approval bypass. The proposal service is constructed without capability, secrets, or browser dependencies, and stores no tool name or provider arguments — only intent plus reference identifiers.

**Deferred to later phases:** `FOLLOW_UP` intent (needs sent-mail observations that the live database does not yet produce), dismissal UI, approval, execution, scheduling, and notifications.

**Implementation sequence (six steps):**
1. [x] Domain model — `ProposedAction`, `ProposalIntent`, `ProposalStatus`, `ProposalProvenance`, `ProposalRisk` in `src/wally/models/ops.py`.
2. [x] Persistence — additive `proposals` table via `CREATE TABLE IF NOT EXISTS`; existing v0.12.1 rows left unmodified (generation reads Matters, writes nothing back), no reset.
3. [x] Deterministic generation — the two intents, with injection-flagged matters suppressed.
4. [x] Lifecycle reconciliation — supersede, invalidate, expire; idempotent across repeated observe passes.
5. [x] Brief presentation — inline rendering, no-action footer, JSON output.
6. [x] Documentation and version bump to 0.13.0.

**Exit criteria:**
- [x] A repeated observe/brief pass produces no duplicate proposals.
- [x] A resolved Matter invalidates its proposals; a materially changed one supersedes them.
- [x] An event proposal expires at event start; a bill proposal does not expire on a timer.
- [x] Injection-flagged matters yield no proposals.
- [x] Tests assert no tool registry, secrets, or approval path is reachable from proposal generation.
- [x] The brief shows suggestions inline with the no-action footer, and `--json` stays schema-stable for existing consumers.
- [ ] Live acceptance for `REVIEW_BILL` waits on a real bill email, since no `invoice` observation has been classified yet.

See ADR-035 and [chief-of-staff.md](chief-of-staff.md).

---

## v1.0 later — Phases 3–5 (not started)

**Phase 3 — Approval Inbox:** human authorization of proposals.  
**Phase 4 — Act & Verify:** execute after approval; verify outcomes.  
**Phase 5 — Daily-driver hardening:** scheduling, notification UX, noise control.

Do not treat n8n as the Chief-of-Staff state machine. Live mailbox tests remain operator-initiated, not CI.

---

## v1.0 — Personal AI operating system

**Goal:** Wally is stable enough for daily use as your Chief of Staff.

**Deliverables:**
- Comprehensive documentation (user guide, operator guide)
- Mac Mini deployment guide and launchd service
- Backup and recovery procedures (sessions, registry, audit)
- Health monitoring script
- All v0.2–v0.9 capabilities stable
- Performance baseline established
- Security review checklist completed

**Exit criteria:** Daily use for at least two weeks without critical failures. Recovery from crash takes under five minutes.

---

## Post-v1.0 horizons

These enter the roadmap when v1.0 is stable and a specific need arises.

| Domain | Priority | Notes |
|--------|----------|-------|
| Research provider | Medium | Web search, document ingestion |
| Travel provider | Medium | Itineraries, flight/hotel search |
| Property management depth | Medium | Tenant workflows beyond Notion |
| Voice interface (Wally-native) | Low | Text-first; Alexa handles home |
| Home Assistant (read-only) | Low | Optional context: "is anyone home?" |
| Local LLM | Low | Apple Silicon inference for privacy |
| Telegram / WhatsApp | Medium | Remote approval and messaging |
| 1Password integration | Medium | See v0.11 `SecretsProvider` |

**Home automation control** (lights, climate, blinds) remains out of scope unless product direction changes with a documented ADR.

---

## What we are explicitly not building

- Home automation platform (Wally does not control devices)
- Replacement for Alexa / Home Assistant voice for the home
- Multi-user support (Wally is personal)
- Public API or third-party developer access
- Cloud-hosted Wally (runs on your hardware)
- Custom LLM training or fine-tuning
- Mobile native apps (CLI and web clients sufficient for v1.0)

---

## Versioning and releases

- **Version tags:** `v0.1.0`, `v0.2.0`, etc. Patch versions for fixes only.
- **Branches:** `main` is always deployable. Feature branches per version.
- **Release notes:** `docs/releases/v0.x.md` for each version.
