# Wally — Roadmap

**Version:** 0.18.0  
**Last updated:** 2026-10-04

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
| v0.14 | v1.0 Phase 3 Approval Inbox | Durable review: approve, reject, or defer. Approval is not execution |
| v0.15 | v1.0 Phase 4 Act & Verify | User-requested execution of the exact approved version, then independent verification |
| v0.16 | Gateway | Local trust boundary so a future interface can call the runtime without choosing its own channel |
| v1.0 later | Phase 5 | Scheduling, notifications, daily-driver hardening |
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

## v0.7 — Conversation intelligence ✓

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

## v0.7 — Conversation intelligence (original plan)

The implemented milestone is the checked v0.7 section above. This is the original wording. It is not the current milestone.

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

## v0.14.0 — v1.0 Phase 3 Approval Inbox ✓

**Goal:** Make a ProposedAction durable, reviewable, and explicitly approvable, rejectable, or deferrable. Approval records authorization intent only. It does not send, pay, submit, or write.

**Lifecycle:** `proposed` → `approved` | `rejected` | `deferred`, and the existing system closures `superseded` | `invalidated` | `expired` | `dismissed`.

- **Approved** stores the user decision, its timestamp, origin (`user_cli` or `user_repl`), optional note, and the fingerprint of the version that was reviewed. It does not call a tool.
- **Rejected** stays rejected for that fingerprint. The same evidence does not recreate it.
- **Deferred** leaves the decision queue until `defer_until`, then returns to `proposed`.
- **Material change** (including a normalized bill amount on `observation.extra["amount"]`) supersedes an approval. The old row keeps its decision. The new row is `proposed`.
- **Matter resolution** invalidates an open proposal, including one already approved. Reopening the matter with the same facts restores `proposed` and clears the decision.
- **Dismissed** remains a terminal status. v0.14 does not add a separate dismiss command; reject is the user refusal.

**Commands:** `wally approvals`, `wally approve`, `wally reject`, `wally defer --until`. REPL: `/approvals`, `/approve`, `/reject`, `/defer`.

**Boundary:** `execution_allowed` always returns false. Orchestrator, runtime, and adapters do not read proposal status. n8n does not own approval. Secret references are not resolved.

**Not in this milestone:** executing an approved proposal, scheduling, notifications.

See ADR-036 and [chief-of-staff.md](chief-of-staff.md).

---

## v0.15.0 — v1.0 Phase 4 Act & Verify ✓

**Goal:** Execute an approved proposal only on an explicit user request, only for the exact version that was approved, and report what an independent check confirms.

- **Entry points:** `wally execute <proposal-id>` and `/execute` issue a request context and call the same `ActVerifyService`. No scheduler, Observe pass, brief, inbox, model output, or source content can start execution. A channel name in text is not authentication.
- **Pipeline:**
  1. Load the proposal.
  2. Require `approved`, a trusted decision origin, and `decision_fingerprint == fingerprint`.
  3. Recompute the proposal from current evidence and require the same fingerprint, an open Matter, and no expiry.
  4. Require a supported intent.
  5. Build a typed plan from trusted Knowledge.
  6. Run `ApprovalGate`, then always prompt through `ApprovalProvider`.
  7. Recheck everything, including the plan digest.
  8. Resolve secrets, then run the executor.
  9. Verify.
  10. Persist.
- **Supported:** `REVIEW_BILL` backed by one trusted Knowledge asset. It logs in to the trusted portal, checks the login, and stops. It never pays; there is no PAY_BILL intent.
- **Unsupported:** `PREPARE_FOR_EVENT` and email-only bills stay inert with "Approved, but execution is not supported yet."
- **Records:** an `executions` table (additive migration) with the statuses `pending`, `preflight_failed`, `authorization_denied`, `running`, `executed_unverified`, `verified_success`, `verified_failure`, and `failed`. There is one in-flight or verified attempt per approved fingerprint. Uncertain attempts block retries until reviewed.
- **Verification:** only an explicit authenticated result counts. Otherwise Wally reports "Executed, but verification could not confirm completion." `wally verify` is read-only; `--confirm success|failure` records your own check. A verified review never resolves the Matter.
- **Commands:** `wally execute`, `wally executions`, `wally execution`, and `wally verify`, plus the matching REPL slash commands.

