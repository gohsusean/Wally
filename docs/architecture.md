# Wally — Architecture history and design notes

**Status:** Historical notebook, including later milestone notes; superseded as
an implementation reference by [current architecture](current-architecture.md).
**Documentation classification updated:** 2026-10-06.

Read [current architecture](current-architecture.md), [operations](operations.md),
[engineering debt](engineering-debt.md) and the relevant [ADRs](decisions.md) first.
The sections below preserve earlier designs and their rationale; Accepted/planned
language is not proof of implementation. In particular, the Memory/Notion-MCP/HA
model, scheduler/event-bus timing, Mac Mini cutover, missing health scripts and
pre-commit scanning are historical proposals. Current Notion uses REST; scheduling
and HA are not implemented. Later sections describe individual milestones, not
uniform authorization across both current action paths.

---

## 1. Purpose

This document describes the technical architecture for Wally — a personal **Chief of Staff** and **personal AI operating system** that orchestrates reasoning, knowledge, and action across external systems.

Wally is **not** a home automation platform. Home Assistant and Alexa remain authoritative for the physical home. Wally may optionally read home context from Home Assistant in the future via a limited provider interface — that is not a near-term goal.

The architecture optimises for:

- **Longevity** — understandable and maintainable for ten or more years.
- **Replaceability** — any provider can be swapped without rewriting the core.
- **Safety** — human approval gates on consequential actions.
- **Solo maintainability** — one engineer can own the entire system.

---

## 2. Conceptual model (historical baseline)

Wally separates **reasoning** from **execution**.

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                          │
│              (text, voice — future; CLI for v0.2)               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                             │
│  • Receives intent                                              │
│  • Retrieves context                                            │
│  • Plans actions (via LLM)                                      │
│  • Routes through safety gates                                  │
│  • Dispatches to providers                                      │
│  • Composes response                                            │
└─────┬──────────┬──────────┬──────────┬──────────┬──────────────┘
      │          │          │          │          │
      ▼          ▼          ▼          ▼          ▼
┌─────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐
│   LLM   │ │ Memory │ │  Home  │ │Workflow│ │Approval │
│Provider │ │Provider│ │  Auto  │ │Provider│ │Provider │
└────┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └────┬────┘
     │          │          │          │           │
     ▼          ▼          ▼          ▼           ▼
┌─────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐
│ OpenAI  │ │ Notion │ │  Home  │ │  n8n   │ │ Human   │
│ Adapter │ │  MCP   │ │Assistant│ │Adapter │ │ (you)   │
└─────────┘ └────────┘ └────────┘ └────────┘ └─────────┘

        ┌──────────────────────────────────────┐
        │           AUDIT LOG                │
        │  (every action, every decision)    │
        └──────────────────────────────────────┘
```

**Key invariant:** The orchestrator knows *capabilities*, not *implementations*.

---

## 3. Core components (historical and milestone-specific notes)

### 3.1 Orchestrator

The heart of Wally. Responsibilities:

1. Accept user input (text initially; voice later).
2. Load relevant context from memory and session state.
3. Invoke the LLM to produce a structured action plan.
4. Classify each planned action by risk level.
5. Route high-risk actions through the approval provider.
6. Execute approved actions via the appropriate capability provider.
7. Log everything to the audit trail.
8. Compose a response that distinguishes facts from inferences.

The orchestrator does **not**:
- Call external APIs directly.
- Contain provider-specific logic.
- Embed LLM-specific response parsing beyond the LLM adapter.

### 3.2 Reasoning Router

Deterministic runtime component that selects a **reasoning profile** for each task. Routing is governance, not reasoning.

```
User → Runtime (Orchestrator) → Reasoning Router → Reasoning Provider → OpenAI → Model
```

Responsibilities:

1. Accept a `Task` (intent, tools, workflow, metadata) — not raw user text.
2. Apply override hierarchy: CLI/env override → task rules → default profile.
3. Return a profile name for the Reasoning Provider.

Future extensions (not implemented): complexity estimation, provider selection, cost/latency optimisation, user preferences, automatic escalation.

### 3.3 Retrieval Router (planned)

Deterministic runtime component — **not yet implemented**. v0.8 is an interim state where the Reasoning Provider may choose retrieval tools (`knowledge_retrieve`, `conversation_search`, `communications_*`, `web_search`, etc.) via prompts. This is **temporary**; the runtime should ultimately own retrieval policy.

**Long-term responsibility:** Given a `Task`, decide which knowledge sources to consult *before* reasoning — not which model to use (that is the Reasoning Router) and not how to reason (that is the Reasoning Provider).

Candidate inputs:
- Task type and intent
- Freshness requirements (current news vs personal records)
- Source authority (policy vs knowledge vs external web)
- Availability of internal knowledge

Candidate sources:
- Knowledge Provider (Notion)
- Conversation recall
- Gmail / Calendar (Communications)
- Web Provider (external)

```
User → Runtime → Retrieval Router → Knowledge Providers → [Content Sanitizer] → Reasoning Provider
```

The LLM must not ultimately own retrieval policy. See [roadmap.md](roadmap.md).

### 3.4 Content Sanitizer

Deterministic runtime component (`runtime/content_sanitizer.py`) that preprocesses **external** content before it reaches the Reasoning Provider. Implemented in v0.8 for `web_fetch` and web search summaries.

**Role:** Normalise and remove irrelevant or potentially malicious material — not to determine truth.

Examples of what it removes or normalises:
- HTML boilerplate, scripts, CSS, embedded frames
- Obvious prompt-injection phrases and embedded tool instructions
- Zero-width characters and excess whitespace

**Pipeline (external sources only):**

```
External source → Web Provider → Content Sanitizer → Reasoning Provider
```

The web sanitizer described here is not authentication. Gmail/calendar and arbitrary Notion/conversation text remain untrusted data; designated structured Knowledge fields have a different role. No source text can issue a principal grant or authorize a decision. See ADR-023, ADR-034 and [current architecture](current-architecture.md).

**Security note:** The Content Sanitizer is **defense in depth**, not the primary security boundary. Primary controls remain:
- Runtime policy (`runtime/policy.py`)
- Trust model and External Source Rule (`runtime/authority.py`)
- Execution identities and provider isolation
- Approval engine and least privilege

Malicious instructions that survive sanitization must still be unable to influence governance or trigger privileged actions.

**Future:** Log when the sanitizer removes suspicious content (e.g. matched injection patterns) to the audit trail, so debugging can surface messages such as *"this webpage contained possible prompt-injection text"*. This is observability only — not a user-facing security guarantee.

### 3.5 LLM Provider (Reasoning Provider)

Abstracts language model interaction.

```python
# Historical sketch; current protocol is src/wally/providers/llm.py
class LLMProvider(Protocol):
  def complete(
    self,
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
  ) -> LLMResponse: ...
