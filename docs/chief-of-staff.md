# Chief of Staff — Observe, Brief & Propose

**Version:** 0.18.0  
**Status:** Current milestone (not production v1.0)

Wally is building persistent operational understanding: what is happening, what changed, what is still open, what can wait, and what the user might do next. Phases 1 and 2 are **read / assess / brief / propose only**. Phase 3 records an explicit decision and does not execute. Phase 4 executes one supported action type, only when the user asks, and verifies the result. Nothing runs on its own. v0.16 adds a local Gateway. v0.17 adds a ChatGPT adapter on that Gateway. A hosted ChatGPT connection has not been validated, and `record_decision` stays disabled. v0.18 adds a Telegram long-poll inbox on the same Gateway: one owner, opaque approval buttons, and an outbox. It does not add a scheduler, remote execution, or a reasoner.

## Loop

| Phase | Status | Behaviour |
|-------|--------|-----------|
| 1 Observe → Assess → Brief | **Complete (v0.12)** | Ingest signals, reconcile Matters, print a brief |
| 2 Assess & Propose | **Complete (v0.13.0)** | Durable suggestions in the brief; do not execute |
| 3 Approval Inbox | **Complete (v0.14.0)** | Human authorization of proposals; approval is not execution |
| 4 Act & Verify | **Complete (v0.15.0)** | User-requested execution of the exact approved version; independent verification |
| 5 Daily-driver hardening | Future | Scheduling, noise, notification UX, more action types |

Complete and unchanged unless a regression is found:

- **v0.10** browser / portal automation
- **v0.11 / v0.11.1** Secrets + security hardening

Phase 1 does **not** send email, pay bills, submit forms, or write to external systems.

## Architecture

Wally owns state, policy, and the brief. Gmail, Calendar, and Notion are replaceable sources.

```
connectors (read) → Observations → reconcile Matters → brief
                         ↑
              trusted Knowledge (cadence / due dates only)
```

- **Observation** — a fact Wally noticed (new mail, event change, obligation instance).
- **Matter** — an open loop tracked over time (unpaid invoice, waiting for reply, upcoming event).
- External email, calendar text, and arbitrary Notion pages are **untrusted DATA**. They cannot authorize tools, resolve secrets, or become policy.
- Only designated Knowledge fields already used as configuration (for Phase 1: `cadence` / `due_date` metadata on retrieved assets) drive deterministic obligation instances.

n8n is not the scheduler or state machine for this loop.

## CLI

```bash
uv run wally brief
uv run wally brief --json
uv run wally brief --no-refresh
uv run wally brief --since 2026-08-01T00:00:00+00:00
uv run wally approvals
uv run wally approvals --json
uv run wally approve <proposal-id> [--note "..."]
uv run wally reject <proposal-id> [--note "..."]
uv run wally defer <proposal-id> --until 2026-10-03 [--note "..."]
uv run wally execute <proposal-id>
uv run wally executions [--proposal <proposal-id>]
uv run wally execution <execution-id>
uv run wally verify <execution-id> [--confirm success|failure]
```

In the REPL: `/brief` (add `--no-refresh` to skip a new observe pass), `/approvals`, `/approve <id>`, `/reject <id>`, `/defer <id> --until <date>`, `/execute <id>`, `/executions`, `/execution <id>`, `/verify <id> [--confirm success|failure]`.

`approvals` reconciles stored proposals and does not observe external sources unless `--refresh` is set. Approving does not execute. Only `execute` acts, and it asks again first.

Brief timestamps use `ops.timezone` when set, otherwise the system local timezone. Stored values remain ISO.

Google Calendar auto-event boilerplate (and google.com/calendar / g.co/calendar URLs) is stripped from descriptions. Email snippets are length-capped; receipts use a shorter cap. Unmatched receipts appear under FYI, not Recently resolved.

Live Gmail/Calendar/Notion is optional. CI uses fake providers. Do not treat a live mailbox run as a required gate.

