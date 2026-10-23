# Active engineering debt and deployment gaps

**18 October financial policy update:** [ADR-050](decisions.md#adr-050-financial-certification-follows-reliable-evidence) supersedes D03's separate identity-certification prompt/attestations. Evidence certifies records independently; unavailable sources block use without destroying valid verification. Payment/portal authorization is unchanged.

**Status:** Open findings from the 5 October 2026 audit of `ce47fcd`, recorded
6 October 2026. D01/D02 and D04 are resolved in code by the focused stabilizations below;
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

**Status:** Resolved in code, 6 October 2026, under [ADR-044](decisions.md#adr-044-certified-financial-identities-and-local-owner-certification).
Live metadata readiness and the first auth-only validation remain pending owner work.

The typed catalog now owns immutable property/entity/provider/account/definition/
instance IDs, stable Notion property mappings, exact money, complete pagination/
relations, duplicates, material versions and persistent certificates. Seven source/
record/chain gates fail closed. Local-owner certification requires a dedicated
capability, primary evidence/attestations, fresh exact-version confirmation and
post-prompt revalidation. Identity/tracking and portal scopes are separate; portal
readiness also requires governed exact-version auth-only acceptance. Generic
knowledge metadata no longer establishes obligations or portal plans.

Existing Recurring Bills is generalized conceptually; additive schema migration
preserves all database/page identities and rows. No live schema, data cleanup,
certification or chain enablement was performed during implementation. Restricted
account serialization suppresses customer IDs. Receipts remain FYI under D04;
D03 provides no payment or settlement path.

The initial implementation coupled every record parse failure to whole-catalog
invalidation. The focused D03 follow-up resolves that implementation limitation:
complete enumeration retains hashed partial identity constraints for malformed
rows, blocks possible collisions/dependencies, and allows provably unrelated
certified chains to remain usable. Source/schema failures remain fail-closed.
Blank rows with no usable identity proof can still block the affected record kind;
there is no owner-checkbox bypass. No live data cleanup or pilot was performed.

Evidence: [typed catalog](../src/wally/finance/service.py),
[Notion reader](../src/wally/finance/notion.py),
[Notion-shaped and boundary tests](../tests/test_finance_catalog.py),
[operator procedure and limitations](finance-catalog.md).

**Live acceptance:** separately authorize one utility/property chain, review and
apply only necessary additive schema changes, pin stable property mappings,
validate primary documents, certify/enable identity, manually intake an occurrence,
then certify/review the portal profile and perform auth-only acceptance. Do not
describe configured/fixture-tested paths as live operational readiness.

## D04 — Unmatched receipt can resolve an unrelated obligation (high)

**Status:** Resolved in code, 6 October 2026, under the owner-approved
fail-closed rule below. Canonical receipt matching remains unavailable.

**Original verified defect:** reconciliation assigned an unmatched receipt to the sole open
knowledge-backed finance Matter without matching financial identity. This
conflicts with the documented FYI rule. False closure/proposal withdrawal is an
**inferred risk**, not an observed live incident.

Evidence: [receipt fallback](../src/wally/ops/reconcile.py),
[stated behavior](chief-of-staff.md#cli).

**Acceptance:** owner approves an explicit matching rule. An unrelated receipt
cannot resolve a Matter based only on cardinality; unmatched/ambiguous evidence
stays FYI or awaits explicit matching. Tests cover zero/one/multiple obligations,
identity mismatches and proposal retention. Update ADR-034's scoped rationale.

**Owner-approved rule / resolution:** financial receipt evidence must not resolve
or materially advance a Matter until it can be bound to the canonical obligation
being tracked. Candidate uniqueness, email-thread association and contextual
correlation alone are insufficient. The owner explicitly chose to keep **all
financial receipts FYI until canonical bill matching exists** on 6 October 2026.
No invoice-thread exception is implemented.

The sole-Matter fallback and receipt auto-resolution are removed. All receipts,
including same-thread/amount matches and claimed canonical IDs or confirmations,
remain separate, idempotent FYI records. Receipt wording classified as an ordinary
reply cannot close or advance a financial Matter through thread lookup either.
Notes cannot resolve themselves on replay, attach themselves to later invoices,
change obligation attention or invalidate its proposal/decision. Legacy FYI rows
retain their identities and history; new invoices do not reuse them.

Baseline regressions reproduced sole-Matter false resolution, FYI replay
self-resolution and a financial reply closure. Coverage includes zero/one/multiple
obligations, disappearing competitors, ambiguous/shared threads, identity claims,
amount/title similarity, injection, legacy notes and proposal/decision retention:
[receipt reconciliation](../tests/test_ops_receipt_reconcile.py). Correct canonical
calendar reconciliation and nonfinancial reply closure still work.

Current ingestion has no canonical receipt-to-obligation binding or authenticated
human association operation. No override is added; execution confirmation verifies
portal review, never payment/receipt association. D03 remains out of scope. Missing
infrastructure fails closed; this fix does not implement automatic matched-payment
resolution. No historical financial closure is reopened or repaired automatically;
suspected false associations need separate operator review of preserved evidence.

## D05 — Telegram processed-update/cursor crash seam (medium)

**Resolved in code, 11 October 2026:** update record and cursor still commit
separately, but replay repairs a stale cursor without repeating effects. Synthetic
tests exercise processed-marker/old-cursor recovery without needing another update.

Evidence: [handler](../src/wally/telegram/service.py),
[ingress commits](../src/wally/telegram/ingress.py).

**Acceptance:** recover the processed-marker/old-cursor state without repeating
side effects or needing another update. Inject a crash after record/before advance,
restart, and verify cursor progress plus unchanged request/proposal/decision counts.
Retain replay safety for crashes before the processed marker too.

## D06 — Delivery defer never releases (medium)

**Resolved in code, v0.18.1:** unchanged delivery proposals release elapsed
deferrals through the existing store transition, clearing decision fields while
preserving audit history. Closed Matters and changed document/recipient bindings
invalidate the review before any release. Calendar, bill and delivery regression
checks verify that release is inert and idempotent.

Evidence: [reconciler](../src/wally/ops/proposal_reconcile.py),
[regressions](../tests/test_ops_approvals.py). Telegram `Not now` still dismisses
only a notification and does not schedule a reminder.

## D07 — Incomplete incremental observation semantics (medium)

**Resolved in code, v0.18.1:** finance versions/bindings remain governed by D03.
Google calendar reads now consume every page before returning a complete result.
Failed/malformed pages and repeating page tokens return no successful prefix;
existing calendar checkpoints, Matters and approvals remain intact. Unmarked or
partial provider lists cannot establish absence.

Calendar fingerprints include start, end, title, description, location and
attendees. Revisions retain one Matter and supersede old proposal authority.
A complete read can establish disappearance only when the previously known event
still overlaps the requested window. Rolling/shrinking horizons do not emit
cancellation or resolve a Matter; retained checkpoints permit future revisions.
A newly observed revision can conservatively replace an older start/title-only
fingerprint without rewriting history.

Evidence: [observer](../src/wally/ops/observe.py),
[adapter](../src/wally/adapters/google/adapter.py),
[real-response-shaped regressions](../tests/test_calendar_observation.py).
These are isolated checks, not live Google acceptance.

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

## D09 — Basic local iCloud copy (v0.18.1 scope)

**Implemented:** allowlisted current copies of sessions, registry, operations,
audit history and original/structured manual-payment evidence. SQLite's backup
API exports active databases consistently; the hourly login LaunchAgent uses the
existing Python environment. Working paths and runtime state remain in place.

**Accepted, 11 October 2026:** the hourly LaunchAgent completed with exit code 0;
25 allowlisted files were checked locally, all three SQLite integrity checks passed,
and Apple's per-item metadata confirmed in-iCloud/unshared/uploaded state without
upload errors or conflicts. Latest copy: 11:39:19 pm MYT; verified: 11:40:28 pm MYT.
[Live acceptance and locked-Mac limitation](operations.md#local-icloud-sync)
distinguish cloud confirmation from local placement and future-run health.

The owner explicitly removed snapshots/versioning, retention, restore procedures
or drills, monitoring/alerts, encryption/credential recovery work and other backup
providers from this milestone. Previous local copy experiments are historical
only. Further backup engineering must not block v0.19.

## D10 — Status notifications can be stranded without chat (medium)

**Resolved in code, 11 October 2026:** status enqueue now updates an empty chat
when owner/private-chat discovery supplies it, like decision enqueue. Synthetic
late-discovery and concurrent outbox tests pass. At-least-once send ambiguity remains.

Evidence: [outbox](../src/wally/telegram/outbox.py).

**Acceptance:** execution-status notifications created before first private-chat
discovery become deliverable after owner authentication, exactly once per dedupe
key under known delivery. Test restart, ownership and late-chat discovery.

## D11 — Telegram lease/delivery reliability limits (medium)

**11 October implementation:** the poller renews during slow handling/delivery.
Expired/lost holders cannot renew; scoped Notion dispatch checks ownership after
credential acquisition. Synthetic loss/takeover tests cover these boundaries.
**Remaining limitation:** leases are per shared database, not cross-host/cross-state
bot fencing. A crash after Telegram accepts sendMessage can show duplicate cards;
no exactly-once delivery guarantee. Actual slow-network/process acceptance remains
part of the Telegram rollout.

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
in-memory tokens without expiry/revocation. ADR-045 now makes hosted decisions
hard-disabled; the old operator confirmation flag cannot enable them.
**Documented:** hosted connectivity/confirmation remains unvalidated. Current
execute/verify absence is deliberate, not a defect.

**Acceptance:** before enabling hosted decisions, demonstrate compatible authenticated
connection, independently trustworthy identity/confirmation constraints, lifecycle
and token handling, and rejection of forged metadata/stale versions. Record the
deployment rationale. Keep decisions disabled and execute/verify unavailable until
separately reviewed; do not tunnel the local owner-secret grant as a workaround.

**10 October implementation evidence:** the shared exact-review contract, scoped
Notion mutation/verification and local Codex stdio/native-biometric provider are
implemented default-disabled and tested synthetically. No hosted per-action proof
verifier was delivered; D15 stays open. Local enrollment/UI/installation, external
writer control and uncertain-attempt recovery are separate rollout gates in the
[approval guide](interface-neutral-approvals.md).

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
current n8n exports, recovery arrangements and any agreed Notion schema; see
[operations](operations.md). The owner's receipt-matching decision is recorded
under D04 above.


## D18 — External Notion continuous discovery and conditional writes

**Status:** Read-time/pre-action foundation implemented under
[ADR-047](decisions.md#adr-047-human-first-presentation-and-external-notion-reconciliation).
[Reconciliation](notion-reconciliation.md) replaces exclusive-writer policy; direct
owner/integration writes continue. Scoped notices come from persisted findings and
complete financial source refresh retains existing certification/dependency gates.
There is no continuous Notion monitor. Intervening edits not observed by a read are
unknown, and GET/PATCH cannot fence an external same-property race. Production
financial writes stay disabled. Future monitoring belongs to the scheduler roadmap;
conditional writes require demonstrated provider support. Acceptance must retain
exact scope, independent reads, stale-trust handling and uncertain-write no-retry
rules without implying that polling stored proposals watches Notion.


## D16 — Supervised payments: live route and authentication acceptance

18 October: [ADR-051](decisions.md#adr-051-exact-telegram-supervised-payments) changes
the owner approval policy to one exact Telegram payment approval. The sandbox
workflow covers automated login/TOTP, shared duplicate claims, guarded submission,
independent settlement and Notion readback/recovery. This does not close D03 live
readiness or prove a bank permits automation. No real category-A route is validated.
The owner selected Casaman Management Q4 2026 via Maybank transfer on 18 October,
superseding the TNB candidate. Maybank is the default transfer bank until changed.
Its current FAQ includes favourite transfers among Secure2u transactions; do not
presume an exemption. Owner-approved 1Password access works; the `whoami` health
false negative was fixed with a metadata probe. An auth-only browser check filled
the username but did not advance to a password step. Bank login remains unverified;
no password or payment was submitted. Local CLI approval can expire. AmBank
requires a bank-app challenge; 1Password TOTP cannot replace it. Current retained
state has no issued instances, enabled chains or credential references.

Remaining: actual funding reference, provider-specific read/auth/submit/history
adapter and independent evidence, portal acceptance/enablement, existing Notion
schema pinning and live private-owner acceptance. Automatic certified issued-bill
intake and assisted challenge continuation are not delivered. Notion concurrent
writers retain a create/read race; uncertain writes and payments cannot auto-retry.
Preserve existing certification; this is not a request for renewed owner attestations.


Authentication follow-up: [the existing Playwright/Chrome foundation](authentication-foundation.md)
now has observed login-step states, isolated Wally contexts and one scoped
1Password pair read with a 90-second budget. One local approval took 25.1 seconds;
offline real-Chrome two-stage login passed. Fresh local access and live Maybank
login remain acceptance blockers; bank-specific work should stay bounded. HLB's
possible existing-favourite exemption remains unconfirmed. No payment or bank
setting change was made and no live route was enabled.

## HLB continuation, 23 October 2026

**23 October final owner decision:** HLB is the sole transfer bank; Maybank
development and authorization-exemption investigation are stopped. Bank-app
authorization is required and the owner completes it; every payment still
needs its exact Telegram approval. The [local security-picture gate](hlb-security-picture.md)
is implemented with synthetic acceptance; owner enrollment and live acceptance remain.
This supersedes earlier bank-selection and investigation notes.

### D16 update, 24 October: review initiates bank authorization

Live HLB review initiated AppAuthorise during reconnaissance; owner bank-app
approval completed a real transfer before any Telegram card and without a final
Submit click. Independent bank history/detail and the original receipt verified
the actual outcome. Recovery retained the duplicate hold and authorization incident,
sent the matched receipt once with independently verified Gmail evidence, and
recorded actual payment/receipt/confirmation in the existing Notion history.
This is not supervised acceptance. See the
[ADR-053 correction](decisions.md#adr-053-live-boundary-correction-24-october-2026).

Disabled submission now blocks entry/review before bank actions; review dispatch
is claimed before its click, including lost/mismatched responses. Remaining:
production composition that treats review as consequential, mandatory challenge
handling at that boundary, complete inventory and exact Telegram-to-bank acceptance.
No live factory or transfer profile is installed. Do not open review to map it in
read-only reconnaissance. Never release the paid occurrence's duplicate hold or
manufacture a retrospective approval.