```

**Current adapter:** OpenAI Responses API.  
**Future adapters:** Local models (Apple Silicon), Anthropic, etc.

**Reasoning profiles** (`providers.llm.profiles`) define how much reasoning a task receives — not a pricing tier. Profiles (`fast`, `balanced`, `deep`) map to models and may later add timeouts, output limits, and retrieval behaviour.

**Reasoning Router** (`runtime/reasoning_router.py`) selects the profile per task before each LLM call. The orchestrator builds a `Task` (intent, tools invoked, metadata) and delegates routing to the router. Override hierarchy: explicit CLI/env override → task-specific rules → default (`balanced`). Users do not choose profiles during normal operation; `--profile` and `WALLY_REASONING_PROFILE` are for development and debugging only.

Routing rules are configurable in `providers.llm.routing` (tool names and task intents → profile). The LLM provider receives the selected profile, maps it to a model, and calls the API — it does not decide the profile.

The orchestrator passes tool definitions derived from registered capability providers. The LLM adapter translates between Wally's tool schema and the provider's native format.

### 3.6 Capability Providers

Each external domain has a provider interface. Providers expose *tools* that the LLM can invoke through the orchestrator.

| Provider | Responsibility | Priority | Initial adapter |
|----------|---------------|----------|-----------------|
| `KnowledgeProvider` | Personal knowledge assets | **Now** | Notion REST |
| `WorkflowProvider` | Trigger and monitor workflows | v0.5 | n8n |
| `CommunicationsProvider` | Email, calendar | v0.6 | Google (Gmail + Calendar) |
| `ConversationProvider` | Cross-session recall, context consolidation | v0.7 | SQLite FTS (local) |
| `WebProvider` | External web search and page fetch | v0.8 | OpenAI `web_search` (first adapter) |
| `PeopleProvider` | Personal contacts (multi-source) | Post-v0.7 | Apple / Google / Notion (TBD) |
| `FinanceProvider` | Bills, payments, financial context | v0.9 | Knowledge + n8n workflows (local adapter) |
| `BrowserAutomationProvider` | Deterministic browser execution (portals, forms) | **v0.10** | Playwright (`adapters/browser/playwright_adapter.py`) |
| `SecretsProvider` | Execution-time credentials | **v0.11** | 1Password CLI (`adapters/secrets/op_cli.py`) |
| Observe & Brief (`ops/`) | Observations, Matters, operational brief | **v0.12** | Wally SQLite + existing read providers |
| `ApprovalProvider` | Execution-time y/n for a consequential tool call | **Now** | CLI prompt |
| Approval Inbox (`ops/`) | Durable decision on a ProposedAction | **v0.14** | SQLite columns on `proposals`; not a tool grant |
| Act & Verify (`ops/act.py`) | User-requested execution of an approved proposal, then verification | **v0.15** | Existing gate, `ApprovalProvider`, browser executor, secrets resolver; `executions` table |
| Principal authority (`runtime/principals.py`) | Issue and check authenticated request contexts | **v0.15** | Per-process HMAC grant; capabilities, not channel-name lists |
| `HomeAutomationProvider` | Home device state/control | **Deferred** | Optional HA read-only (future) |

#### Finance safety (`runtime/finance_safety.py`)

**Legacy path scope:** the claims below describe intended checks, not proven canonical input provenance. Current bill dictionaries can be model-supplied, parameters can override verified fields, and paid evidence is self-asserted. See D01/D02 in [engineering debt](engineering-debt.md). Do not use this path as the template for new privileged capabilities (ADR-042).

- **Payment initiation** — `finance_trigger_payment` is approval-gated (`ActionClass.FINANCIAL`). The **ExecutionCapabilityRouter** (`runtime/execution_router.py`) selects the workflow from provider `payment_method` in the trusted `bill` object — the LLM does not choose workflow names. Approval prompts include verification summary, payment method, and runtime-selected capability.
- **Verification Engine** (`runtime/verification_engine.py`) — deterministic, **payment-method-aware** comparison of statement evidence against trusted Knowledge Asset fields. Bank transfer: payee, amount, due date, bank account. Card portal: portal URL, provider, amount, account reference (no bank account). LLM may extract `statement` fields; comparison and block decisions are runtime-only.
- **Marking bills paid** — Triggering a workflow does **not** update knowledge. A bill may be recorded as paid in Notion only with `payment_evidence`:
  1. `workflow_success` — successful n8n workflow result
  2. `user_confirmation` — explicit user confirmation
  3. `verification_provider` — future read-only financial verification
- Without valid evidence, `knowledge_update` / `knowledge_create` that marks a finance-role bill as paid is rejected by `evaluate_bill_paid_write_policy`.

#### Proposal approval versus execution approval (v0.14 history)

The always-false helper below was a Phase 3 boundary. ADR-037 adds separately authorized execution; ADR-038 replaces literal-origin authorization. Approval remains inert.

These are different stages.

| | Approval Inbox | `ApprovalProvider` |
|--|----------------|--------------------|
| Object | Durable `ProposedAction` | One consequential tool call |
| When | Before any execution exists | Immediately before a provider write |
| Effect in v0.14 | Status becomes `approved`, `rejected`, or `deferred` | Still the y/n gate on tools that actually run |
| Executes? | No. `execution_allowed` is always false | Only if the user confirms that call |

An email, calendar item, Notion page, or model reply cannot set proposal status. n8n does not store it. A material fingerprint change supersedes an approval instead of reusing it. See ADR-036.

#### Act & Verify (v0.15)

A channel adapter issues an authenticated `RequestContext` and calls `ActVerifyService.execute`. The service, not the adapter, checks `EXECUTE_PROPOSAL`. CLI and REPL are the current adapters; they are not the authorization boundary.

```
authenticated request context → preflight (exact approved fingerprint, open Matter,
same fingerprint from current evidence, supported intent) → typed plan from trusted
Knowledge → ApprovalGate → ApprovalProvider prompt (always) → recheck + plan digest
→ GovernedBrowserExecutor.run_portal_review_login (secrets resolved here)
→ verify_portal_review (read-only VERIFY_AUTH) → executions row + audit
```

- The intent-to-executor map lives in code (`SUPPORTED_EXECUTION_INTENTS`). Proposals carry no tool, provider, URL, argument, or secret.
- `REVIEW_BILL` logs in and stops. It cannot reach `run_card_portal_payment`, `finance_trigger_payment`, or n8n.
- The `executions` table keeps one in-flight or verified attempt per approved fingerprint. `running` and `executed_unverified` block retries until the user reviews them.
- A verified review does not resolve the Matter. Only Observe evidence does. See ADR-037.
- Request provenance (channel, principal, external refs, correlation id) is audit metadata. It does not enter the proposal fingerprint. See ADR-038.

#### Authenticated principals (v0.15)

`PrincipalAuthority` (`runtime/principals.py`) is the only issuer of request contexts. Local channels today: `cli` and `repl`, both with decide, execute, and verify. A future channel registers a policy; Act & Verify does not learn its name. Hand-built principals and unregistered channel names fail closed.

#### Gateway (v0.16)

`GatewayRuntime` (`gateway/service.py`) is the local boundary for an external adapter. The adapter authenticates with its own credential and is registered to one channel. The runtime then calls `PrincipalAuthority.issue()` for that channel. The HMAC key never leaves the authority. A payload cannot select `cli` or any other channel.

CLI and REPL do not go through the Gateway. They still issue in-process. `create_app` builds a Gateway with no adapters and does not open a socket.

An ActiveMatter is a continuity handle across interfaces: many correlation ids, and many session refs per channel. Its `active` / `archived` visibility is about whether Wally should keep offering the handle. Where a canonical Matter exists, that Matter's status remains the state of the bill or problem.

Conversational items on a request are a short untrusted capsule. They are not stored as a transcript, not returned by `get_context`, and not part of a proposal fingerprint. See ADR-039.

#### ChatGPT interface (v0.17)

`wally chatgpt serve` binds `127.0.0.1` only. `initialize` returns no private state. `tools/list` and `tools/call` require `Authorization: Bearer`. The bearer is a random token minted after the owner secret completes a PKCE S256 grant on `POST /oauth/token`. That grant stays on localhost. It is not published through a tunnel. The OpenAI subject string cannot mint the token.

The adapter is registered as channel `chatgpt` with `approval_adapter` false, so Gateway execute still fails closed. Tool annotations mark reads `readOnlyHint=true` and writes `readOnlyHint=false`. `record_decision` is annotated `destructiveHint=true` and is registered only when writes are on, the subject allowlist is non-empty, both secrets are set, and `WALLY_CHATGPT_DECISION_CONFIRMATION` is set because the operator has seen the host confirm that call. The handler still checks the bearer, the pinned subject, `decide_proposal`, proposal status `proposed`, and an exact fingerprint.

An ad-hoc delivery request is grounded against exact trusted titles. A unique document and recipient become a `deliver_document` proposal through the existing proposal path. The utterance is not in the fingerprint. Zero or several matches write nothing. `deliver_document` is not an executable intent. See ADR-040.

The adapter is implemented. A hosted ChatGPT connection has not been validated, so `record_decision` stays unregistered in the default configuration. That gap is platform access and transport setup. It does not change the Gateway trust boundary.

#### Telegram inbox (v0.18)

`wally telegram poll` long-polls the Bot API from this Mac. There is no public webhook. The adapter is registered as channel `telegram` with `approval_adapter` false. The owner is `telegram.owner_user_id` in a private chat. Display name, username, groups, edits, and message text do not authorize.

The bot token is `telegram.bot_token_ref`, a `keychain://` pointer resolved by `SecretsProvider` when the poller starts. The audit log records that reference, not the token. The 1Password item remains the source copy, copied in with `wally telegram credential install`. `WALLY_TELEGRAM_BOT_TOKEN` is only the test and local injection path. The in-process Gateway credential is minted for that process when it is not already set.