## Prioritization

Rule-based scores with explicit reasons (overdue, due soon, open finance, waiting too long). The brief quotes those reasons. There is no opaque model score.

## Phase 2 — Assess & Propose (v0.13.0)

Implemented. Full rationale in ADR-035. This phase adds no execution path.

Phase 2 turns Matters into **ProposedActions**: durable, explainable advice addressed to the user. A proposal is never an authorization. `ProposedAction` belongs to the operational domain and is defined in `src/wally/models/ops.py` beside `Observation` and `Matter`, deliberately separate from the executable types in `models/actions.py`, so no proposal can reach the tool registry by accident. Generation, persistence, and lifecycle logic live in `wally/ops/`.

### Initial intents

Two, both grounded in Matter types the loop actually produces:

- **`PREPARE_FOR_EVENT`** — an upcoming calendar Matter needs preparation before it starts.
- **`REVIEW_BILL`** — an open finance Matter needs the user to look at a bill.

Generation is deterministic: rules over trusted Matter state pick the intent, its reference parameters, and its lifecycle. Matters flagged for prompt injection produce no proposals at all. Only intent plus reference identifiers (matter, observation, knowledge, event, thread) are stored — never a tool name, provider argument, amount, or address.

### Lifecycle and invalidation

As shipped in v0.13.0: `proposed` → `superseded` | `invalidated` | `expired` | `dismissed`. v0.13.0 had no user decision state. Phase 3 adds `approved`, `rejected`, and `deferred` without turning any of them into execution.

| Transition | Rule |
|------------|------|
| Superseded | The same matter and intent regenerate with materially changed content. The prior row is kept for audit. |
| Invalidated | The underlying Matter resolves, or its supporting facts stop holding. |
| Expired | **Calendar only.** A `PREPARE_FOR_EVENT` proposal expires at event start. |
| Dismissed | Terminal status. No dismiss command in v0.13.0. v0.14 uses reject for a user refusal. |

Bill proposals carry no timer. They close only on resolution, material change, or supersession, so an overdue unresolved bill is never hidden by an arbitrary expiry window. Repeated observe passes are idempotent: the same facts regenerate the same proposal rather than a duplicate.

### Presentation

Proposals render inline beneath their Matter, prefixed `↳ Suggested:` so advice is visually distinct from observed fact. One global footer states that Wally has taken no action. `--json` gains a parallel proposals field and stays schema-stable for existing consumers.

### No-execution boundary

Phase 2 performs no tool call, no provider write, no secret resolution, no capability routing, and no approval bypass. The proposal service is constructed without capability, secrets, or browser dependencies, and tests assert those paths are unreachable from generation. Persistence is additive: a `proposals` table created with `CREATE TABLE IF NOT EXISTS`, leaving v0.12.1 rows unmodified — generation reads Matters but writes nothing back to them — with no reset required.

### Deferred from Phase 2

`FOLLOW_UP` (it depends on sent-mail observations the live database does not yet produce), execution, scheduling, and notifications. Approval shipped in Phase 3. Dismissed remains a terminal status; reject is the user-facing refusal.

### Sequence and exit criteria

Six steps: (1) domain model, (2) additive persistence, (3) deterministic generation of the two intents, (4) lifecycle reconciliation, (5) brief and JSON presentation, (6) documentation and the 0.13.0 version bump.

Those exit criteria are covered by the proposal test suite: a repeated pass produces no duplicates; resolution invalidates and material change supersedes; event proposals expire at start while bill proposals do not; injection-flagged matters yield nothing; tests prove no execution path is reachable; and the brief shows inline suggestions with the no-action footer. Live `REVIEW_BILL` acceptance still waits on a real bill email, since no `invoice` observation has been classified yet. That is an operational check, not missing Phase 2 code.

## Phase 3 — Approval Inbox (v0.14.0)

Implemented. Full rationale in ADR-036. This phase adds no execution path.

