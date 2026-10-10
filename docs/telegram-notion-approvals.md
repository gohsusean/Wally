# Telegram authorization for scoped Notion metadata

Updated 11 October 2026. [ADR-046](decisions.md#adr-046-telegram-authorizes-eligible-scoped-notion-edits)
extends the interface-neutral approval foundation. Telegram is the default owner
approval interface for eligible edits. Production writes remain disabled. The
native Codex/macOS path and its tests remain dormant; biometric enrollment and
password authentication are outside this milestone.

## Owner experience

Codex calls `propose_notion_edit` with a registered target key and finite replacement
values. Wally reads Notion and stores an immutable specification with inert proposal
advice. The existing Telegram poller discovers it in the same operations database.
Proposal origin cannot approve it.

The card shows the record label (configured target key), exact page identity/link,
property names/IDs, old/new values, proposal ID, fingerprint, expiry and certification
consequences. Buttons are **Approve & Execute**, **Reject**, **Later (1 hour)**.
Individual cards support selected records. An additional **Approve & Execute all
shown** card contains the complete batch scope, with no hidden selection. Decisions
are atomic; provider executions are sequential and separately recorded. A batch is
not an atomic Notion transaction. Already settled selections invalidate its older card.

Cards use a UTF-16 length budget below Telegram's message limit. Oversized batches
use individual complete cards. An oversized individual review is withheld and
audited, never truncated into an actionable summary. No arbitrary source prose,
credentials, amounts, account details or protected-property content is sent.

One button press authorizes separate internal stages:

1. The trusted Bot API callback validates owner/private chat, known delivered message
   identity, opaque nonce, immutable scope and deadline, then durably claims the review
   once. Forwarded, inaccessible, forged, changed and stale callbacks fail closed.
2. `PrincipalAuthority` confirms and consumes a fresh exact decision presentation
   through its registered Telegram provider. Proposal decision, confirmation and
   audit are persisted independently of execution. Ordinary approval remains inert.
3. A trusted worker holds only read, `EXECUTE_NOTION_EDIT` and `VERIFY_NOTION_EDIT`.
   Telegram itself has no execute/verify capabilities. The worker has no exposed
   Gateway registration. A second exact-purpose confirmation uses the same still-fresh
   callback interaction, with a different nonce/channel/purpose and linked correlation.
4. Wally revalidates approval/version, Matter, expiry, source, registration, field/option
   policy, polling authority and write gate. Record/proposal claims precede certification
   invalidation and credential acquisition. The backend checks source/authorization
   again afterward and PATCHes only approved properties.
5. Independent schema/record GETs must match approved values and protected business
   properties. Only `verified_success` establishes completion. Verification never certifies.

Durable reviews record owner Telegram ID, exact scope/action, callback ID, timestamp,
expiry and outcome. They are attribution, not executable receipts: a restart cannot
recreate authorization. A model statement, old login, generic biometric success or
copied confirmation cannot authorize an action. The eligible interaction needs no
Terminal, Touch ID or password.

Reject writes a decision only. Later records a real defer, releases it after one hour
while still valid, and requires another press. Consumed buttons are harmless. Callback
toasts and message delivery can fail independently; durable outcomes survive.
Approved/unstarted, executing/interrupted, verified and uncertain states are distinct.
Codex reads `get_notion_edit` for attempt IDs/status, then `inspect_notion_execution`
for read-only reconciliation.

## Policy and composition

[config/notion-edits.yaml](../config/notion-edits.yaml) ships with empty targets,
`writes_enabled: false`, `telegram_writes_enabled: false`, `telegram_targets: []`
and no native confirmer. The gates are independent: a Telegram sandbox does not
enable local execution. The explicit Telegram key subset must reference registered
targets. Runtime reloads policy at confirmation and dispatch, including after action
credential acquisition. Only select properties mapped to `amount_policy` or
`frequency` and finite code-owned existing options qualify. Another field requires
explicit code/policy/test changes. Model risk assessments grant nothing. Use a
readable configured record label without unnecessary sensitive information.

Classification and financial designation/canonical mapping checks remain in the
backend. Certification, bill/invoice registration, payments, amounts, banking,
secrets, security changes, destructive operations, pending/governance knowledge and
unregistered records/properties are excluded. Potentially material dispatch invalidates
affected financial certification, even if later blocked before PATCH. Failed/uncertain
writes never restore it. Rejected/unattempted advice leaves certification unchanged.
Verification never certifies, pays or resolves an obligation.

Codex defaults to Telegram mode with five tools: status, propose, exact review,
inspect attempt and re-verify. Decision/execute tools are absent and capability checks
also deny them. `--approval-channel local` retains the seven-tool native path for
future reviewed use. Its helper/state is preserved. Installed MCP and poller must
share project/profile, operations database, registry and reviewed edit policy. The
old `data/codex-local/state` isolation cannot hand off proposals to the existing bot.

The existing LaunchAgent/long polling deployment is retained. There is no second
poller, webhook, hosting, reasoner or automation provider. Lease renewal continues
during slow processing; lost/expired holders cannot renew and scoped dispatch checks
current ownership. Replayed processed updates repair cursor progress. Status rows
created before private-chat discovery acquire the chat later. Lease protection is
local to one database: never run the same bot against another database/host concurrently.

The Mac must be awake, logged in and online. Offline proposals wait; stale reviews
cannot act. Reviews expire after 15 minutes; a stale button or owner question requests
fresh cards, never automatic execution or periodic prompt storms. Elapsed defers
also return a review. Proposals expire after 24 hours. Sends remain
at-least-once: a crash after Telegram accepts a send may show duplicates. Only the
durably recorded message ID can authorize; ambiguous/unrecorded delivery cannot.

## Uncertainty and trust limits

Timeouts, read failures, protected-property mismatch, interruption and known pre-PATCH
failure retain the page claim and execution history with an explicit category.
There is no PATCH retry. Inspection distinguishes approved/original/unexpected/
unavailable state without unlocking or settling RUNNING attempts. An
`executed_unverified` attempt can be independently re-verified without PATCH.
Original state is not retry permission. RUNNING requires operator process-liveness
reconciliation; no automatic recovery write, authorization replay or claim deletion
is introduced.

Telegram account identity is not biometric human-presence proof. Accidental presses,
account takeover and bot-token compromise matter for this bounded action class.
Protect the account with Telegram's security controls. Bots use cloud chats, not
end-to-end encrypted Secret Chats. Never include secrets or unnecessary financial
information. The [Bot API callback contract](https://core.telegram.org/bots/api#callbackquery)
provides sender/message data, not a signed proposal-specific human attestation.

Wally cannot prevent direct edits by other users/integrations with Notion write
access. Immediate semantic reads do not fence external writers between GET and PATCH;
no atomic compare-and-set is demonstrated in the inspected
[page update contract](https://developers.notion.com/reference/patch-page).
Use a dedicated managed financial database/teamspace and Wally writer; ordinary users
and other connections should have view/comment or read-only access to that subset.
Leave ordinary Notion work elsewhere unrestricted. Remove inherited write grants and
audit connections; linked views/database locks are not permission enforcement.
[Connection capabilities](https://developers.notion.com/reference/capabilities)
separate read/update/insert rights, and parent sharing grants child access. An owner
OAuth connector can still bypass Wally if its identity retains edits: use a distinct
limited identity or remove access to the managed subset. No permissions/credentials
were changed here. Arbitrary same-owner code can modify runtime/policy or access
existing credentials; no adversarial local-agent isolation is claimed. Exclusive
production control needs effective permission validation and potentially a protected
runtime identity.

## Origins and ChatGPT

Codex and trusted Wally workflows stage through existing authenticated submit contexts;
identical Telegram policy applies regardless of origin. Hosted ChatGPT restrictions
are unchanged: `record_decision` is hard-disabled; execute/verify are absent.
Proposal/review translations exist, but the shipped HTTP composition does not attach
the edit service. Hosted mutation proposal submission is not claimed operational.

The practical follow-up is authenticated, scoped hosted proposal submission to this
store, followed by Telegram review. It needs reviewed identity/token lifecycle,
connectivity and composition. Telegram supplies owner interaction; OAuth/transport
authentication alone does not prove a human approved the specific action. No
unauthenticated endpoint, tunnel or hosted authorization workaround was implemented.

## Validation and remaining acceptance

Synthetic tests exercise Codex MCP proposal creation, Telegram review/callback,
separate confirmations, one properties-only HTTP PATCH, independent GETs, benign
audit changes and verified outcome. They cover Frequency/Amount Policy, partial
batches/reject/defer, identity/scope/version/expiry failures, duplicate/concurrent/
restart callbacks, stale source, policy/lease revocation during credential acquisition,
timeouts, protected-property mismatch, interruption, read reconciliation, certification
invalidation and preserved dormant local capabilities. Fixtures do not establish live
owner interaction or provider acceptance.

The 11 October workspace gate passed **734 tests**, with the three documented
real `.env`/Keychain mutation tests excluded; lint, local links/anchors, fences and
diff checks passed. The installed `wally_local` stdio command actually initialized,
listed five Telegram-mode tools and reported principal `owner`, channel
`codex_local`, both gates false and zero targets. Its Wally-only arguments now use
shared state/default policy; other Codex settings and dormant native state/helper
were preserved. The existing LaunchAgent was restarted after confirming both gates
false, no registered targets and no unresolved scoped attempts. Process/start-marker
inspection establishes code loading, not current Bot API connectivity or a live click.
An actual read-only `getMe` call authenticated successfully to the configured
`SeanWallyBot` without consuming updates, sending messages or accessing Notion.
The exact staged snapshot, excluding unrelated finance work, passed **723 tests**
with the same three exclusions and clean lint. Successful startup/API authentication
is not owner authorization or end-to-end mutation acceptance.
On 11 October the owner explicitly authorized isolated, nonfinancial live acceptance.
This Codex conversation discovered all five installed Wally tools and called status,
which reported principal `owner`, channel `codex_local`, both gates false and zero targets.
Actual Desktop discovery is now established. No biometric interaction is claimed.

The Notion connector created a private top-level **🧪 Wally Sandbox** page,
`3f54d585-1c89-81b9-b4e1-e3448785db06`, and its **Synthetic Acceptance Records**
database, `288053ad-2773-45a6-b1ce-3e191d1c2b96` (data source
`6a171555-9014-4214-951c-6fc03fbd4c52`). Two synthetic records start with Frequency
`monthly` and Amount Policy `fixed_contract`; neither is financial or certified.
No existing parent was used. Identities are also retained in ignored local state at
`data/sandbox-acceptance/manifest.json` so continuation does not create duplicates.

The connector's access does **not** establish Wally's integration access. An actual
read-only `/users/me` authenticated as **Wally's Connection**, while GETs for the new
sandbox page and data source returned 404. Acceptance is awaiting the owner adding
that connection on this sandbox only through Notion's **⋯ → Connections** menu.
Both gates and registrations remain disabled/empty while waiting. The four Phase 6
records, staged September invoice and payment actions remain excluded.

No actual Telegram approval/rejection or live Wally PATCH has occurred. After the
connection step, acceptance still requires exact nonfinancial registration,
sandbox-only Telegram gate, shared runtime/client reload, actual owner button presses
(including rejection and replay), independent read verification, Telegram outcome
delivery and disabling sandbox writes again. Production financial rollout remains
blocked by live acceptance and external-writer controls.