`wally telegram install` registers a per-user LaunchAgent, `com.wally.telegram-poll`, under `~/Library/LaunchAgents/`. It starts after that user logs in. It does not run at the login screen, and it does not run while the Mac is off or asleep. The plist names the repo's `.venv/bin/python -m wally telegram poll`, sets the working directory to the repo, and sets a fixed `PATH` that includes the virtualenv and the usual 1Password CLI locations. It does not source a shell profile and it does not contain the bot token, an `op://` session, or a Gateway grant. The poller reads the login keychain and does not need a Terminal `OP_SESSION`. If the login keychain is still locked, the poller exits and launchd tries again after 30 seconds. `wally telegram uninstall` does not delete that keychain item. A network timeout or Telegram 5xx stays inside the process. `telegram_lease` still allows only one poller to hold the bot. Logs are `data/logs/telegram-poll.stdout.log` and `data/logs/telegram-poll.stderr.log`.

Callback data is a short random nonce plus `a`, `r`, or `n`. The outbox row binds that nonce to the proposal id, fingerprint, chat, and allowed actions. The handler loads that row and then runs the existing decide path. `Not now` dismisses the notification and leaves the proposal `proposed`. It does not schedule a reminder.

A proposal that needs a decision has one outbox row and one delivery path, whether the owner just asked in Telegram or Observe created the proposal. Outbound delivery is at-least-once with best-effort duplicate suppression. A crash in the window after Telegram accepts `sendMessage` can show a second card. A replayed `update_id` does not create a second request, proposal, or decision. One poller holds a local lease. See ADR-041.

