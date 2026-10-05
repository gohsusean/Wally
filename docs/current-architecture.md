# Current Wally architecture

**Status:** Current implementation reference, checked against `ce47fcd` during
the 5 October 2026 handover audit. Documentation updated 6 October 2026.

Code and tests take precedence over this description. Runtime observations are
point-in-time evidence, not deployment guarantees. See [operations](operations.md)
for machine prerequisites and [engineering debt](engineering-debt.md) for known
exceptions and acceptance gaps. The older [architecture notebook](architecture.md)
preserves history; it is not the current system specification.

## Engineering direction

**New capability development should converge on the operational
proposal/capability/provenance architecture rather than creating additional
privileged behavior through the legacy conversational tool path.** This is the
accepted direction in [ADR-042](decisions.md#adr-042-new-capabilities-converge-on-the-operational-architecture).
It does not remove, refactor, or disable the legacy path. Migration and new
executors require separately reviewed implementation work.

## Two current paths

### Operational path: preferred foundation for new capabilities

```text
Gmail / Calendar / Knowledge reads
  -> SourceObserver -> Observations -> MatterReconciler -> Matters / priorities
  -> deterministic proposal generation / reconciliation -> brief / Approval Inbox
  -> explicit authenticated decision on a proposal version
  -> separate execute request -> ActVerifyService -> typed allowlisted executor
  -> stored evidence / independent verification -> durable execution record

External interface -> authenticate adapter -> fixed-channel Gateway
  -> PrincipalAuthority-issued RequestContext -> capability check -> ops service
```

Wally owns bookkeeping, policy, proposals, decisions, and attempts. n8n does not
own this loop or its authorization. Observation and proposal generation do not
call executors. CLI/REPL operational commands issue contexts in-process; they do
not pass through the Gateway. External adapters use the Gateway.

Implementation: [ops service](../src/wally/ops/service.py),
[Gateway](../src/wally/gateway/service.py), [Act & Verify](../src/wally/ops/act.py).
Rationale: ADR-034 through ADR-039 in [decisions](decisions.md).

### Legacy conversational path: still callable

```text
REPL message -> SessionStore / context -> Orchestrator / OpenAI tool loop
  -> ToolRegistry -> deterministic policy / risk classification / ApprovalGate
  -> optional CLIApprovalProvider prompt -> concrete provider action
```

This path can read/write operational Notion knowledge, draft/send email, create
calendar events, and trigger registered n8n workflows. Financial, destructive,
and irreversible actions are approval-gated in the tracked MacBook profile;
ordinary reversible writes are not necessarily prompted. It does not require a
canonical proposal or use RequestContext for every tool action.

The finance tool has deterministic routing and preflight checks, but accepts
model-supplied bill fields as interim trusted inputs. Extra parameters can differ
from verified fields, and payment-evidence claims are not bound to authenticated
records. Generic workflow triggering does not receive the finance tool's
verification path. These are current limitations, not permission to copy that
design: see D01/D02 in [engineering debt](engineering-debt.md).

Implementation: [orchestrator](../src/wally/orchestrator/core.py),
[ToolRegistry](../src/wally/orchestrator/tools.py),
[finance routing](../src/wally/runtime/execution_router.py). ADR-029 documents the
interim missing-metadata compromise. Act & Verify's no-payment restriction must
not be represented as a guarantee about every conversational tool.

## Domain, state, and continuity

- **Observation:** immutable source fact with source identity, timestamp,
  bounded text, trust/authority labels, and references. Gmail/calendar text is
  untrusted. Only designated structured Knowledge metadata drives obligations.
- **Matter:** canonical issue/open loop, linked to evidence and explainable
  priority. States: `open`, `watching`, `blocked`, `resolved`, `dismissed`;
  a declared enum state is not proof of an implemented transition into it.
- **ActiveMatter:** continuity handle, optionally pointing to a Matter, with
  multiple correlation IDs and `(channel, external_session_ref)` links.
  `active`/`archived` visibility changes discoverability, not Matter resolution.
- **Correlation ID:** request lineage, not enduring issue identity or authority.
- **RequestContext / Principal:** runtime-issued authority for a channel.
  **RequestProvenance:** persisted attribution without the authority grant.
- **Conversational Session:** full local messages, FTS5 recall, and optional
  summaries in SessionStore; separate from operational continuity records.

Gateway request evidence is bounded to eight items of 280 characters, forced
untrusted and hashed by Wally. Public context and audit events omit that capsule's
text. These minimization limits do not apply to full conversational transcripts.
See [ops privacy](ops-privacy.md) and [domain models](../src/wally/models/ops.py).

## Proposal, decision, execution, verification

Current intents: `prepare_for_event`, `review_bill`, `deliver_document`.
Proposals contain reference identifiers and display-only advice, not URLs,
credentials, provider arguments, amounts, or dispatchable tool names.
Fingerprints bind material inputs; display/prioritization/provenance does not
change version identity. Document delivery is grounded against the static exact
document/recipient titles and IDs in `config/chatgpt.yaml`, not live Notion lookup.

User decisions enter `approved`, `rejected`, or `deferred` from `proposed`, after
capability checks. Decision fields live on the reviewed proposal row and copy its
fingerprint; they are not a separate append-only decision table. Audit events
retain history when fields are cleared. Open states are proposed/approved/deferred.

Changed facts supersede/invalidate open versions; successors need a fresh
decision. Calendar proposals expire at event start; bills have no arbitrary
expiry. Rejected/dismissed versions remain closed, superseded history does not
resurrect, and eligible invalidated/expired versions may reopen without an old
decision. Elapsed defer normally clears the decision and returns to proposed;
delivery currently skips this release logic (D06). No current user command
enters proposal `dismissed`; Telegram notification dismissal is a separate state.

Only `review_bill` has an operational executor: portal login/review, not payment.
Preflight fetches the canonical approved-classification finance Knowledge asset,
checks supporting evidence and approval fingerprint, and builds a typed plan
using HTTPS portal configuration, credential refs, selectors and an auth success
condition. A fresh execution prompt is required even if the generic gate would
allow the action; approval/target/plan are rechecked afterwards before secrets
resolve and the executor runs. Prepare-event and document-delivery remain advice.

Attempts persist preflight/authorization failure, `pending`, `running`,
`executed_unverified`, verified outcomes, or `failed`. A compare-and-set starts
execution; partial unique indexes limit blocking attempts per fingerprint.
Pending is known not executed and can be retired on another explicit request.
Running/unverified is uncertain and blocks unsafe retries. Verified success is
not repeated; known failure can permit a new explicit attempt and prompt.

Verification checks stored evidence or an authorized explicit human confirmation;
it does not rerun the browser. Portal-login success does not pay a bill or resolve
its Matter. Only later Observe evidence changes the operational obligation.
The live Notion adapter does not yet populate the metadata needed for this plan
or recurring obligations; fixture coverage is not live readiness (D03).

## Identity and interface boundaries

PrincipalAuthority owns a per-process HMAC key and grants the six capabilities:
submit request, read context, link channel, decide proposal, execute proposal,
verify execution. Grants bind principal/channel/authentication and are neither
persisted nor copied into proposal fingerprints. The system is single-owner;
local terminal identity assumes that operator is the owner.

Gateway authenticates distinct adapter credentials, fixes channel by registration,
rejects identity claims in payloads, issues context, checks capability, and calls
the service. Execute also requires an approval-capable adapter. It is primarily
interface-neutral, with additional ChatGPT host checks on decisions.

- **CLI/REPL commands:** all six capabilities; approve/reject/defer and explicit
  execute/verify. Natural-language REPL tools use the separate legacy path above.
- **ChatGPT MCP/HTTP:** loopback only; initialize returns no private state, tool
  listing/calls require bearer authentication. Local owner-secret/PKCE-style JSON
  grant mints in-memory bearer tokens with no implemented expiry/revoke. Reads
  default on; submit/link/visibility writes are opt-in. Decisions need writes,
  allowlisted subject, both secrets, and operator confirmation configuration.
  Caller `_meta` is not cryptographically authenticated OpenAI identity; the
  configuration flag is not per-call proof of a click. Hosted auth/confirmation
  remains unvalidated; keep decisions disabled. No execute/verify capabilities.
- **Telegram:** owner numeric ID in a private chat, fixed channel; submit/read/
  link/decide only. Groups/edits ignored. Nonce-backed Approve/Reject checks bind
  owner, chat, proposal and fingerprint; repeats do not decide again. `Not now`
  dismisses the card only; text approval redisplays a card. No Later/defer button,
  execution, verification, reasoner, scheduler, or public webhook.
- **Generic Unix socket:** transport library with registration-dependent
  permissions, exercised by tests; not a currently deployed standalone server.
- **Home Assistant/Alexa:** no implemented Wally interface. Configuration stubs
  do not enable a provider; ADR-022 defines the product boundary.

`create_app` attaches an unregistered Gateway and opens no listener. Telegram and
ChatGPT build separate restricted runtimes with their own authority and shared
operations SQLite state; neither supplies an ActVerifyService. Downstream Notion,
Google, n8n, OpenAI, Playwright, and secrets adapters are provider integrations,
not additional owner-authentication surfaces.

## Current, historical, and future evidence

Current source version is v0.18.0; there are no release tags at the audit baseline.
Git begins with a recovered working tree on 30 September 2026; the baseline
already had Assess & Propose. Earlier milestones have ADR/documentary evidence,
not an independently reconstructible implementation commit history.

Scheduling/proactive triggers, more executors, hosted ChatGPT completion, contacts,
and optional HA context remain [roadmap](roadmap.md) work. Semantic/vector retrieval,
federation and retriever/store extraction are historical proposals; current recall
is SQLite FTS5. n8n exports and proven recovery are missing, not delivered features.
The [backlog](engineering-debt.md) records defects without claiming fixes.