**Not in this milestone:** scheduling or proactive triggers, notifications, payment execution, and other action types.

See ADR-037, ADR-038, and [chief-of-staff.md](chief-of-staff.md).

---

## v0.16.0 — Gateway ✓

**Goal:** One local boundary a future external interface can call, without letting that interface mint a principal, choose a channel, or keep a second copy of Wally's state.

- **Trust path:** external client → channel-specific adapter / authenticated ingress → Gateway → runtime. The adapter registration fixes the channel. Payload fields named `channel`, `principal`, `grant`, `capability`, `capabilities`, or `authentication` are rejected.
- **Issuer:** only `PrincipalAuthority.issue()` inside the runtime. The HMAC key is not sent to the Gateway client. CLI and REPL stay in-process and are not Gateway adapters.
- **Socket:** a local Unix socket, line-delimited JSON, started only by an explicit server. `create_app` attaches a Gateway with no adapters and does not listen.
- **Requests:** `submit_request` mints a new correlation id unless the caller continues one that already exists. A request may also link to an ActiveMatter.
- **Evidence:** up to eight typed items (`latest_user`, `prior_user`, `assistant_summary`, `external_ref`), each length-capped and untrusted. They do not approve, select an executor, inject a secret, claim verification, or change a proposal fingerprint.
- **Reads and actions:** `get_context`, `list_proposals`, `list_active`, and `lifecycle` return ids and statuses. `decide`, `execute`, and `verify` call the v0.15 services. Execute fails closed when the adapter has no approval adapter, before those services run.
- **Continuity:** an ActiveMatter is a cross-interface handle. It may hold many correlation ids and many `(channel, external_session_ref)` links. `active` / `archived` says whether Wally should keep offering the handle. The canonical Matter status stays authoritative.
- **Audit:** adapter, channel, principal, correlation id, operation, and evidence hashes. No evidence text, grant, or credential.

**Not in this milestone:** ChatGPT, MCP, Telegram, Home Assistant, a public network API, remote approval UX, scheduling, notifications, transcript sync, and a generic tool interface.

See ADR-039.

---

## v0.17.0 — ChatGPT Interface — implemented ✓

**Goal:** ChatGPT is the primary ad-hoc interface. It asks what needs attention, continues an ActiveMatter, and submits work. Wally builds the canonical proposal. A decision is recorded only for an authenticated owner, on an exact proposal fingerprint, when the host is known to confirm that call.

- **Path:** ChatGPT MCP client → localhost adapter → Gateway → existing Observe / proposal / decide services. The registration fixes the channel at `chatgpt`. The model cannot set channel, principal, grant, subject, or owner.
- **Authentication:** `POST /oauth/token` on `127.0.0.1` accepts the owner secret plus a PKCE S256 verifier and returns a random bearer. `tools/list` and `tools/call` require that bearer. A subject string does not mint it. The Gateway credential is a separate secret. The HMAC key stays in `PrincipalAuthority`.
- **Subject pin:** `_meta["openai/subject"]` must be in the configured allowlist before `record_decision` runs. An empty allowlist leaves that tool unregistered. The subject is audit metadata, not a fingerprint field.
- **Confirmation:** `record_decision` is registered only when `WALLY_CHATGPT_DECISION_CONFIRMATION` is set, together with writes, both secrets, and a non-empty subject allowlist. The default is off. The tool is annotated `destructiveHint=true` so the host treats it as a consequential write. The handler still checks owner, subject, capability, status `proposed`, and the exact fingerprint.
- **Tools:** reads `list_attention`, `get_matter`, `list_proposals`, `get_lifecycle` (`readOnlyHint=true`). Writes `submit_request`, `link_session`, `set_handle_visibility` (`readOnlyHint=false`). No `execute` or `verify`.
- **Requests:** a delivery utterance is untrusted. Wally grounds it to one trusted document title and one trusted recipient title, then stores a `deliver_document` proposal at status `proposed`. The fingerprint covers matter, intent, document id, and recipient id. Ambiguous or unknown titles write nothing. The proposal is not executable.
- **Continuity:** the host session id links the handle. One session can hold many Matters. One Matter can hold many sessions. Archive changes handle visibility only. No transcript is stored.
- **Schema:** no new tables.