#### Browser Automation Provider (v0.10)

**`BrowserAutomationProvider`** executes deterministic browser interactions — open portals, navigate, fill forms, click, upload, download, read confirmation pages. It does **not** contain business logic, choose payment methods, or bypass approval.

| Does | Does not |
|------|----------|
| Run action scripts from the runtime | Decide what to pay or how much |
| Open trusted portal URLs from Knowledge | Select execution capabilities |
| `WAIT_FOR_USER` when credentials unavailable | Store or fetch secrets directly |
| Return page text / downloads for verification | Expose ad-hoc tools to the LLM for payments |

**Default adapter:** Playwright in `adapters/browser/playwright_adapter.py`. The runtime depends on the protocol only.

**Credentials:** When `SecretsProvider` is available, the **runtime** resolves credentials after approval and may FILL login fields. When unavailable, Wally opens the portal and waits for **manual authentication**.

**Trusted portal URL rule (mandatory):** Browser automation may **only** navigate to portal URLs from approved Knowledge Assets (`payment_portal_url`, etc.). URLs from emails, web pages, PDFs, unverified user text, or LLM output must **not** be used as navigation targets. If the trusted URL is missing from Knowledge, execution stops and the user is asked to add/approve it first. Enforced in `runtime/browser_safety.py`. See [browser-automation.md](browser-automation.md).

**Payment method mapping:**

| `payment_method` | Execution capability | Execution layer |
|------------------|---------------------|-----------------|
| `bank_transfer` | `pay-bill-bank-transfer` | Workflow Provider (n8n) |
| `card_portal` | `pay-bill-card-portal` | Browser Automation Provider (behind capability) |

#### Governed execution pipeline

High-trust actions (especially finance) follow a fixed governance chain. Browser automation sits at the **execution** layer only:

