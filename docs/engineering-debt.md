# Active engineering debt and deployment gaps

**Status:** Open findings from the 5 October 2026 audit of `ce47fcd`, recorded
6 October 2026. D01/D02 are resolved by the focused finance stabilization below;
the other findings remain open. Priority expresses impact, not proof that harm occurred.

Current paths are described in [current architecture](current-architecture.md);
operational prerequisites in [operations](operations.md). New privileged
capabilities follow [ADR-042](decisions.md#adr-042-new-capabilities-converge-on-the-operational-architecture).
Close a finding only with implementation/deployment evidence and its acceptance
conditions; update this record when the evidence changes. Synthetic tests do not
by themselves establish live provider readiness.

## D01 — Legacy finance verifies different fields from execution (high)

**Status:** Resolved in code, 6 October 2026; live metadata readiness remains D03.

**Original verified defect:** the conversational finance adapter accepted model-supplied `bill`
fields without canonical rebinding. Extra execution parameters take precedence
over bill account/payee/destination fields, while verification compares bill and
statement. An in-memory synthetic probe routed a different account without being
blocked. Human approval remains a mitigation; canonical Act & Verify is separate.
ADR-029 acknowledges interim model fields but does not justify this mismatch.

Evidence: [routing](../src/wally/runtime/execution_router.py),
[finance checks](../src/wally/runtime/finance_safety.py),
[adapter](../src/wally/adapters/finance/local.py).

**Acceptance:** fetch/bind canonical approved knowledge; verify the exact resolved
execution payload, including destination/account and amount. Conflicting model
or parameter overrides fail before approval/provider calls. Regression tests
cover equal verified inputs with a different execution destination and canonical
drift. New capability work must not reproduce the legacy privilege path.

**Resolution:** runtime fetches the exact asset ID from approved operational
finance knowledge; requires canonical amount/currency/payee and applicable target
fields. Model bill fields/parameters are assertions only: conflicting or unknown
keys fail before approval. Verification checks canonical versus resolved payload
and statement; the reviewed digest binds metadata, dispatch and registered
workflow target. Execution requires an authenticated execute capability, a fresh
prompt even when the generic gate allows, and matching re-fetched inputs after
the prompt. Credential injection cannot populate financial slots. Generic
financial workflow tools (including aliases/payment-tagged misclassifications)
are refused. Regression coverage: [finance trust](../tests/test_finance_trust.py)
and [verification](../tests/test_verification_engine.py). No live payment was run.

## D02 — Legacy payment evidence has weak provenance (high)

**Status:** Resolved in code, 6 October 2026, using explicit authenticated human
verification; automated payment-completion evidence is not implemented.

**Original verified defect:** `user_confirmed=True`, `workflow_status=triggered` and a provider name
can satisfy the paid-write guard as caller arguments. They are not linked to an
authenticated decision, completed execution, or independent verification record.
Generic `workflow_trigger` also bypasses the finance tool's verification pipeline
even though registered financial workflows still require a financial prompt.

Evidence: [evidence policy](../src/wally/runtime/finance_safety.py),
[tool dispatch](../src/wally/orchestrator/tools.py).

**Acceptance:** bind paid-state evidence to trusted runtime records for the same
obligation/action; model assertions and mere webhook acceptance cannot establish
completion. Every reachable financial route applies equivalent verification and
authorization. Tests reject forged confirmation/provider/workflow evidence and
generic-tool attempts to bypass financial verification.

**Resolution:** no tool-payload evidence type establishes completion. Every
finance create/update (and detected paid claims/evidence elsewhere) requires a
runtime-issued verify capability plus a fresh human prompt to independently
check the exact record/write. Target/current asset are re-read after confirmation;
drift blocks the write. Audit stores authority-free provenance and a bound write
fingerprint, never grants. Forged, foreign-authority and restricted-channel
contexts fail. Dispatch/login results leave paid state and Matters unchanged;
webhook acceptance is explicitly unverified. All generic financial workflow
routes are blocked rather than relying on model tool choice. Tests cover both
forged claims and the legitimate human path. See [ADR-043](decisions.md#adr-043-canonical-legacy-finance-dispatch-and-authenticated-human-evidence).

## D03 — Live Notion metadata and finance-role mapping missing (high)

**Verified:** Notion `_page_to_asset` does not populate KnowledgeAsset.metadata.
Recurring obligations and portal review depend on fields supplied by fixtures.
The audited local registry had no finance role and tracked overrides supplied
none. ADR-029 leaves schema mapping as a review trigger. **Unknown:** whether an
approved schema exists outside the repo.

Evidence: [Notion adapter](../src/wally/adapters/notion/adapter.py),
[observation ingest](../src/wally/ops/observe.py), [plan construction](../src/wally/ops/act.py).

**Acceptance:** agree/document schema and designated database roles, implement
typed mapping for cadence/due and portal/credential-reference/auth fields, and
test actual Notion-shaped responses through ingestion/preflight. Missing or
malformed fields fail closed. Complete a separately authorized auth-only live
validation before describing those paths as operationally ready; no payments.

## D04 — Unmatched receipt can resolve an unrelated obligation (high)

**Verified:** reconciliation assigns an unmatched receipt to the sole open
knowledge-backed finance Matter without matching financial identity. This
conflicts with the documented FYI rule. False closure/proposal withdrawal is an
**inferred risk**, not an observed live incident.

Evidence: [receipt fallback](../src/wally/ops/reconcile.py),
[stated behavior](chief-of-staff.md#cli).

**Acceptance:** owner approves an explicit matching rule. An unrelated receipt
cannot resolve a Matter based only on cardinality; unmatched/ambiguous evidence
stays FYI or awaits explicit matching. Tests cover zero/one/multiple obligations,
identity mismatches and proposal retention. Update ADR-034's scoped rationale.

## D05 — Telegram processed-update/cursor crash seam (medium)

**Verified:** update record and cursor advance commit separately; the seen-update
branch returns without repairing the cursor. **Inferred consequence:** a crash
between commits can repeatedly fetch the same update until a higher ID arrives.

Evidence: [handler](../src/wally/telegram/service.py),
[ingress commits](../src/wally/telegram/ingress.py).

**Acceptance:** recover the processed-marker/old-cursor state without repeating
side effects or needing another update. Inject a crash after record/before advance,
restart, and verify cursor progress plus unchanged request/proposal/decision counts.
Retain replay safety for crashes before the processed marker too.

## D06 — Delivery defer never releases (medium)

**Verified:** delivery reconciliation returns before elapsed-defer logic. A
synthetic fake-store probe retained `deferred` beyond its deadline. This affects
CLI/REPL deferral; Telegram currently has no Later/defer button.

Evidence: [delivery reconciliation](../src/wally/ops/proposal_reconcile.py).

**Acceptance:** all deferrable intents return to proposed after elapsed defer,
clear decision fields, retain audit history and remain non-executing. Tests cover
delivery, finance and calendar plus stale fingerprint/closed-Matter cases.

## D07 — Incomplete incremental observation semantics (medium)

**Verified:** Knowledge fingerprints use asset/period, ignoring edits within a
period; calendar snapshots use start/summary, omitting other material fields.
Calendar listing does not paginate; disappearance from the upcoming result can
resolve a Matter. Incomplete results can therefore masquerade as resolution.

Evidence: [observer](../src/wally/ops/observe.py),
[Google adapter](../src/wally/adapters/google/adapter.py),
[calendar reconciliation](../src/wally/ops/reconcile.py).

**Acceptance:** define material source changes and explicit disappearance/horizon
rules, ingest revisions without duplicate Matters, and require complete source
results before absence-based closure. Test same-period due/cadence edits, calendar
end/description changes, pagination, partial failures and horizon movement through
the real adapter response shapes. Updated material inputs invalidate old approvals.

## D08 — n8n definitions/deployment not reproducible (high deployment gap)

**Verified:** workflow registry exists, but `workflows/` contains no exports and
the deployment script only reports them. ADR-013's automated deployment is intent,
not implemented behavior. **Unknown:** actual remote workflow effects/authentication.

Evidence: [registry](../config/workflows.yaml),
[inventory helper](../scripts/deploy_workflows.py), [ADR-013](decisions.md#adr-013-wally-owned-n8n-workflow-definitions).

**Acceptance:** obtain/review secret-free deployed exports, record effects,
credential references, auth, idempotency and completion criteria. Document and
demonstrate reproducible deployment/rollback to a non-production target. Distinguish
webhook acceptance from completion and prove what the backup workflow actually backs up.

## D09 — Backup and restore procedure unknown (high operational gap)

**Unknown:** working backup destination, schedule, access/encryption, retention,
restore method and accepted recovery objectives. A workflow named weekly-backup
does not establish any of these. Git excludes private state/credentials.

**Acceptance:** agree recovery point/time objectives; document consistent backup
of sessions, registry, operations and audit plus credential-source recovery by
reference. Restore to an isolated location and demonstrate preserved classifications,
decisions, relationships, uncertain executions and Telegram replay behavior.
Do not reconnect a restored instance or clear uncertain state automatically.
See [recovery requirements](operations.md#backuprecovery-not-established).

## D10 — Status notifications can be stranded without chat (medium)

**Verified:** decision enqueue updates an empty chat after it becomes known;
status enqueue returns the existing row without doing so. Claim requires a chat.

Evidence: [outbox](../src/wally/telegram/outbox.py).

**Acceptance:** execution-status notifications created before first private-chat
discovery become deliverable after owner authentication, exactly once per dedupe
key under known delivery. Test restart, ownership and late-chat discovery.

## D11 — Telegram lease/delivery reliability limits (medium)

**Verified:** the lease renews at poll start, not throughout handling/delivery.
**Inferred:** slow processing longer than 90 seconds permits another holder while
the first still acts. Separate, **documented accepted limitation:** a crash after
Telegram accepts sendMessage can show duplicate cards; no exactly-once guarantee.

Evidence: [poll loop](../src/wally/telegram/poll.py),
[lease](../src/wally/telegram/ingress.py), [ADR-041](decisions.md#adr-041-telegram-decides-through-a-server-side-nonce).

**Acceptance:** bound processing or renew/fence ownership so two consumers cannot
both act after expiry; test long processing and takeover. Preserve idempotent
decisions and make retry/terminal-failure status understandable. Keep at-least-once
delivery explicit; acceptance does not require impossible exactly-once remote sends.

## D12 — Incomplete audit attribution/outcome semantics (medium)

**Verified:** some Gateway handlers omit available principal/channel/correlation;
some legacy tool exceptions return errors without denial and are audited as success.
Structured response facts/inferences remain empty scaffolding.

Evidence: [Gateway](../src/wally/gateway/service.py),
[tool errors](../src/wally/orchestrator/tools.py),
[orchestrator audit](../src/wally/orchestrator/core.py),
[response composition](../src/wally/orchestrator/response.py).

**Acceptance:** all relevant authenticated operations carry attribution without
grants/evidence text; denied, failed, accepted and completed outcomes are distinct.
Tests cover exception/returned-error paths and each handler. Either implement
meaningful epistemic response fields or explicitly document their limited contract.

## D13 — Local file/privacy and credential-access policy (medium)

**Verified audit observation:** `.env`, databases and audit files had `0644` bits;
private data is plaintext. Keychain access trusts the interpreter, not a Wally
script. Those facts alone do not establish access by every other account; parent
permissions and machine protections matter. **Unknown:** accepted threat/retention policy.

**Acceptance:** document permitted local readers, retention, disk/backup protection
and Keychain interpreter scope; validate effective access with non-secret fixtures.
Implement any approved permission/retention changes in a separate operational task,
without exposing values, losing history, or breaking launchd credential access.

## D14 — Installed package metadata and documentation currency (low)

**Verified audit observation:** source/pyproject/lock version v0.18.0, installed
editable distribution metadata v0.13.0. There were no tags/release notes. This
patch corrects documentation navigation/scope; it does not reinstall or tag releases.
Application prompts still contain historical wording and are intentionally unchanged
because prompt edits can change behavior.

**Acceptance:** separately approved environment reconciliation makes installed and
imported versions agree; packaging checks catch drift. Define/use a release-record
policy and keep current architecture distinct from history. Review stale product
prompts with behavioral validation in a separate change, never as incidental docs cleanup.

## D15 — Hosted ChatGPT authentication/confirmation unvalidated (deployment gate)

**Verified:** custom loopback owner-secret grant; caller-supplied subject metadata,
operator-attested confirmation flag, in-memory tokens without expiry/revocation.
**Documented:** hosted connectivity/confirmation remains unvalidated. Current
execute/verify absence is deliberate, not a defect.

**Acceptance:** before enabling hosted decisions, demonstrate compatible authenticated
connection, independently trustworthy identity/confirmation constraints, lifecycle
and token handling, and rejection of forged metadata/stale versions. Record the
deployment rationale. Keep decisions disabled and execute/verify unavailable until
separately reviewed; do not tunnel the local owner-secret grant as a workaround.

## D16 — Dormant socket transport and claim-validation limits (low)

**Verified:** standalone Gateway socket has no accepted-client timeout, weak
malformed-UTF8 handling, and unlinks an existing bind path. Identity-key recursion
stops beyond depth eight; ignored deep fields currently do not select authority.
The transport is test/library code, not a deployed standalone service.

**Acceptance:** before deployment, stalled/malformed clients cannot terminate/block
all calls, socket ownership/path replacement is safe, permissions are deliberate,
and payload limits match documented validation. Tests cover these boundaries and
preserve fixed-channel authentication. The socket test must not overwrite live state.

## Handover validation record

The 5 October audit collected 451 tests; 448 audit-safe tests passed across two
runs and Ruff passed. Two socket tests needed permission to bind. The two `.env`
mutation tests and login-Keychain round-trip test were not run. No live provider
or full hosted ChatGPT validation was performed. Those are dated evidence, not
permanent agent rules or a certification of all paths.

No broad historical chat extraction is required. Required external inputs are
current n8n exports, recovery arrangements, any agreed Notion schema, and the
owner's receipt-matching decision; see [operations](operations.md).