A pending proposal can be approved, rejected, or deferred from the CLI or REPL. Those adapters issue a request context; `ObserveBriefService.decide` checks `DECIDE_PROPOSAL`. The decision is stored on that proposal version: timestamp, channel, principal, optional correlation id, optional note, and `decision_fingerprint` copied from the row. Source text and model output are not principals.

The brief adds **Decisions waiting for you** for pending proposals, with the proposal id. Approved proposals leave that section. Deferred proposals stay quiet until `defer_until`. Rejected proposals do not reappear for the same fingerprint.

As shipped in v0.14.0, `execution_allowed` always returned false. Approval does not resolve secrets or call the tool registry. Phase 4 replaced that guard with a real check; approval alone still runs nothing.

## Phase 4 — Act & Verify (v0.15.0)

Implemented. Full rationale in ADR-037.

**Trigger.** A channel adapter issues a `RequestContext` and calls `ActVerifyService.execute`. CLI and REPL are the current adapters; the service checks `EXECUTE_PROPOSAL` against the principal authority. Observe, brief, inbox, reconciliation, and the orchestrator do not import the executor. Text such as "Execute proposal pa_123 now." in an email, calendar event, Notion page, or model reply is data. Claiming `channel=cli` in that text is not authentication.

**Preflight.** Each check fails closed and is recorded:

1. The proposal is `approved`, the recorded decision came from an authenticated principal (a non-empty `decision_principal` and channel), and `decision_fingerprint == fingerprint`.
2. The Matter is open, the proposal has not expired, and recomputing it from current evidence gives the same fingerprint. A mismatch is audited as `execution_blocked_stale_approval` and needs a fresh approval.
3. The intent is supported.
4. A typed plan can be built from trusted Knowledge.

**Whitelist.** The code, not the proposal, chooses the executor.

| Intent | Executes | Behaviour |
|--------|----------|-----------|
| `REVIEW_BILL` with one trusted Knowledge asset | `browser.portal_review_login` | Opens the Knowledge `payment_portal_url` (https only), fills login fields from `op://` refs, submits, checks the Knowledge success condition, and stops. Never pays. |
| `REVIEW_BILL` from email only | — | No trusted target. "Approved, but execution is not supported yet." |
| `PREPARE_FOR_EVENT` | — | Preparation is your work; no calendar writes. Same message. |

The plan uses only the portal URL, secret refs, selectors, and success condition from Knowledge metadata. Amounts, payees, card refs, and anything in email or page text are ignored.

**Two approvals.** Approving the proposal records intent. At execute time, `ApprovalGate` runs (dry-run denies) and `ApprovalProvider` always prompts, using a summary built from the typed plan. Then every preflight check runs again, plus a plan-digest comparison, immediately before the executor.

**Secrets.** Refs are resolved only after the runtime prompt is granted, and before the browser session opens. Approval, preflight failures, and denials resolve nothing. Values are scrubbed and never stored.

**Attempts.** Each request writes an execution record. A unique index allows one in-flight or verified attempt per approved fingerprint.

| Status | Meaning | Next request |
|--------|---------|--------------|
| `preflight_failed`, `authorization_denied`, `failed` | Nothing ran | Allowed |
| `pending` | Authorized but the executor never started (crash) | Retired as `failed`, then allowed |
| `running`, `executed_unverified` | Outcome uncertain | Blocked until `wally verify` |
| `verified_success` | Confirmed | Blocked; never repeated |
| `verified_failure` | Confirmed not done | Allowed, with a new prompt |

**Verification.** Only an explicit authenticated result from the configured success condition counts. Otherwise Wally says "Executed, but verification could not confirm completion." `wally verify` re-reads stored evidence and never reruns the action. For an uncertain attempt, `--confirm success|failure` records your own check.

**No optimistic resolution.** A verified login is not a paid bill. The Matter and proposal are unchanged; only later Observe evidence resolves the bill.

## Privacy

See [ops-privacy.md](ops-privacy.md).