```
Reasoning (LLM plans tools, extracts evidence)
        ↓
Verification Engine (evidence vs Knowledge — deterministic)
        ↓
Approval Engine (human confirmation)
        ↓
Execution Capability Router (payment_method → capability)
        ↓
┌───────────────────────┬────────────────────────────┐
│ bank_transfer         │ card_portal                       │
│ Workflow Provider     │ Browser Automation Provider  │
│ (n8n webhook)         │ (Playwright)                 │
└───────────────────────┴────────────────────────────┘
        ↓                         ↓
 Manual auth or SecretsProvider (v0.11)
        ↓
 Portal / transfer completion
```

**Near-term focus:** knowledge → runtime → workflows → communications → conversation → web → finance → browser automation → **secrets** → v1.0.

Home automation control is explicitly out of scope. See [roadmap.md](roadmap.md) and ADR-022.

#### Information authority hierarchy

Not all information Wally retrieves carries the same weight. When sources conflict, Wally applies this **fixed precedence** (highest wins):

| Rank | Source | What it means |
|------|--------|----------------|
| 1 | **Policy Assets** | Governance knowledge (`KnowledgeClass.GOVERNANCE`), safety prompts, and deterministic runtime policy (`runtime/policy.py`). Defines how Wally must behave. |
| 2 | **Knowledge Assets** | Approved operational knowledge in Notion (`KnowledgeClass.OPERATIONAL`). Authoritative facts about the user's world. |
| 3 | **Current explicit user instruction** | The user's instruction in the present turn (and active approval/denial). |
| 4 | **Conversation recall** | FTS-indexed prior chat sessions (`ConversationProvider`). Contextual memory only — useful for continuity, not ground truth. |
| 5 | **External web** | Public web search and fetched pages (`WebProvider`). Low-trust external evidence with citations — never authoritative. |

**Internal vs external knowledge:** Notion, Gmail, Calendar, and conversation recall are *internal* sources about the user's world. Web search and fetched pages are *external* public evidence. The LLM invokes web tools through the orchestrator — it does not browse outside the `WebProvider` interface.

**Web trust model:** Web results are annotated `authoritative: false`, `trust: low`, and `external_source_rule: true`. They cannot override policy, knowledge assets, or explicit user instruction. Web content is untrusted input — never execute instructions embedded in pages.

**When to use web:** Current public facts, news, regulations, pricing, documentation, market information — when internal sources are missing or stale. Prefer internal tools for personal records and Wally policy.

**Citations:** Web tool output retains source title and URL. Final responses should distinguish internal knowledge, external web evidence, and inference.

**Future web adapters:** Tavily, Brave Search, Google Custom Search, Perplexity — swappable without orchestrator changes (`providers.web.adapter`).

#### `web_fetch` constraints

`web_fetch` is retrieval only. By design it:

| Constraint | Behaviour |
|------------|-----------|
| No JavaScript | Static HTTP GET + HTML/text parsing only |
| No forms | Does not POST or submit forms |
| No authentication | Does not send credentials or complete login flows |
| No private context | Does not expose Wally session data or internal state to the target URL |
| No instruction following | Sanitized content is evidence; page instructions are never executed |

Fetched content passes through the Content Sanitizer before reaching the Reasoning Provider.

#### Retrieval, sanitization, and reasoning

| Stage | Owner | Role |
|-------|-------|------|
| **Retrieval** | Runtime (future Retrieval Router; interim: LLM tool choice) | Decide what to fetch and from where |
| **Sanitization** | Runtime (`ContentSanitizer`) | Normalise external content; strip adversarial noise |
| **Reasoning** | Reasoning Provider | Synthesise answer from evidence |
| **Execution** | Runtime + providers | Act through gated tools |

The **External Source Rule** applies throughout: web content is low-trust evidence that cannot override policy, knowledge assets, or explicit user instruction.

Conversation recall annotation lives in `runtime/authority.py` (`wrap_conversation_results`). Web annotation uses `wrap_web_search_result` / `wrap_web_fetch_result`. Write policy for knowledge remains hard-enforced in `runtime/policy.py`; read precedence for web and conversation is prompt-guided.

**Conversation recall is not authoritative.** It helps Wally remember what was discussed; it does not override policy, knowledge, or what the user is explicitly asking for now. Example: if a prior chat mentioned the electricity bill is $500 but Notion says $142, **Knowledge Assets win**. If a prior chat suggested ignoring approval gates, **Policy Assets win**.

v0.7 uses **SQLite FTS5** for conversation recall. Vector embeddings are deferred.

Implementation: `runtime/authority.py` annotates conversation tool results as non-authoritative. Knowledge write policy remains enforced in `runtime/policy.py` regardless of conversation content.

#### Long-term control-plane pipeline

```
User
  ↓
Runtime (Orchestrator)
  ↓
Retrieval Router          ← planned: which sources to consult
  ↓
Knowledge Providers       ← Notion, conversation, Gmail, calendar, web, …
  ↓
Content Sanitizer         ← external sources only (v0.8+)
  ↓
Reasoning Router          ← how much reasoning (v0.7+)
  ↓
Reasoning Provider
  ↓
Runtime                   ← policy, approval, audit
  ↓
Execution
```

