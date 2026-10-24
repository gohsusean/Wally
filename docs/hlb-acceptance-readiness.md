# HLB acceptance readiness — 24 October 2026

**Not ready for a live payment acceptance test. Submission remains disabled.**
Inspection starts at `d95e27b`; observations are point-in-time checks around
11:00 MYT. No bank was contacted, payment initiated, email sent, Wally service restarted,
credential changed or production record written. Unrelated local edits are preserved.

## Verified checklist

- [x] The [HLB adapter](../src/wally/adapters/browser/hlb_transfer.py) requires the
  exact Telegram worker scope before entry/review and outgoing payment dispatch.
  Offline regressions cover identity/amount/period drift, expiry, rejection, replay,
  bank-app authorization, unknown routes, redirects and retries. New cases deny
  unmapped GET/POST/PUT requests without approval while retaining history reads.
- [x] The [production poll entry point](../src/wally/telegram/poll.py) supplies no
  `payment_factory`. Its default composition registers no payment worker, payment
  confirmer or payment-card handler; Telegram/CLI/REPL lack execute/verify payment
  grants. This absence is tested, not presented as an installed payment workflow.
- [x] Wally's configured Notion credential verified the pinned Payment History
  parent, field types and both account/bill relation targets. Live queries found
  exactly one Tropicana Gardens Q4 2026 account/period record and one bank-reference
  record: MYR 960.96, paid 23 October, correct account/bill relations, confirmation
  sent. The native receipt downloaded and matched the retained original PDF hash.
- [x] Wally's configured Google OAuth credential refreshed. The existing receipt
  verifier confirmed the original Gmail message's SENT label, exact recipient,
  subject/body and PDF hash. Connected Gmail also returned one matching sent
  confirmation in the incident date range. No send/draft/self-check was performed.
- [x] Wally's Keychain bot reference authenticated via Telegram `getMe`; no updates
  were polled/acknowledged, callbacks consumed, messages sent or cards created.
- [x] The actual local occurrence reservation and unique duplicate index remain.
  The existing payment guard, run against production SQLite in read-only/query-only
  mode, rejected that exact occurrence. Changing its amount does not change the
  occurrence key. The other existing hold and through-2026 baseline remain.

## Historical trigger and mapping evidence

The retained inspector and session recover the action: `review` located the
transfer frame with `#idAmt` and clicked `#idBtnSubmit` when its value was Next/Review.
The session records `correct-reference` then `review` at 15:40:13 UTC on 23 October
(23:40:13 MYT), following an earlier entry-validation failure. Review initiated
AppAuthorise; bank-app approval completed payment without a final Submit click.

Historical frame paths were `/rib/app/fo/trx/3rdpartytrsf` and `/rib/app/fo/trx/hist`;
observed fields include `#idAmt`, `#idAcctNo`, `#idDesc` and `#idOthInfo`.
**These observations are not validated production mappings.** No HTTP request
endpoint/method/body capture was recovered; a frame path is not that evidence.
Unknown requests are demonstrated to fail closed offline.

Private evidence remains in `data/manual-payment/tropicana-gardens-2026-q4/`, Codex
session `01a168ef-e54f-7651-91fa-4a669c5d80ed`, and the retained temporary inspector.
`/private/tmp/wally_hlb_inspector_actions.py` has SHA-256
`09e0696fe9f4f70df64d9788b8262a78883a62fc5671e17b421760cc5328d1fa`.
It used raw browser clicks outside the supervised worker and bypassed its gate.
No matching inspector process was running. Preserve it for forensics only; do not
restart it. The worker's guard cannot constrain unrelated browser tools or owner
bank-app actions. Back/expiry is never proof that an initiated payment was cancelled.

## Outstanding checklist and intervention

- [ ] **Production composition/mappings are absent.** Only the offline demo
  constructs the HLB provider. There is no live beneficiary/funding/form/request/
  history/receipt profile or registered payment factory. The runtime catalog has
  no issued instances, enabled chains or typed `bank_transfer:` receiving accounts;
  its operations database has no supervised-payment attempt/specification tables.
  Complete history/scheduled/mandate inventory is unverified. Engineering must
  review the actual request contracts and compose the existing components with
  submission disabled. Do not guess mappings or open review to discover them.
- [ ] **The running poller predates the fixes.** Its process started 21 October,
  22:44:41 MYT. The LaunchAgent uses this checkout's editable `.venv` installation,
  but current disk source does not establish imported in-memory code. Distribution
  metadata is `0.13.0`; source declares `0.18.2`. Reload/version verification is still
  needed after composition; restarting alone would not supply the missing factory.
- [ ] **Fresh 1Password/HLB access is unverified.** The CLI exists, but the configured
  vault-metadata probe and bounded diagnostic retry timed out after 15 seconds.
  Opening the existing 1Password app and repeating the metadata probe also timed
  out. No bank credential fields were read. Owner unlock/desktop CLI authorization may
  be needed; the timeout alone does not establish why. Historical bank login and
  the retained picture reference do not prove fresh HLB readiness.
- [ ] **Live callback/delivery acceptance is incomplete.** `getMe` proves bot
  authentication, not a production payment card or owner callback. The verified
  existing Gmail send is historical, not a new delivery acceptance test.
- [ ] **Historical local metadata is not fully reconciled.** The paid attempt is
  still `reserved`, without a finalized success hash/reference on that row; it
  blocks retry. The old sent claim's `definition_page_id` identifies the linked
  provider, not the recurring bill. The live Notion recurring-bill relation was
  independently verified correct. Do not reuse that claim to patch relations,
  fabricate a success/approval, resend mail or release the occurrence hold.
- [ ] **A different genuinely unpaid obligation is required.** Tropicana Gardens
  Q4 2026 is paid and never an acceptance candidate. Any eventual payment needs
  separate unpaid-obligation identification, exact transaction-specific Telegram
  approval and mandatory owner HLB app authorization. No unrelated certification
  or retrospective payment approval is requested.

## Smallest next action

Restore owner-authorized 1Password desktop/CLI access, then rerun only the bounded
metadata probe. Reviewed production mappings and disabled composition remain
engineering prerequisites before reload verification and a separate acceptance
request. See the [HLB workflow and safeguards](hlb-supervised-transfers.md).

## Verification

313 offline tests passed against an isolated `d95e27b` snapshot plus only the new
tests/checklist, including 58 HLB transfer cases. Ruff, local documentation links,
Markdown fences and `git diff --check` passed. Live checks were limited to Notion
reads/read-only queries, receipt downloads, Gmail reads/OAuth refresh, Telegram
`getMe`, process/file inspection and bounded 1Password metadata probes.
