# Current Wally architecture

**Status:** Current implementation reference, checked against `ce47fcd` during
the 5 October 2026 handover audit. Documentation updated 6 October 2026.
The D01/D02 stabilization updates the legacy finance boundary under ADR-043.
D03 adds the certified financial catalog under ADR-044; live validation is pending.
The 10 October extension adds default-disabled scoped Notion edits under ADR-045;
see [interface-neutral approvals](interface-neutral-approvals.md) for tested code
and remaining deployment/confirmation gates.

The 11 October [Telegram extension](telegram-notion-approvals.md), ADR-046, makes
Telegram the default review/authorization interface for explicitly eligible scoped
metadata edits. One owner interaction produces separate decision and execution
confirmations; the trusted worker alone holds scoped execute/verify capability.
Native local confirmation stays dormant. All write gates remain default-disabled.

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
Gmail / Calendar / certified financial instance reads
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

Finance requests now name a canonical bill asset ID. Runtime fetches approved
operational finance metadata, rejects conflicting/unknown assertions, verifies
the canonical dispatch against statement evidence, and binds a digest to the
bill, payload and registered workflow target. An authenticated execute capability
and fresh prompt are mandatory. After the prompt, canonical inputs and routing
must still match before credentials resolve or the exact dispatch runs. Generic
financial workflow calls/aliases are blocked. Missing live metadata fails closed
(D03); model dictionaries are no longer the fallback.