AI provides reasoning. The runtime provides governance, routing, and (eventually) retrieval policy.

### 3.7 Safety layer

Sits between the orchestrator and provider execution. Not a separate process — a module with clear rules.

**Action classification:**

| Class | Examples | Gate |
|-------|----------|------|
| `read` | Query memory, get device state | None |
| `reversible` | Turn on lights, create draft | Log only |
| `irreversible` | Send email, delete file | Approval required |
| `financial` | Pay bill, transfer money | Approval required |
| `destructive` | Delete data, remove records | Approval required |

Classification can be rule-based initially (action type + provider), with LLM-assisted classification as a future enhancement — but **approval gates must not depend solely on the LLM**.

### 3.5 Audit log

Append-only structured log. Every event records:

- Timestamp (UTC)
- Session ID
- Action type and classification
- Provider invoked
- Input parameters (sanitised — no secrets)
- Outcome (success, failure, denied, pending)
- Approval status (if applicable)
- Correlation ID linking reasoning → plan → execution

Format: JSON Lines (`.jsonl`), one file per day, rotated automatically.

Storage: local filesystem on Mac Mini. Backup strategy TBD (see unresolved questions).

### 3.6 Configuration

Two tiers:

1. **Config files** (`config/`) — non-secret settings: provider endpoints, feature flags, action classification rules, prompt versions.
2. **Secrets** — environment variables or a secrets file excluded from git: API keys, tokens.

Profiles: `config/macbook.yaml` (development) and `config/macmini.yaml` (production). Same codebase, different config.

---

## 4. Request lifecycle

A typical interaction flows through these stages:

```
1. INPUT
   User: "What bills are due next week?"

2. CONTEXT ASSEMBLY
   Orchestrator loads session history + queries MemoryProvider
   for relevant financial records.

3. REASONING
   LLM receives context, available tools, and safety rules.
   Returns structured plan:
     - action: memory.search(query="bills due", timeframe="next 7 days")
     - response_intent: summarise results for user

4. CLASSIFICATION
   Safety layer classifies memory.search as "read" → no approval.

5. EXECUTION
   MemoryProvider adapter queries Notion via MCP.
   Result returned to orchestrator.

6. RESPONSE COMPOSITION
   LLM (or template) composes final answer.
   Metadata tags facts vs inferences.

7. AUDIT
   Full trace written to audit log.
```

A higher-risk example:

```
User: "Pay my TM110 maintenance bill."

1. LLM retrieves provider from Knowledge (finance_bills_search)
2. LLM calls finance_trigger_payment with trusted bill + statement evidence
3. ExecutionCapabilityRouter: payment_method → pay-bill-bank-transfer
4. VerificationEngine runs method-specific checks
5. Safety classifies as financial → approval with verification summary
6. User approves
7. Execution layer (by payment method):
   - `bank_transfer` → WorkflowProvider triggers n8n (`pay-bill-bank-transfer`)
   - `card_portal` → BrowserAutomationProvider opens portal, runs deterministic actions (v0.10+)
8. Manual authentication when credentials unavailable (initial v0.10 behaviour)
9. Audit log records routing, verification, approval, execution
```

#### Execution capability registry

`config/workflows.yaml` registers **execution capabilities** (how to pay, send, reconcile) — not business-specific per-vendor flows. Provider metadata in Knowledge determines routing. See [workflows.md](workflows.md).

---

## 5. Provider interface design

### Design rules

1. **Narrow interfaces** — each method does one thing.
2. **Domain types** — Wally defines its own types; adapters translate.
3. **Idempotency where possible** — safe to retry read operations; write operations carry idempotency keys.
4. **Explicit errors** — typed exceptions, not opaque strings.
5. **Health checks** — each provider reports `is_healthy()` for startup diagnostics.

### Example: MemoryProvider (conceptual)

```
MemoryProvider
├── search(query, filters) → list[MemoryEntry]
├── get(entry_id) → MemoryEntry
├── store(entry) → entry_id          # reversible
├── update(entry_id, changes) → void  # reversible
└── delete(entry_id) → void           # destructive → gated
```

### Example: HomeAutomationProvider (conceptual)

```
HomeAutomationProvider
├── get_state(entity_id) → DeviceState
├── list_entities(domain?) → list[Entity]
├── call_service(domain, service, entity_id, params) → ServiceResult
└── get_areas() → list[Area]
```

Wally refers to entities by **aliases** (e.g. `study_lights`), not raw HA entity IDs. A mapping file in `config/` translates aliases to provider-specific identifiers.

---

## 6. Tool registration

Providers register their capabilities as tools available to the LLM:

```
Provider → registers ToolDefinitions → Orchestrator maintains ToolRegistry
                                              ↓
                                    LLM receives tool schemas
                                              ↓
                                    LLM returns tool_calls
                                              ↓
                                    Orchestrator dispatches to provider
```

This keeps the LLM's view of the world stable even when adapters change.

---

## 7. Prompt management

