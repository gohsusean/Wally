# Interface-neutral approval, centralized authorization

**11 October extension:** [Telegram approvals](telegram-notion-approvals.md) under
ADR-046 are now the default for eligible metadata edits. One exact owner callback
supplies separate decision/execution confirmations to the existing service. Telegram
itself has no execute/verify grant. The local native path below is retained for
future use with `--approval-channel local`; it is not required for ordinary eligible
edits. The earlier rollout record is historical evidence, not the active workflow.

Updated 10 October 2026. [ADR-045](decisions.md#adr-045-interface-neutral-approval-centralized-authorization)
extends the operational architecture. This is an implemented, default-disabled
Notion metadata-edit slice, with isolated tests and an opt-in local Codex adapter.
The [local rollout record](local-codex-rollout.md) records the later isolated MCP
installation and native helper build. Neither is evidence of a successful biometric
interaction, a hosted ChatGPT connection, or safe live financial readiness.

## What is implemented

The existing `Principal`, `RequestContext`, `PrincipalAuthority`, proposal rows,
Matter lifecycle, rejection/deferral semantics and `executions` table remain the
foundation. `NotionEditService` adds the `edit_notion_record` intent. Its
`ProposedAction` holds identifiers and advice, never executable provider arguments.
A separate immutable specification records exact before/after enum values,
normalized Notion page/data-source identifiers, the reviewed target policy,
protected-property hashes, schema fingerprint and canonical finance version.
The specification's digest binds the proposal fingerprint; it grants no authority.

The initial code-owned write scope supports existing **select** options for
`amount_policy` and `frequency`, including `source_defined` and `statement_driven`.
It supports neither free text nor monetary amounts, payment/paid status, relations,
credentials, governance classification, arbitrary properties or new select options.
Additional reconciliation/classification/metadata operations remain future work.
Registered targets must belong to owner-approved operational knowledge. Financial
sources additionally require existing designation, canonical identity and stable
property mappings. Pending/governance knowledge cannot be edited.

The separation is enforced as follows:

1. **Propose:** authenticated submit capability stages inert advice and an immutable
   edit specification. The caller supplies only a registered target key and enum
   replacements. Wally reads the current source and constructs the version.
2. **Review:** authenticated read returns the proposal ID, fingerprint, target,
   expiry and exact old/new property values.
3. **Decide:** explicit selections contain `(proposal_id, fingerprint, decision)`;
   defer also supplies a future date. Wally constructs an immutable human review
   containing every selection and its old/new values. A trusted runtime confirmation
   provider must authenticate the owner and confirm that exact review. Batch changes
   are committed together after revalidation; an unselected proposal stays pending.
4. **Authorize execution:** a separate request requires a current stored approval,
   the scoped execute and verify capabilities, enabled writes, and a **new** human
   review of the exact execution. Approval itself executes nothing.
5. **Execute:** Wally checks current Matter, expiry, decision, specification, source
   state and policy after confirmation. It atomically claims both the proposal and
   normalized page ID in SQLite. Certification is invalidated before a potentially
   material financial write. Action credentials are acquired in the write client
   factory; source state is rechecked again after credential acquisition. PATCH
   contains only the approved property IDs and replacement values.
6. **Verify:** a separate Notion GET reads the record and schema. Approved values
   must match, and all protected business-property hashes and schema must match the
   expected result. Only then is the attempt `verified_success` and its page claim
   released. Verification does not certify financial data or resolve an obligation.

`PrincipalAuthority.confirm_human` calls only a provider installed by trusted
runtime composition. It creates the review nonce and five-minute deadline itself.
Its confirmation record is consumed once for that exact purpose, context and
presentation; copied/foreign/replayed records cannot authorize another operation.
Persisted confirmation fields are attribution, not reusable credentials. There is
no tool argument, bearer receipt, boolean assertion or API for supplying approval
proof. No interface can register its own verifier via a request.

The scoped capabilities are `EXECUTE_NOTION_EDIT` and `VERIFY_NOTION_EDIT`. The
local Codex policy does **not** grant broad `EXECUTE_PROPOSAL`, `VERIFY_EXECUTION`
or `CERTIFY_FINANCIAL_DATA`. No browser, payment workflow, n8n action or legacy
ToolRegistry is attached to its runtime. Generic approval/execution/verification
entry points refuse this new intent so that they cannot bypass the exact review.
Existing Terminal and Telegram behavior for their established intents is retained.

## ChatGPT: current limitation

`propose_notion_edit` and `get_notion_edit` are implemented translations through
Gateway, usable when a trusted composition supplies the edit service and registered
targets. The shipped ChatGPT HTTP composition does **not** attach that service:
these calls report unavailable until an operator supplies reviewed read/staging
configuration. The existing connection still needs its local bearer grant.

All hosted `record_decision` calls remain disabled in code. The old
`WALLY_CHATGPT_DECISION_CONFIRMATION` setting is accepted for configuration
compatibility but cannot enable decisions. Subject/org metadata and the local
bearer authenticate neither the human's exact decision nor its scope. ChatGPT
gets no scoped edit execute/verify capability. Calling the Gateway directly with
claimed owner identity or asserted approval cannot bypass these boundaries.

The official OpenAI [MCP authentication guide](https://developers.openai.com/plugins/build/auth)
describes resource-server token validation and using OAuth to authenticate users;
mTLS authenticates the ChatGPT client. The [MCP server guide](https://developers.openai.com/plugins/build/mcp-server)
says annotations do not replace server authorization/confirmation and describes
elicitation for structured information. [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
describes local/remote server integration. **Assessment:** the inspected Wally
integrations and these documented mechanisms establish no Wally-verifiable,
action-specific human attestation that binds an owner, exact proposal version and
scope. This is a finding about the inspected integration, not a claim that every
future platform mechanism is incapable of providing it.

A hosted confirmation integration remains unimplemented. OAuth identity plus an
independent Wally-owned authenticator could be a future design, but no public
approval endpoint, tunnel, hosted login, passkey enrollment or signed platform
receipt verifier was added here.

## Codex: implemented local confirmation path

`python -m wally.codex` is a new explicit local stdio MCP entry point. It builds a
restricted Gateway and provides seven tools: runtime status, read-only interrupted
attempt inspection, propose, exact review, scoped batch decision, separate
execution, and independent re-verification. It does not run
normal App bootstrap, start a listener, refresh the finance catalog or invoke a
reasoner. The local process is connection authentication; its tool invocation
alone cannot authorize a decision or execution.

The optional `MacOSBiometricConfirmation` provider launches an operator-built,
SHA-256-pinned helper owned by the configured Mac user. The helper displays the
entire immutable selection/old/new review, including registered target and field
names, then requests fresh biometric
`deviceOwnerAuthenticationWithBiometrics`. A click alone is insufficient.
Authentication reuse is set to zero and there is no password fallback in this
policy. The helper independently hashes the canonical authorization envelope and
uses only its bound presentation; a mismatched digest never reaches the UI.
Cancellation, unsupported biometrics, timeout, helper substitution, wrong
UID, wrong nonce/digest or a changed policy fails closed. The confirmation records
include method, configured UID and helper digest. Apple documents the
[biometric policy](https://developer.apple.com/documentation/localauthentication/lapolicy/deviceownerauthenticationwithbiometrics)
and [reuse setting](https://developer.apple.com/documentation/localauthentication/lacontext/touchidauthenticationallowablereuseduration).

This allows a local Codex tool request to present a native review while the owner
stays in Codex. The human must still review that popup and authenticate; typing
“Approve both” merely selects which proposals the adapter asks to confirm. Each
execute request prompts separately. No Codex conversation-reading or generic
platform approval dialog supplies Wally authority.

**Verified here:** Swift source compiles against the installed macOS SDK and the
private app is ad-hoc signed; actual binary probes deny expired/mismatched reviews
before UI. Python tests simulate helper results, check integrity/UID/digest failure paths,
and exercise the stdio/Gateway/Notion sequence with synthetic records. **Not
verified here:** actual biometric UX, ownership/enrollment of this machine's
biometrics, installed helper integrity over time, Codex Desktop MCP connection,
or a live Notion write. The helper was installed and queried for availability,
but was not launched for authentication. Current machine readiness is in the rollout record.
This is a local owner/process trust model, as with the existing terminal: it is
not a sandbox against arbitrary code running as the owner or a compromised Mac.

`TerminalHumanConfirmation` is available as an optional exact-review administrative
provider for trusted CLI/REPL composition. It uses the existing local-owner prompt
assumption and refuses piped input/nonterminal channels. It is not registered in
the new stdio runtime; current general Terminal commands retain their behavior.
Telegram retains its existing authenticated nonce decisions and restricted
capabilities. This patch does not wire its ordinary Approve callback to the new
exact-edit service and grants it no new execute, verify or certification authority.

## Verification, audit and failure handling

Protected comparisons use semantic property values rather than whole page JSON.
Notion edit timestamps/actors are nonbusiness metadata. An explicitly registered
rich-text property named `Audit History` may be ignored; it cannot also be an
editable or mapped financial business property. Other properties are protected.
Unknown protected types and potentially truncated list/relation values fail closed
instead of guessing completeness. Financial certificates are conservatively
invalidated before a write, including an uncertain attempt. Existing dependency
checks make dependent authority unavailable; a successful edit never restores or
issues a certificate. Catalog refresh/recertification is a separate owner operation.

Attempts reuse the operational execution table. The additive `notion_edit_specs`,
`notion_edit_claims`, `human_review_events`, and `human_confirmations` tables retain
specifications, page claims, presented scope, authenticated method/owner, consumed
confirmation identity, execution and independent verification. Events never contain
provider exceptions or secrets; protected business data is hashed. Model identity
claims are not copied into authenticated audit provenance.

A write exception, mismatched read, unavailable verification or process interruption
is never reported as success. No automatic retry occurs. A durable page claim
blocks even a different proposal targeting the same record across process restarts.
An explicit re-verification of `executed_unverified` only reads; `running` may still
be in flight and needs manual reconciliation. `inspect_notion_execution` can
independently observe original, approved or unexpected state even for RUNNING;
it never writes, settles the attempt, proves who changed Notion, or releases a
claim. An unavailable read is explicit. `patch_not_dispatched` distinguishes a
known pre-dispatch failure from `patch_outcome_uncertain`; conservative certification
invalidation and the write lock remain in either case. This slice intentionally has no
automatic/manual-unlock API for a still-running or unresolved claim. Never delete
its row just to permit another attempt. Successful versions are not repeated.

Supersession keeps old history closed. Rejection sticks for the same material
review. Defer release clears the decision. An expired/invalidated review can receive
a new immutable review revision with fresh identity and no inherited approval.
Partial batches authorize only selected versions; failure of one selected
precondition commits no batch decisions.

Notion's documented [page update API](https://developers.notion.com/reference/patch-page)
is a property PATCH; the inspected contract offers no documented conditional
compare-and-swap parameter. **Limitation:** semantic reads immediately before PATCH cannot eliminate a race
with another external writer. Wally's page claim fences Wally attempts, not Notion
users/integrations. [ADR-047](decisions.md#adr-047-human-first-presentation-and-external-notion-reconciliation)
supersedes exclusive-writer policy with [read-time reconciliation](notion-reconciliation.md).
The owner can continue direct Notion writes; Wally detects observed drift and stales
trust without automatically adopting, overwriting or certifying it. A same-property
GET/PATCH race remains and needs separate production rollout acceptance. No unrelated
integration permissions were changed by this development task.

## Rollout gate and remaining steps

The shipped [policy](../config/notion-edits.yaml) has `writes_enabled: false`,
`macos_confirmation: null` and no targets. Normal App, ChatGPT and Telegram startup
never enable these writes. No production record, certification, invoice, payment
chain, service, credential or existing runtime classification was changed by tests.
The four Phase 6 certifications and privately staged September TNB bill remain
outside this task; they are not registered as edit targets here.

Before live use, an operator must:

1. Review/build/pin the helper, bind the Mac UID and enrolled biometrics to the Wally
   owner, and demonstrate confirmation/cancellation on isolated records. The
   explicit `scripts/install_codex_approval_helper.py` builds/signs a private app
   only into a new destination and emits its SHA-256 pin; it never authenticates.
   Record that pin in the reviewed local policy. No secret is required for this step.
2. Configure explicit target keys, UUID locators, allowed property IDs/options and,
   for finance, the canonical finance object ID. Existing approved classification,
   designation and source mapping gates still apply. Keep writes disabled initially.
3. Connect Codex to the local stdio command using its supported MCP configuration:
   the existing repository interpreter, `-m wally.codex --project-root <repo>`.
   Secrets stay in the runtime environment/ignored `.env`, never MCP arguments.
   Use `--state-dir` and `--edit-policy` for the isolated rollout; neither can be
   changed through tool arguments. The default production policy remains disabled.
   The local rollout record names the installed Codex server configuration.
4. Validate the actual UI/biometric sequence and independent verification in isolation,
   accept external reconciliation/race limitations, and define uncertain-attempt
   recovery before enabling writes. Then explicitly enable the reviewed write flag;
   target/flag policy is re-read after human confirmation. Dry-run always denies.
5. For hosted ChatGPT, separately implement and demonstrate authenticated owner
   identity and action-specific confirmation. This deployment gap remains D15.

Tests are in [scoped edits](../tests/test_notion_edits.py),
[local MCP](../tests/test_codex_edits.py), [native/terminal confirmation](../tests/test_native_confirmation.py),
and existing principal, approval, Act & Verify, ChatGPT, Telegram and finance suites.
Synthetic certification fixtures are not certification of production financial data.