Tool-supplied `payment_evidence` never establishes completion. All finance
knowledge create/update calls require an authenticated verify capability and a
fresh human check of the exact record/write, followed by revalidation. Recognized
paid claims/evidence on other targets also require this check. Audit binds human
verification to target/write fingerprint and authority-free provenance. No new
payment-completion provider/record or Matter-resolution route is introduced.
Dispatch and portal login remain insufficient evidence; see
[ADR-043](decisions.md#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence).

Implementation: [orchestrator](../src/wally/orchestrator/core.py),
[ToolRegistry](../src/wally/orchestrator/tools.py),
[finance routing](../src/wally/runtime/execution_router.py). ADR-029 documents the
historical missing-metadata compromise, superseded for caller-supplied authority
by ADR-043. Act & Verify's no-payment restriction must
not be represented as a guarantee about every conversational tool.

## Domain, state, and continuity

- **Observation:** immutable source fact with source identity, timestamp,
  bounded text, trust/authority labels, and references. Gmail/calendar text is
  untrusted. Only enabled, certified typed financial instances drive canonical obligations.
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

D04 reconciliation follows the owner-approved invariant: financial receipt
resolution requires binding to the canonical obligation. Candidate uniqueness,
Gmail thread identity, matching amounts and contextual correlation cannot supply
that binding. It is not implemented today, so **all financial receipts remain
FYI** and leave obligation attention/proposals unchanged. No invoice-thread
exception exists. Ordinary thread replies cannot close or advance financial
Matters either. Notes have a separate identity and cannot become matches by
replay or disappearance of competitors; legacy FYI history is preserved.
No human association API or remote permission is added. See
[D04](engineering-debt.md#d04--unmatched-receipt-can-resolve-an-unrelated-obligation-high)
and ADR-034 for the scoped rationale; D03 is implemented in code; its separately authorized live pilot remains pending.

## Proposal, decision, execution, verification

Current intents: `prepare_for_event`, `review_bill`, `deliver_document`,
`edit_notion_record`. The edit intent references a separately stored immutable
review specification; proposals themselves remain non-dispatchable.
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

The generic Act & Verify executor supports `review_bill` only: portal login/review, not payment.
Preflight rereads the enabled certified financial chain and occurrence evidence,
checks the approval fingerprint, and builds a typed plan
using certified canonical provider/account/profile configuration and an auth success
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
its Matter. Financial receipt resolution awaits future canonical obligation binding; current
receipts remain FYI.
The dedicated Notion finance reader supplies provisional typed candidates; generic
metadata supplies no authority. Code/fixture validation does not establish live readiness.

## Identity and interface boundaries

PrincipalAuthority owns a per-process HMAC key and grants nine capabilities:
submit request, read context, link channel, decide proposal, execute proposal,
verify execution, local-owner financial certification, scoped Notion edit execution
and scoped Notion edit verification. Grants bind principal/channel/authentication and are neither
persisted nor copied into proposal fingerprints. The system is single-owner;
local terminal identity assumes that operator is the owner.

Gateway authenticates distinct adapter credentials, fixes channel by registration,
rejects identity claims in payloads, issues context, checks capability, and calls
the service. Execute also requires an approval-capable adapter. It is primarily
interface-neutral, with additional ChatGPT host checks on decisions.

- **CLI/REPL commands:** existing local operator capabilities; approve/reject/defer and explicit
  execute/verify. Natural-language REPL tools use the separate legacy path above.
- **ChatGPT MCP/HTTP:** loopback only; initialize returns no private state, tool
  listing/calls require bearer authentication. Local owner-secret/PKCE-style JSON
  grant mints in-memory bearer tokens with no implemented expiry/revoke. Reads
  default on; submit/link/visibility writes are opt-in. Hosted decisions are disabled
  in code even when the old confirmation flag is set. Caller metadata, bearer
  authentication and a configuration flag do not prove a human's exact decision.
  No execute/verify capabilities. Exact-edit read/staging translations exist but
  the shipped HTTP composition does not attach the edit service.
- **Local Codex stdio:** explicit `python -m wally.codex` restricted runtime. Only
  submit/read/scoped reconciliation in default Telegram mode; no decision/execute
  tools or broad financial execution/certification. The seven-tool native path
  remains dormant behind explicit local mode. Policy ships empty/disabled; Desktop
  discovery and actual Telegram/Notion acceptance remain unvalidated.
- **Telegram:** owner numeric ID in a private chat, fixed channel; submit/read/
  link/decide only. Groups/edits ignored. Nonce-backed Approve/Reject checks bind
  owner, chat, proposal and fingerprint; repeats do not decide again. `Not now`
  dismisses established-intent cards only; text approval cannot decide. Eligible
  metadata cards under ADR-046 support exact Approve & Execute/Reject/Later and full
  batches. Separate scoped worker authorization/execution/verification uses the
  existing service; Telegram itself gets no execution capability. No reasoner,
  general scheduler or public webhook is introduced.
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

## Certified finance catalog

[ADR-044](decisions.md#adr-044-certified-financial-identities-and-local-owner-certification)
and the [operator guide](finance-catalog.md) define Wally-owned subject/provider/
account/definition/instance identity, provisional typed Notion candidates,
immutable material versions and local-owner certification. Stable property IDs,
complete source/relation reads and explicit designation precede certification.
Seven gates separate ordinary knowledge from financial authority. Identity and
portal scopes are independent; portal enablement needs exact-version governed
auth-only acceptance. Reviewed runtime profiles own browser configuration.

Complete enumeration retains malformed rows as Needs Attention diagnostics with
hashed partial identity constraints. Unknown coordinates never prove uniqueness.
Potential collisions and unavailable dependencies invalidate affected certificates
and chains; provably disjoint certified chains remain usable. Source/schema failures
block the affected source and any uniqueness proof that needs its complete inventory.

Additive `finance_*` tables reside in the operational database. No rows or live
classifications are migrated automatically. Generic knowledge metadata cannot
drive canonical obligations or portal plans. Manual primary-evidence intake keeps
expected occurrences free of payable facts; issued amounts use exact decimals.
Finance edits invalidate certification and produce same-period observation revisions.
Receipts remain FYI; no payment/settlement/scheduler is introduced. The separately
authorized first utility/property chain remains necessary before live readiness.


## Interface-neutral scoped Notion edits

[ADR-045](decisions.md#adr-045-interface-neutral-approval-centralized-authorization)
establishes **interface-neutral approval, centralized authorization**. The shared
`NotionEditService` retains proposal decisions in the existing operational rows,
uses exact immutable specifications for selected versions, and requires runtime
human confirmation in addition to channel authentication. Scoped batches commit
atomically; approval never starts execution. Current metadata scope is existing
select options for amount policy/frequency. Unknown/truncated protected state
fails closed. Re-read after human confirmation and credential acquisition; claim
the canonical page before a write; independently verify expected business state.
Financial certification is invalidated, never silently retained or reissued.

New additive specifications, claims and human-review audit tables preserve existing
state. Default policy registers no target or confirmer and enables no write. The
local native provider is compiled/signed and boundary-tested; the
[isolated rollout](local-codex-rollout.md) records installation and actual negative
envelope probes, not successful biometric or live Notion acceptance. Read-only
inspection reconciles interrupted state without releasing claims. Hosted
confirmation remains unimplemented. Notion external-writer concurrency and actual
UI/enrollment/provider readiness are explicit rollout gates in the
[implementation guide](interface-neutral-approvals.md). No new payment/scheduler
or external approval endpoint is introduced.
