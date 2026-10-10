# Local Codex approval rollout

Point-in-time setup evidence, 10 October 2026. This extends
[ADR-045](decisions.md#adr-045-interface-neutral-approval-centralized-authorization)
without changing the approval trust model. Production financial rollout is blocked.

## Installed and observed

- Codex configuration contains the enabled stdio server `wally_local`, using the
  existing repository interpreter and `python -m wally.codex`. Its tool timeout
  is 360 seconds, exceeding the bounded five-minute native review window. Other
  Codex server/settings entries were preserved; no secret was added to arguments.
- The command uses `--state-dir data/codex-local/state` and
  `--edit-policy data/codex-local/policy.yaml` as absolute paths. Operations,
  registry and audit state are isolated from production. Local deployment files,
  signed app, integrity pin and configuration backup are ignored/private runtime
  artifacts. No production classification or financial state was copied/reset.
- An actual stdio probe initialized the installed command, listed all seven tools
  and called `get_notion_edit_status`. Wally reported principal `owner`, channel
  `codex_local`, writes disabled, fresh confirmation required, separate execution
  confirmation required, and **zero targets**. This proves the command works;
  Desktop discovery still requires restarting the server in Codex Settings and
  observing its tools in the actual conversation. Computer use cannot operate
  this app's settings with the currently available tool.
- The helper was compiled, packaged as `Wally Approval.app`, ad-hoc signed and
  verified. Its private integrity pin identifies UID 501 and executable SHA-256
  `32fc5d10f0ef43b7bc0f97b1c7c11cc1c2473ae8fd8a6557b7ada49b9779d099`.
  The native binary actually rejected expired and digest-mismatched envelopes
  before displaying UI. Neither check authenticates a human.
- An unsandboxed availability query returned `touch_id: true`,
  `biometrics_available: false`, `error_code: -12`. The installed Apple SDK defines
  this as `LAErrorBiometryNotPaired`: biometry requires a removable accessory and
  none is paired. Owner hardware setup is required. No successful biometric
  interaction, UI cancellation/rejection, or live Notion edit is claimed.
- The local policy still has no confirmer or targets and writes disabled. The
  production `config/notion-edits.yaml` is unchanged and disabled. Binding the Mac
  account/enrolled fingerprints to the Wally owner awaits explicit owner evidence.

The native result is a trusted local-process confirmation, not a signed remote
biometric attestation. UID identifies the Mac account; it does not identify which
enrolled person's fingerprint was used. The owner must establish that enrollment
binding. As documented in ADR-045, arbitrary code running as the same owner can
modify owned runtime code/policy or use accessible provider credentials; this
installation does not sandbox an adversarial Codex process. Protecting against
that threat requires isolating trusted runtime code, policy and credentials from
the agent's write/execution authority, with a separately reviewed deployment.
Keep production writes disabled until the accepted local trust model and effective
provider access controls are established. No security isolation is claimed here.

## Minimum owner steps, entirely outside Terminal

1. Confirm that macOS account `seangoh` (UID 501) and its enrolled fingerprints
   belong only to people authorized to approve Wally actions. Pair the compatible
   Touch ID accessory using macOS setup, then report readiness in Codex.
2. In Codex Settings, open MCP servers and restart `wally_local`. Confirm tool
   discovery with `get_notion_edit_status` in this conversation. No app restart,
   Terminal command or production service restart is required by this runbook.
3. Approve creation of one `Wally Approval Sandbox` Notion database and supply its
   nonfinancial parent page. The proposed schema is Name (title), Amount Policy
   (select: fixed_contract/source_defined), Frequency (select:
   monthly/statement_driven), and Notes (protected rich text). Sample A and Sample B
   contain synthetic values only. No create call has been authorized/performed yet.
4. After creation is approved and completed, register only those two page IDs and
   exact select-property IDs in the isolated policy/registry. Validate reads and
   native cancellation first, then confirm the precise isolated write gate. Never
   register financial pages for this acceptance test.
5. Ask Codex to propose Sample A Amount Policy `fixed_contract → source_defined`
   and Sample B Frequency `monthly → statement_driven`. Codex must show the exact
   Wally review IDs/fingerprints and old/new values. Approve A and reject B in one
   scoped batch; inspect the independent native scope, then authenticate with
   Touch ID. The decision performs no write. Separately request execution of A,
   inspect its native review and authenticate again. Accept success only from
   independent `verified_success`; read B to confirm it remains unchanged.
6. Demonstrate cancellation, fresh challenges, refusal of an old proposal version,
   and replay rejection. An expired review and a substituted digest already fail
   in the real binary; receipt replay and scope changes are automated fixture tests.
   Actual biometric/UI behavior still needs this owner session. Close the isolated
   write gate after acceptance and retain all audit/attempt history.

## Recovery and certification

`inspect_notion_execution` is read-only provider reconciliation: it reports
`approved_state_observed`, `original_state_observed`, `unexpected_state`, or
`read_unavailable`. It never repeats PATCH, settles RUNNING, restores certification,
releases a page claim, or proves which actor produced the observed state.
An original state observation is not permission to retry: a delayed request or
still-running process may remain in flight.

For `executed_unverified`, `verify_notion_edit` can independently verify the full
approved result without repeating PATCH. For RUNNING, establish that every writer
has stopped and reconcile the attempt with the owner; no automatic unlock API is
provided. Keep the durable claim until a separately reviewed recovery decision.
Synthetic tests cover timeout after PATCH was applied, restart/duplicate attempts,
and original/approved/unexpected/unavailable read results.

`patch_not_dispatched` records a known failure before entering the provider PATCH;
`patch_outcome_uncertain` records a failure after dispatch might have occurred.
Both retain locks and conservatively invalidated financial authority if invalidation
already occurred. Denial before execution leaves certification unchanged. Tests
cover denial, pre-dispatch failure, uncertain write and independently verified
success; none restores or creates a certificate. Production certificates were
not read or modified for these tests.

## External writer controls before financial rollout

Recommended enforceable arrangement: dedicate a database/data source to canonical
Wally-managed records. Give ordinary users and their general-purpose connectors
view/comment access there; grant write access only to Wally's dedicated integration.
Audit inherited teamspace/group/page permissions and remove broader grants on that
managed subset. Use a separate editable intake area for proposed corrections, and
leave unrelated operational databases and integrations unchanged. Verify effective
permissions using each relevant non-owner identity/integration before rollout.
If a general connector inherits the owner's full-access identity, its own tool
approval setting cannot enforce this arrangement: use a separate account with
view/comment access to the managed subset (and ordinary access elsewhere), or
remove that connector's access to the managed subset. Keep a separate explicit
administrative access path. Do not claim this works until denied direct-write
probes have demonstrated the actual identity/permission boundaries.

Notion's [permission documentation](https://www.notion.com/help/sharing-and-permissions)
explains that the broadest access wins. Its [database documentation](https://www.notion.com/help/intro-to-databases)
confirms that `Can edit content` permits property-value edits and database locking
does not prevent record edits. A lock/view/filter is not an authorization boundary.
No workspace permission or integration credential was changed in this task.
An owner/admin can restore access; that remains an explicit administrative trust
limit. A general connector acting as that owner can bypass Wally until its access
to the managed subset is removed/rerouted. This arrangement is a recommendation,
not a proven permission deployment. Semantic checks still cannot fence an external
writer racing PATCH; financial writes stay off until controlled access is demonstrated.

## Practical hosted ChatGPT follow-up

The most promising practical design is an OAuth-authenticated Wally MCP service
plus a Wally-controlled passkey confirmation page that presents the exact immutable
review and binds a fresh server challenge to owner, proposal IDs/fingerprints,
scope, purpose and expiry. Decision and execution require separate challenges.
The final callback must be verified by Wally, consumed once and revalidated before
acting; a model tool call, OAuth session or elicitation answer is insufficient.

Official OpenAI documentation supports [OAuth authentication](https://developers.openai.com/plugins/build/auth)
and [MCP elicitation](https://developers.openai.com/plugins/build/mcp-server), and
states that annotations do not replace server authorization/confirmation. These
are transport/user-interaction features, not a documented action-specific human
attestation. Wally already implements immutable reviews, consumed confirmation
records, decisions, scoped execution and independent reads. It does not implement
the passkey enrollment/challenge verifier, hosted approval UX, HTTPS deployment,
OAuth lifecycle or remote confirmation provider. Demonstrating a suitable
platform-supported link/elicitation UX and trustworthy owner binding remains
necessary. Hosted decisions and execution stay disabled; no public endpoint or
insecure confirmation workaround was added.

## Automated validation

The operationalization workspace gate passed 693 tests, excluding only the three
repository-documented `.env`/Keychain mutation tests. The focused edit, native
boundary and local MCP suites passed 63 tests. Ruff, local documentation links,
anchors/fences and whitespace checks passed. Synthetic fixtures establish code
behavior, not live Notion permissions or successful Touch ID confirmation. The
existing five finance edits were preserved and are excluded from the rollout commit.
The exact staged snapshot independently passed 682 tests with the same three
exclusions; the lower count omits the unrelated uncommitted finance tests.
