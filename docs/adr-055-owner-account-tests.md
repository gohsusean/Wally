# ADR-055: Exact owner-account test transfers

**Status:** Owner directed, 24 October 2026; live acceptance pending.

The owner authorized reusable preparation of real HLB acceptance transfers to the
existing **Sean UOB** favourite, **UOB account 2203062747**, exactly **MYR 1.00**,
with the original bank receipt emailed to **gohsusean@gmail.com**. This is permission
to prepare and propose tests, never blanket permission to transfer money.

Use the existing supervised-payment proposals, exact private Telegram callback,
separate PrincipalAuthority decision/execution confirmations, narrow worker,
durable occurrence/prepared-transaction claims and independent bank verification.
No channel receives new execute/verify capabilities. Every distinct test needs
fresh Telegram approval before bank login/transfer entry; AppAuthorise remains a
separate mandatory owner action. Replay and uncertain outcomes block further
submission; an uncertain test also blocks creating another test to evade its hold.

The trusted test composition alone omits invoice, billing-account, billing-period,
due-date, Typical Amount and genuine bill reconciliation requirements. Its source
reconstructs only the exact owner-account allowlist; ordinary bill services cannot
load its specifications, and retain all existing validation and disabled live gates.
The fixed policy lives in `config/test-transfer.yaml`; changing it is refused
pending explicit owner review. No beneficiaries are created or edited.

The live test provider validates the selected favourite, bank/account, current
one-time date, HLB source suffix, amount and references before Next/Review. A
per-command request guard checks the official URL, current runtime authorization,
exact encoded form values and command; unknown requests latch the route closed.
Review is claimed before its only click. It may initiate payment/AppAuthorise;
there is no later Submit click and no automatic retry.