Prompts live in `prompts/` as versioned files:

```
prompts/
├── system/
│   └── v1.md              # Core Wally personality and rules
├── capabilities/
│   ├── memory_v1.md       # How to use memory tools
│   └── home_v1.md         # How to use home automation tools
└── safety/
    └── v1.md              # Safety and approval instructions
```

The orchestrator assembles prompts from these files. Version numbers allow rollback and A/B comparison.

---

## 8. Source layout (planned)

```
src/wally/
├── __init__.py
├── __main__.py              # Entry point: python -m wally
├── ops/                     # Observe → propose → Approval Inbox → Act & Verify (v0.12–v0.15)
├── gateway/                 # Local adapter boundary (v0.16); no network listener
├── orchestrator/
│   ├── core.py              # Main reasoning loop
│   ├── context.py           # Context assembly
│   └── response.py          # Response composition
├── providers/
│   ├── knowledge.py         # KnowledgeProvider protocol
│   ├── workflow.py          # WorkflowProvider protocol
│   ├── browser.py           # BrowserAutomationProvider protocol
│   ├── secrets.py           # SecretsProvider protocol
│   ├── finance.py           # FinanceProvider protocol
│   └── ...
├── adapters/
│   ├── openai/
│   ├── notion/
│   ├── n8n/
│   ├── browser/             # Playwright adapter (v0.10)
│   │   └── stub.py
│   └── secrets/
├── safety/
│   ├── classifier.py        # Action risk classification
│   └── gates.py             # Approval gate logic
├── audit/
│   └── logger.py            # Structured audit logging
├── models/
│   ├── actions.py           # Action, ActionPlan, ActionResult
│   ├── messages.py          # Message, Session
│   └── memory.py            # MemoryEntry and related types
└── config/
    └── loader.py            # Config and secrets loading
```

This is a historical target sketch, not the current tree. Implementation exists; see [repository layout](layout.md).

---

## 9. Deployment model (historical plan, not a runbook)

### Phase 1: MacBook (development)

- Run locally during development.
- Connect to remote Home Assistant and n8n instances.
- OpenAI API calls over the internet.
- Notion MCP via Cursor or standalone MCP client.

### Phase 2: Mac Mini (production)

- Single Python process (systemd or launchd service).
- All providers run as adapters within the process.
- Local audit log storage.
- Home Assistant and n8n may run on the same machine or the network.
- Voice handled via Home Assistant Voice pipeline.

### Migration strategy (MacBook → Mac Mini)

1. **Same codebase, different config profile** — no code changes required.
2. **Secrets migration** — copy environment variables or secrets file; never commit them.
3. **Audit log migration** — rsync `data/audit/` directory.
4. **Alias mappings** — `config/entities.yaml` and similar files transfer as-is.
5. **Smoke test script** — `scripts/health_check.py` validates all providers before cutover.
6. **Parallel running** — optional brief period where both machines run; MacBook config points to read-only mode.

No containerisation required initially. Docker may be considered later if dependency isolation becomes painful.

---

## 10. Testing strategy (planned)

| Layer | Approach |
|-------|----------|
| Provider interfaces | Protocol compliance tests with mock adapters |
| Adapters | Integration tests against real services (marked `@integration`, skipped in CI) |
| Safety gates | Unit tests with every action classification |
| Orchestrator | End-to-end tests with mock LLM returning canned plans |
| Prompts | Snapshot tests for prompt assembly |

Test fixtures and mock providers live in `tests/`.

---

## 11. Major architectural trade-offs

### Single process vs microservices

**Decision:** Single process.  
**Rationale:** Solo maintainer, single-machine deployment, low operational overhead. Providers are modular *within* the process. Split only if a component needs independent scaling or isolation (unlikely for a personal OS).

### MCP as integration layer vs custom adapters (historical choice)

**Decision:** MCP where available (Notion); custom HTTP adapters elsewhere (HA, n8n).  
**Rationale:** MCP is valuable when the ecosystem supports it, but Wally should not depend on MCP for all integrations. Custom adapters are simpler for REST APIs with stable contracts.

### Synchronous vs event-driven orchestration

**Decision:** Synchronous request-response for v0.2–v0.5; event-driven layer added for proactive intelligence (v0.8).  
**Rationale:** Conversation is inherently request-response. Proactive features (scheduled checks, HA event triggers) need an event bus later, but not on day one.

### LLM-driven vs rule-driven safety classification

**Decision:** Rule-driven initially; LLM-assisted as supplement, never sole gate.  
**Rationale:** Safety gates must be deterministic and auditable. An LLM might *suggest* a classification, but rules enforce it.

### Local memory vs cloud memory

**Decision:** Cloud (Notion) initially; local vector store as future option.  
**Rationale:** Notion is already in use and accessible everywhere. Local embeddings on Apple Silicon become attractive at v0.7 for speed and privacy.

---

## 12. Known risks (historical assessment; mitigations not all implemented)

