# Chief of Staff — Observe, Brief & Propose

**Version:** 0.13.0 (latest shipped)  
**Status:** Current milestone is v0.14 Approval Inbox (not production v1.0)

Wally is building persistent operational understanding: what is happening, what changed, what is still open, what can wait, and what the user might do next. Phases 1 and 2 are **read / assess / brief / propose only**. Phase 3 will record explicit approval without executing.

## Loop

| Phase | Status | Behaviour |
|-------|--------|-----------|
| 1 Observe → Assess → Brief | **Complete (v0.12)** | Ingest signals, reconcile Matters, print a brief |
| 2 Assess & Propose | **Complete (v0.13.0)** | Durable suggestions in the brief; do not execute |
| 3 Approval Inbox | **Current (v0.14)** | Human authorization of proposals; approval is not execution |
| 4 Act & Verify | Future | Execute after approval; verify outcomes |
| 5 Daily-driver hardening | Future | Scheduling, noise, notification UX |

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
```

In the REPL: `/brief` (add `--no-refresh` to skip a new observe pass).

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

`proposed` → `superseded` | `invalidated` | `expired` | `dismissed`. There is no `approved` state; the enum does not contain one.

| Transition | Rule |
|------------|------|
| Superseded | The same matter and intent regenerate with materially changed content. The prior row is kept for audit. |
| Invalidated | The underlying Matter resolves, or its supporting facts stop holding. |
| Expired | **Calendar only.** A `PREPARE_FOR_EVENT` proposal expires at event start. |
| Dismissed | Reserved for Phase 3. No user-facing dismissal ships in v0.13.0. |

Bill proposals carry no timer. They close only on resolution, material change, or supersession, so an overdue unresolved bill is never hidden by an arbitrary expiry window. Repeated observe passes are idempotent: the same facts regenerate the same proposal rather than a duplicate.

### Presentation

Proposals render inline beneath their Matter, prefixed `↳ Suggested:` so advice is visually distinct from observed fact. One global footer states that Wally has taken no action. `--json` gains a parallel proposals field and stays schema-stable for existing consumers.

### No-execution boundary

Phase 2 performs no tool call, no provider write, no secret resolution, no capability routing, and no approval bypass. The proposal service is constructed without capability, secrets, or browser dependencies, and tests assert those paths are unreachable from generation. Persistence is additive: a `proposals` table created with `CREATE TABLE IF NOT EXISTS`, leaving v0.12.1 rows unmodified — generation reads Matters but writes nothing back to them — with no reset required.

### Deferred

`FOLLOW_UP` (it depends on sent-mail observations the live database does not yet produce), dismissal UI, approval, execution, scheduling, and notifications.

### Sequence and exit criteria

Six steps: (1) domain model, (2) additive persistence, (3) deterministic generation of the two intents, (4) lifecycle reconciliation, (5) brief and JSON presentation, (6) documentation and the 0.13.0 version bump.

Those exit criteria are covered by the proposal test suite: a repeated pass produces no duplicates; resolution invalidates and material change supersedes; event proposals expire at start while bill proposals do not; injection-flagged matters yield nothing; tests prove no execution path is reachable; and the brief shows inline suggestions with the no-action footer. Live `REVIEW_BILL` acceptance still waits on a real bill email, since no `invoice` observation has been classified yet. That is an operational check, not missing Phase 2 code.

## Privacy

See [ops-privacy.md](ops-privacy.md).