The RM1 DuitNow fee assumption uses HLB's published
[Connect fee table](https://www.hlb.com.my/en/connect/faq-en.html), which lists
zero online transfer fees. Any displayed nonzero or unreadable fee still stops
the test before its initiating action.

Only an independently navigated bank history/detail match can establish settlement.
The exact original bank PDF is retained and sent through the existing guarded
Gmail receipt claim/readback. Email failure never repeats a transfer. Tests are
identified in existing `payment_specs`, `payment_attempts`, execution and event
records, with receipt files under `data/supervised-payment`; the local history
adapter writes no Notion bills or property obligations. No new database or test
management framework is introduced.

`/pay test` requests a distinct test review. It does not approve it. Production
registers this narrow route alongside the disabled ordinary bill composition.
Startup never creates or executes a test. Synthetic unit tests check the boundary;
they do not establish real HLB acceptance.

## Live evidence, 24 October 2026

Real Telegram owner callbacks were recorded before worker authorization and HLB
login. The running LaunchAgent retrieved the HLB login through 1Password after
the owner authorized its Python CLI prompt, and verified the authenticated bank
dashboard. No earlier test reached Next/Review or dispatched an initiating request;
each stopped attempt remains durably recorded as `not_submitted`.

Execution exposed an unstable implementation fingerprint: Python marshal output
changed while the actual submit method was active. Stable loaded-code semantic
material now binds the route; a regression exercises the real submit frame and
its separate callback greenlet without contacting the bank.

The bank dashboard confirms **PAY & TRANSACT → DuitNow to Account/ IBG** uses
the shared transfer endpoint with a menu parameter. Navigating to its bare URL
selected the internal HLB-account variant. The worker now clicks PAY & TRANSACT
and waits for the exact visible DuitNow/IBG link, pinning its complete destination
before navigation. It refuses the bare endpoint and ambiguous menu destinations.

As of 09:45 UTC, the corrected-menu attempt awaits a new Telegram approval.
No RM1 transfer, original test receipt or test confirmation email has yet been
verified. End-to-end bank acceptance remains pending that approved execution.
The original Tropicana claims and confirmation evidence remain unchanged.

At 22:19 MYT, another fresh owner Telegram callback preceded successful HLB
login. The worker stopped before transfer entry because hovering did not expose
the submenu. An isolated read-only session then verified that clicking the
PAY & TRANSACT toggle exposes DuitNow to Account/ IBG (`o=1`); it opened no
transfer form. Commit `89dc812` deploys that verified click and visibility wait.
As of 22:28 MYT, its fresh Telegram review remains unapproved; no bank session
has started for that attempt. Ordinary bill submission remains disabled.

At 22:42 and 22:50 MYT, separate fresh approvals reached the correct interbank
form. Both stopped before beneficiary selection/Next when the request guard
blocked HLB's automatic `mobileDeviceFlagUpdateBtn2` JSF request. Captured command
metadata shows that it executes only that setup control, renders `idFormEntry`,
and carries no amount or beneficiary fields. Credential and ViewState values were
not retained in the diagnostic records.

The focused adapter fix forwards that bank-generated setup request unchanged,
once during authorized entry, with the exact observed metadata and field set.
It does not construct or alter a device flag, approve AppAuthorise, or allow a
payment command through the setup rule. A bank form-update response must complete
before beneficiary selection. Offline tests reject added payment fields, a
different action, execution target, URL, expiry and replay. These tests do not
establish complete live acceptance. No further test cards are being issued as
part of the incremental debugging cycle.

### Nonfinancial adapter completion, 24–25 October 2026

The owner directed no further incremental Telegram approval requests. The separate
[nonfinancial inspector](adr-056-nonfinancial-hlb-inspection.md) now validates login
and empty-form setup without a payment scope. Actual HLB setup uses a native
`execution` URL and includes blank payment fields plus selector defaults; these
are checked against the current form. Mojarra also serializes named input buttons
that browser FormData omits. Tokens and credential values are never retained.

History recovery now retains its source-account check on every fresh read, selects
by bank reference rather than a shifting row index, checks the exact transfer date,
and restores previously checked recipient identity for read-only recovery after a
restart. The production test route records a verified test in existing Notion
Payment History with empty Bill and Provider Account relations. It attaches the
original receipt and records the independently verified email status through the
existing native receipt adapter; it never invents a bill obligation.

Live checks verified HLB login and setup, Wally's operational Notion target/schema,
and Wally's Gmail account matching the configured receipt address. HLB rejected
the saved-favourite lookup with a scheduled-maintenance notice asking to return
after 12:10am. No Next/Review, test payment, receipt email, Notion write or additional
Telegram card occurred. Destination confirmation and actual acceptance remain open.

At 00:10 MYT on 25 October, a fresh guarded session still received the same HLB
maintenance page after the exact native favourite lookup. Separately,
[UOB's official downtime notice](https://www.uob.com.my/default/service-interruption-notice.page)
lists DuitNow unavailable from 22:00 on 24 October until 08:00 on 25 October.
This is consistent with the failed UOB destination lookup; it does not prove the
cause of HLB's generic error redirect. No alternate destination or bank was used.

An independent HLB history navigation succeeded without a blocked request and
confirmed the existing eight-cell reference-row mapping. The latest isolated
repository run passed 1,305 tests, excluding the three documented real `.env` and
Keychain mutations. The subsequent 67 receipt/Notion checks also passed after the
test record was changed to use a unique title and leave Billing Period empty.
These are development/read-only results; no real RM1 workflow is certified complete.

The historical detail read also matched the selected bank reference and exposed
the expected recipient/amount/date/reference labels and original-receipt control,
without downloading a receipt or changing records. All diagnostic contexts closed.

Automatic approval review initially rejected the poller reload as an unrequested
live-service mutation under AGENTS.md. That rejected action did not execute, and
no workaround was attempted. The owner then explicitly approved one reload.

At 00:22 MYT on 25 October, launchd reported the reloaded process as PID 24338.
Its new startup diagnostic matched the checked-out HLB test adapter fingerprint
and reported `OwnerAccountNotionHistory`. Ordinary payment submission stayed off.
The previous lease expired naturally and a new current holder was observed;
no lease was cleared. All payment claims matched the pre-reload hash, the ten
existing attempts remained unchanged, and there were no active payment cards.
No new card, transfer, bank session, email or Notion write was initiated by reload.

Successful live destination validation, explicit destination confirmation and the
single transaction-specific Telegram card remain outstanding. Runtime deployment
does not establish live RM1 acceptance.

At 02:09 MYT, the owner-directed continuation recovered this checkpoint before
the UOB window ended. A one-time task continuation was scheduled for 08:05 MYT
on 25 October. No bank lookup was attempted early. Explicit confirmation of
Sean UOB ending 2747, HLB funding ending 4394 and the configured receipt address
was requested; it remains a prerequisite to the single new payment proposal.
Previously passing automated tests and Telegram approval validation were not
repeated. The pending diagnostic, independent-history recovery and Notion test
receipt changes are being committed separately from unrelated workspace changes.
Scheduling a continuation is not evidence of a bank lookup or transaction.

At 08:05 MYT on 25 October, the scheduled continuation verified the actual clock
and invoked the guarded nonfinancial diagnostic. Credential retrieval stopped with
`ProviderUnavailableError` before the browser was opened. The existing 1Password
desktop app was opened and one recovery invocation also stopped at credential
retrieval; a separate permitted executable-name inspection confirmed the desktop
process was running. An earlier sandboxed process check could not establish app
absence. No secret values were exposed and no additional credential probe was run.

Neither invocation reached HLB login or dispatched a saved-favourite lookup, so
post-maintenance bank availability and destination validation remain unknown.
The local payment ledger still contained ten `not_submitted` attempts and no
stored settlements or active payment cards. No new proposal/card, transfer,
receipt email, Notion write, service reload or automated-test rerun occurred.
Local 1Password unlock/CLI authorization and explicit destination confirmation
were requested from the owner. No further bank attempt was scheduled. Ordinary
bill payment submission remains disabled; no live RM1 acceptance is claimed.

### Saved-favourite name mapping, 25 October 2026

The owner explicitly confirmed UOB account 2203062747, the RM1 amount and
gohsusean@gmail.com, and unlocked 1Password. Subsequent nonfinancial inspection
verified HLB login, empty-form setup and a returned bank/account matching the
configured UOB destination. The selected saved-payee form exposes `Favourite
Account Name`, not `Recipient Name`. Treating the missing latter field as a wrong
destination was an adapter error; the favourite alias value itself had not yet
passed the corrected check.

The corrected entry parser reads the observed favourite label and validates it
against the fixed owner-approved alias, alongside the unchanged exact bank/account
checks. It retains the alias separately from any actual bank recipient name. This
is not legal-name certification and grants no additional payment authority.
Independent history still requires the exact funding account, destination bank and
account, amount, date, tracking and recipient references, bank reference, outcome
and a nonempty bank recipient name. Any bank name observed before submission must
also match. The independently read detail hash binds the later original receipt;
an alias is never substituted for the bank's recipient name. Read-only recovery
restores the two evidence fields separately and rejects conflicting aliases/names.

The nonfinancial inspector now waits for the complete native favourite-response
before reading the updated form. Five focused response-completion cases and 16
affected recipient/history/recovery cases passed; no Telegram approval tests or
broad suites were repeated. These checks validate code, not a bank transaction.

After reviewing this specific fix, the owner authorized one read-only verification.
That session verified login but timed out during navigation to the empty transfer
form, before reaching the corrected favourite check. Bank attempts were paused
and the owner was asked before further inspection. No new card, initiating action,
receipt email, Notion write or service reload occurred. The fix remains pending
live verification. Ordinary bill payment submission stays disabled.

The owner's standing instruction is recorded in the
[internal-blocker project rule](../.cursor/rules/pause-on-internal-blockers.mdc):
pause and check before another live attempt when an internal expectation,
self-imposed constraint or uncertain bank action blocks progress.