| Risk | Impact | Mitigation |
|------|--------|------------|
| OpenAI API changes | LLM adapter breaks | Thin adapter, version-pinned API, abstraction layer |
| Notion rate limits / downtime | Memory unavailable | Graceful degradation; cache recent queries locally |
| MCP protocol instability | Notion adapter breaks | Adapter isolation; fallback to Notion REST API |
| Prompt injection via memory | Unsafe actions | Memory content treated as untrusted input; safety rules in system prompt |
| Approval fatigue | User auto-approves everything | Batch approvals; clear consequence summaries; audit review |
| Scope creep | Never reaches v1.0 | Strict roadmap; one capability per version |
| Single-machine failure | Wally unavailable | Accept for v1.0; HA and n8n have their own availability |
| Secret leakage | API keys exposed | Env vars only; pre-commit hook scanning; `.gitignore` |

---

## 13. Design questions — resolved (2026-06-27)

| # | Question | Decision |
|---|----------|----------|
| 1 | Session persistence | **Yes, from v0.2.** SQLite store for conversation history. v0.7 adds semantic search and consolidation on top. |
| 2 | Approval UX | **CLI y/n in v0.2–v0.5.** Telegram and WhatsApp as approval channels post-v1.0 (or late v0.9 if prioritised). |
| 3 | Entity aliases | **Deferred with HA.** If a future read-only HA provider ships, aliases may live in `config/entities.yaml`. Not required for v1.0. |
| 4 | Notion schema | **Existing workspace.** Knowledge registry for classifications; `notion.yaml` for platform defaults only. |
| 5 | n8n workflows | **Wally owns workflow definitions.** Stored in `workflows/` and deployed to n8n. |
| 6 | Financial data | **Notion for financial memory from day one.** Passwords, cards, bank credentials via **1Password** (future `SecretsProvider`). |
| 7 | Proactive triggers | **Calendar, knowledge, email (v0.9).** Cron-based; not HA device events. Conservative default. |
| 8 | Multi-device | **Remote access required.** HTTP API in v0.9 with authentication. |
| 9 | Offline behaviour | **Explicit failure.** Wally states when OpenAI or Notion is unreachable. Never proceeds as if it has information it does not. |
| 10 | Voice latency | **2–3 seconds acceptable.** |

### 13.1 Entity aliases (deferred)

Entity aliases for Home Assistant are **deferred** until an optional read-only HA provider is prioritised. Wally does not implement home automation in the foreseeable roadmap.

If implemented later: aliases in `config/entities.yaml` map human names (`study_lights`) to HA entity IDs. Wally would read state only — control remains with Alexa and Home Assistant.

### 13.2 Proactive intelligence (historical future design)

**Proactive** means Wally initiates contact — you don't ask first.

Two trigger types:

| Type | How it works | Example |
|------|--------------|---------|
| **Scheduled (cron)** | Wally checks on a timer | "Electricity bill due in 3 days" |
| **Event-driven** | Wally reacts to calendar, email, or knowledge changes | "Flight tomorrow — packing list?" |

Home Assistant device events are **not** proactive triggers unless an optional HA provider is added post-v1.0.

**Aggressiveness** controls how often Wally interrupts you:

| Level | Behaviour |
|-------|-----------|
| **Conservative (default)** | Only high-confidence, time-sensitive suggestions. Bills due soon, calendar conflicts, unanswered important email. |
| **Moderate** | Adds useful-but-not-urgent prompts. Packing reminders, weather-based suggestions. |
| **Off** | Reactive only. Wally never initiates. |

**Quiet hours:** No proactive notifications between configurable times (e.g. 22:00–07:00), except security-class events.

**Unimplemented historical proposal:** event bus + cron runner + preference config in `config/proactive.yaml`. That file is dormant; there is no current scheduler or implemented quiet-hours/never-again preference engine. See future Phase 5 in [roadmap](roadmap.md).

### 13.3 Remaining open items

These need detail during implementation, not architectural approval:

- **Google Workspace credentials** — Email and calendar adapter auth (v0.6).
- **1Password integration timing** — After Browser Automation (v0.11+). `SecretsProvider` is independent; browser automation must work with manual auth first. See ADR-030.
- **Telegram/WhatsApp approval** — Post-v1.0 unless remote approval urgency arises.

---

## 14. Recommended improvements (historical proposals, not installed tooling)

Before writing code, consider adopting these practices:

1. **ADR discipline** — Every significant decision gets an entry in `decisions.md` before implementation.
2. **Dry-run mode** — Global flag that logs planned actions without executing. Invaluable for testing reasoning.
3. **Capability registry** — Central registry where providers self-register tools at startup. Enables health dashboard.
4. **Structured responses** — All Wally responses include `{facts: [...], inferences: [...], actions_taken: [...]}` metadata.
5. **Prompt versioning in audit log** — Record which prompt version was used for each reasoning step.
6. **Pre-commit hooks** — Secret scanning, formatting (ruff), and basic linting from v0.2.
7. **Health check endpoint** — Even in a CLI-first tool, a `/health` HTTP endpoint on Mac Mini aids monitoring.
8. **Idempotency keys on writes** — Prevent duplicate workflow triggers if the orchestrator retries.
