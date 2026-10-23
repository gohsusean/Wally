# HLB supervised transfers — v0.19

**24 October 2026:** live reconnaissance exposed an authorization-boundary failure:
opening HLB's transfer review initiated AppAuthorise. The owner approved in the
bank app and payment completed before any Telegram card, without a final Submit
click. Independent history/detail and the original receipt verified settlement.
This is an incident, **not successful supervised-payment acceptance**. No live
payment factory is installed or enabled. The offline flow now reproduces review
initiating AppAuthorise; it does not establish live selector/request contracts.

Do not open transfer review during reconnaissance. `submission_enabled=False`
blocks `enter_review` and `submit` before any bank form action. Entry navigation
also claims the adapter's transaction before dispatch, preventing another review
after a lost response or mismatched result. A later Back click does not establish
cancellation. Uncertain outcomes require independent history reads, never another
authorization request. See [the incident decision](decisions.md#adr-053-live-boundary-correction-24-october-2026).

The implementation is [the HLB adapter](../src/wally/adapters/browser/hlb_transfer.py),
[certified transfer source](../src/wally/finance/transfers.py), the existing
[payment worker](../src/wally/finance/payments.py) and
[Telegram reviews](../src/wally/telegram/payment_approvals.py).
[ADR-053](decisions.md#adr-053-supervised-hlb-existing-beneficiary-transfers)
records the route and authorization boundaries.

## Workflow

1. An explicit preparation request supplies one canonical issued bill instance.
   `CertifiedTransferSource` reuses the existing bill, identity certificates,
   evidence, owner responsibility and exact identity-chain enablement. Trusted
   composition binds its bill-account page to an existing certified receiving
   account. A customer/property account identifier is never used as a receiving
   bank account. The recipient must have the same issuer and responsibility scope,
   and a `bank_transfer:<bank code>` namespace. The separate recipient certificate
   joins the immutable review fingerprint. Missing/contradictory evidence blocks
   preparation; it does not cause unrelated recertification prompts.
2. Read-only HLB preflight reuses the existing scoped 1Password login and enrolled
   picture gate. It checks the existing beneficiary, complete bank history,
   scheduled-payment and mandate inventory. Unknown/incomplete inventory blocks
   preparation. Potential duplicates include the same invoice reference, or the
   same recipient/amount/currency even under another reference. This deliberately
   conservative first route can flag an older equal-amount payment for review.
   Existing Notion receipts and shared manual/automated occurrence claims are also
   checked. The quote carries a unique 15-character tracking reference. It is shown
   on the card, entered separately from the invoice and required in bank history;
   it is not a fabricated bank-issued transaction number. The bank's own reference
   and settlement are obtained only from history. The live form/history must retain
   both invoice and tracking reference exactly for this route to pass acceptance.
3. Telegram shows the receiving bank/account, payee, amount/currency, billing
   period, invoice/reference, source account label, zero fee and expiry. It explains
   the separate required HLB bank-app action. Only the authenticated private owner
   callback on that exact delivered card can authorize its separate decision and
   execution stages. Preparing, logging in, certification and old approval cannot
   authorize a transfer. No actual card was requested in this iteration.
4. The narrow worker consumes the exact execution confirmation, reserves its durable
   occurrence/transaction claim and re-reads current facts, decision, Matter, target,
   inventory, lease and deadlines. Only then does it bind an ephemeral submission
   scope covering the complete bill and quote. HLB requires that scope before entry
   navigation, checks the filled controls before Review/Next, and checks the exact
   outgoing payment fields before dispatch. Only immediate MYR transfers
   with zero fees are supported. There is no beneficiary creation/editing, scheduled
   debit, exemption investigation, second bank or generic model browser action.
5. Review/Next is the payment submission; there is no later Submit click. The worker
   durably records `challenge_pending`
   before sending the bank-app instruction. It waits up to the profile's bounded
   timeout using read-only browser observations. It never presses another transfer,
   continuation or authorization control. Owner bank-app/biometric authorization is
   required; 1Password cannot replace it. Lost leases, timeouts, interrupted browser
   sessions and lost responses retain the claim. No automatic resubmission occurs.
6. A fresh history-page navigation in the same isolated authenticated context reads
   the outcome independently of the submit/challenge response. Exactly one row must
   match bank, receiving account, payee, amount, currency, invoice reference and
   funding label and unique reviewed tracking reference, with an independent bank
   reference. Pending, failed, unknown,
   missing, duplicate or mismatched evidence cannot mark the payment paid. An
   unchanged successful receipt remains settled across later retrieval failures.
7. Only verified settlement can create the existing narrow Notion receipt, followed
   by independent GET/query readback. Ambiguous Notion creates recover by reads,
   without repeating POST or transfer submission. Matters are not auto-closed.

The HLB route is checked on the payment worker, rather than demanding acceptance
of an unrelated utility-provider login. The generic portal-payment source retains
its portal gate. Identity/recipient certification, enablement, exact Telegram
approval, HLB route validation and independent verification all remain mandatory.
No new capability is granted to Telegram, Codex, CLI or ChatGPT.

### Review boundary correction and retained evidence

The retained `authorization-incident.json` records that opening transfer review
initiated AppAuthorise, no Telegram card was presented/approved, and no final
Submit was clicked. The consequential action is the transfer form's Next into
review, followed by owner bank-app approval. The record does not retain its exact
live DOM selector or HTTP endpoint; neither is guessed from the synthetic profile.
Back did not establish cancellation. Preserve the original incident record,
settled occurrence hold, bank receipt, sent confirmation and Notion history.

`SupervisedPayments.execute` binds a process-local submission scope only after
the authenticated Telegram decision/execution confirmations and durable attempt
claims. `HLBTransferProvider.enter_review` requires that scope **before navigating
to the transfer form**, and again before Review/Next. An arbitrary guard callback,
persisted decision or caller-supplied channel cannot create that scope. Its complete
bill/quote fingerprints bind beneficiary/bank account, amount/currency, bill and
billing-account identity, billing period, evidence, route, funding and expiry.
Current facts, decision, Matter, lease and deadlines are rechecked; expiry during
those reads also blocks dispatch. The scope is invalidated when submission exits.

The isolated browser installs a request guard before its initial navigation.
Profiles explicitly enumerate safe `read_requests`, consequential `payment_requests`
and request-body `payment_fields`; the profile fingerprint binds all three. Unknown
routes/redirects and mismatched payment fields are blocked before network dispatch.
Each consequential method/URL is claimed before dispatch, so browser retries cannot
repeat it. Read-only recovery remains available after a blocked request. The
synthetic owner-app callback is local test infrastructure, never a bypass for HLB.

There is no production request/selector profile or payment factory. Live submission
remains disabled. A separate live acceptance request still needs reviewed actual
read/auth/payment request contracts, complete duplicate inventories, an eligible
unpaid obligation and the exact Telegram card before entering the consequential
flow. The already settled Tropicana Gardens occurrence is not an acceptance candidate.

## Simulation and recovery

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m wally.finance.hlb_transfer_demo
```

This uses real installed Chrome, temporary state and a synthetic picture reference,
synthetic 1Password/Telegram adapters and in-process Notion HTTP. Every browser
request is fulfilled locally or aborted; the HLB origin is used solely to exercise
the existing exact-origin login gate. The owner's enrolled reference, `.env`,
production databases and real bank are untouched. Simulation mode requires the
explicit offline browser. No image, screenshot, cookies, HAR, trace or persistent
bank profile is written. Audit, messages and execution records are scanned for
synthetic credential leakage.

Observed dry run: one synthetic owner tap, one simulated submission, one simulated
bank-app authorization, independent history verification, one Notion create and
one independent readback. Same-update and new-update button replays moved no money.

[Test coverage](../tests/test_hlb_transfer.py) includes all reviewed material-field
mismatches, history mismatches, duplicate/incomplete inventory, the inspection
submission lock, app timeout, lost submit response, pending/failed outcomes,
read-only recovery, recipient certificate reuse/revocation and tenant exclusion.
Existing payment tests cover forged/replayed/stale callbacks, expired approval,
source/Matter/policy drift, lease loss, occurrence conflicts, ambiguous Notion writes,
through-2026 coverage and preserved settlement after failed retrieval.

The review-boundary correction passed **391 isolated regression tests** against
`0e6dcae` plus only its seven changed files, including **58 HLB transfer cases**.
Coverage includes missing/rejected/expired approval, all approved identity/amount/
period changes, expiry during the final request check, replay, mandatory bank-app
authorization, outgoing-body mismatch, unexpected navigation, preflight redirect,
browser retry and lost-response recovery. Ruff, local links, Markdown fences and
`git diff --check` passed. All Chrome traffic was fulfilled locally or aborted.

Earlier route verification covered **1,012 passing isolated tests** against the staged source,
including **37 HLB transfer tests**. The three real `.env`/Keychain mutation tests
and two local socket tests were excluded. Two interpreter-identity fixtures passed
after the temporary source snapshot linked the existing virtualenv. Ruff, all
38 local links in the affected guides/ADRs, Markdown fences and `git diff --check`
passed. The offline HLB demo also passed its credential-leak scan. No dependencies
were installed and no live provider was started for verification.

`reconcile` is the existing trusted worker's verification operation, never a transfer
retry. It can log in read-only in a new ephemeral context after the original session
closes. No new Telegram/Gateway execute endpoint, scheduler, recovery daemon or
owner setup ceremony is introduced. The composing caller must close the provider
and browser on shutdown/rejected/abandoned preparation. A timed-out attempt requires
a later explicit read-only reconciliation; it is not silently retried.

## Live readiness and the next payment

A read-only count of retained runtime state on 23 October found 21 account records,
23 definitions and 67 retained certificates, but no issued instances, enabled chains,
portal acceptances or typed `bank_transfer:` recipient records. These counts do not
imply that primary bill/payee evidence is absent elsewhere in Notion or retained
files. Reuse and map that evidence under ADR-050; do not ask the owner to certify
unrelated records or repeat a valid identity attestation.

The shipped HLB profile is explicitly synthetic. There are **no guessed live
transfer/history selector or request defaults**. `submission_enabled` defaults false, in
addition to the existing disabled/unvalidated worker gate. Supplying a YAML flag,
replaying a card or successful login cannot activate a production route. No service
was restarted, no credentials changed, no beneficiary altered and no actual
payment approval requested.

Read-only reconnaissance is limited to beneficiary/history inspection and receipt
retrieval. Never exercise `enter_review` or the form's Next control before the exact
Telegram authorization: bank-app approval can complete payment from that review.
The worker now treats entry/review dispatch as potentially submitted, supports its
mandatory challenge there and recovers by independent history without another debit
click. Complete history/scheduled/mandate inventory, reviewed live selector/request
composition and exact Telegram acceptance remain open. Live submission stays disabled.

There is no new bank enrollment, credential setup or infrastructure requirement
introduced here. Existing 1Password authorization can still require local unlock.
For the first real payment, the owner needs an eligible specific unpaid bill,
its exact Telegram approval and HLB bank-app authorization. All bills through
31 December 2026 remain administratively covered; only a direct owner instruction
identifying one particular old bill as unpaid can lift its block, and that still
requires a separate payment card. Tenant-water exclusions remain in the certified
responsibility gate. January 2027 starts clean tracking.

The recovery verified a fresh authenticated history session, exact transaction
detail, native HLB PDF download, Gmail SENT/body/attachment matching and the existing
Notion record/receipt readback. Receipt downloads require an explicit scoped read
context and remain disabled by default; authentication state is never persisted.
These observations do not validate Telegram-to-bank execution, bank-app rejection/
expiry or the complete production inventory. The duplicate hold and incident audit
remain; no retrospective Telegram approval is recorded. Casaman stays on hold.