**Deployment:** The local MCP adapter and the read/write/decide capability model are implemented. A hosted ChatGPT connection has not been validated. `record_decision` remains disabled until that platform provides an authenticated connection and an explicit confirmation of the decision call. The loopback `/oauth/token` grant is a local test harness. A Secure MCP Tunnel can reach a private MCP server, and OAuth discovery can pass through it, but the authorization server itself is not tunneled. This is a platform and deployment limit. The Wally architecture is unchanged.

**Not in this milestone:** remote execution, Telegram, Home Assistant, scheduling, notifications, transcript sync, and a generic operation tool.

See ADR-040.

---

## v0.18.0 — Telegram inbox ✓

**Goal:** Telegram is the private place Wally can open a conversation. The owner sees one approval card, decides with a button, and can ask what is going on in the same chat. Approve records a decision. It does not execute.

- **Path:** long poll → fixed `telegram` Gateway adapter → existing submit, read, link, and decide services. One numeric user id in a private chat. A local lease keeps a single poller.
- **Bot token:** `telegram.bot_token_ref` is an `op://` pointer resolved by the existing secrets provider. The owner user id stays ordinary configuration. `WALLY_TELEGRAM_BOT_TOKEN` is the test injection path.
- **LaunchAgent:** `wally telegram install` registers `com.wally.telegram-poll` for the logged-in user. It starts after login, restarts after a crash or a configuration exit, and waits 30 seconds between those restarts. The plist has no secret. The poller stays up across a Telegram timeout.
- **Callbacks:** `callback_data` is a random nonce plus a compact action. The outbox row binds the nonce to the proposal id, fingerprint, chat, and allowed actions. The payload is not trusted because Telegram delivered it.
- **Ingress:** each `update_id` is stored before the long-poll offset moves. A redelivery does not open a second request, Matter, proposal, or decision.
- **Outbox:** one row per dedupe key. Delivery is at-least-once with best-effort duplicate suppression. A stale `sending` lease can be retried. A durably delivered row is not sent again. A second card after an ambiguous crash is acceptable. A second decision is not.
- **One card:** a `decision_required` proposal is delivered only through the outbox, for a Telegram request and for a proposal Observe already stored.
- **Not now:** dismisses that notification. The proposal stays `proposed`. No reminder is scheduled.
- **Text:** "approve it" can show the card again. The text does not record the decision. Utterances stay untrusted evidence and use the existing grounding path. There is no reasoner in this release.
- **Schema:** `notification_outbox`, `telegram_updates`, `telegram_cursor`, and `telegram_lease`.

**Deployment:** The credential path is in place. A live Bot API smoke test has not been run. This machine has no `op` CLI on `PATH`, no `telegram.bot_token_ref`, and no owner user id. Until those are set, `wally telegram poll` cannot reach Telegram. The fake-client suite covers callbacks, replay, Not now, and the lease.

**Not in this milestone:** Home Assistant, a general scheduler, remote execution from Telegram, a Wally Reasoner, group chats, transcript sync, and a public webhook.

See ADR-041.

---

## v1.0 later — Phase 5 (future)

**Phase 5 — Daily-driver hardening:** scheduling and proactive triggers, notification UX, noise control, broader action-type support.

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
