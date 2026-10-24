# HLB production approval wiring — 24 October 2026

The real `telegram poll` entry point now supplies
`build_production_payments` to the existing trusted factory seam. It registers
the existing HLB provider, exact Telegram payment reviews, narrow payment worker,
certified transfer source, native Notion receipt adapter and Gmail receipt handler.
Startup performs no bank login, payment, email or Notion write. It initializes
only the existing additive payment tables.

Live execution is locked off in trusted code, and the production profile loader
rejects submission enablement and synthetic mode. No YAML or owner message can
enable payment submission. The existing exact Telegram scope remains mandatory
before transfer entry, Next/Review and payment requests. HLB AppAuthorise remains
a separate owner action; the offline bank simulates that action only.

The authenticated private owner's `/pay <issued-instance-id>` requests preparation
and the separate payment card. It does not authorize payment. Read-only preparation
has a separate gate, so disabling execution need not prohibit preparation. The
original source, billing identity, baseline, duplicate and evidence checks remain.
Forwarded preparation instructions are refused. A missing route stops before bank
access and reports a bounded diagnostic.

## Concrete remaining route gap

`config/hlb-payments.yaml` records the actual state: no reviewed live profile or
recipient binding is installed. The canonical source also has no registered
invoice extractor in this composition. These are unresolved parts of one live
route registration, not permission to guess mappings or bypass evidence checks.
Historical transfer selectors do not establish the exact outgoing request contract.
Consequently a genuine production bill card cannot yet be prepared by this route.

Completing that registration requires the actual HLB selector/request/history
contracts and the specific unpaid bill's existing recipient and invoice evidence
bindings. Transfer Next/Review must never be used for reconnaissance. The paid
Tropicana occurrence and its existing email/duplicate holds must remain untouched.

## Runtime evidence

On every actual poller startup, `Telegram payment runtime:` reports its PID,
provider class, both disabled submission gates, profile presence and hashes of
the imported entry/review and request-guard code objects. Compare that PID with
launchd and those hashes with the checked-out implementation. A restarted PID
alone does not prove composition. No credential or bank account values enter this
diagnostic.

The production-builder acceptance test substitutes locally intercepted bank
pages and mock Notion/Telegram effects. It verifies the sequence: prepare, present
card, authenticated owner callback, durable claim, HLB entry/Review, simulated
bank-app authorization, independent history verification and Notion readback.
Replay performs no additional submission. This is offline evidence, not a real
Telegram callback or live HLB route acceptance.

Focused verification: **222 tests passed** across production composition, HLB
authorization/transfer, supervised payments, receipt email/native history and
Telegram/LaunchAgent behavior. Ruff and whitespace checks passed.

The actual LaunchAgent was reloaded to **PID 6833**. Its startup diagnostic reports
`HLBTransferProvider`, `exact_telegram_before_hlb_entry_v1`, no configured live
profile and both submission gates false. Both imported code hashes matched the
checkout. The two existing manual occurrence holds were byte-for-byte unchanged;
there were zero supervised payment attempts. No transfer or confirmation email
was initiated. The prior poller's lease is allowed to expire normally; it is not
deleted to accelerate takeover.

The initial bounded 1Password probe failed while the Mac was locked. After the
owner unlocked the Mac and 1Password, vault metadata access succeeded. The scoped
HLB login-pair read still required the separate **1Password Access Requested →
Allow ChatGPT to get CLI access → Authorize** prompt. Authorizing that existing
request restored the governed pair read. No security setting or credential changed.

The subsequent isolated HLB session passed the existing username, enrolled
security-picture and password checks and verified the authenticated dashboard.
Its network guard denied transfer paths and unreviewed POSTs. No transfer form,
Next/Review, bank-app authorization, payment, email or Notion write occurred. All
owned browser contexts were closed. A history-menu lookup was ambiguous and
stopped without clicking or dispatching another request; it is not live history
acceptance.

The retained 23 October session supplies transfer field IDs, but no captured
payment request body/contract. Its favourite flow exposes recipient display
fields, whereas the current adapter expects a recipient select control. Thus
unlocking 1Password does not complete the missing live bank adapter contract.
Production remains blocked at `profile: null`; the owner does not need another
unlock operation to resolve this engineering gap. Mapping cannot be inferred
from successful login or simulated HTML, and an unapproved live transfer form
cannot be opened to discover it.

Telegram preparation now reports 1Password CLI authorization failures explicitly
instead of incorrectly asking the owner to check bill evidence. The response
uses fixed text, closes the bank context and never includes provider error text.

Follow-up verification: **58 focused tests passed** for production wiring,
authorization boundaries, Telegram and secrets, with Ruff, whitespace and document
checks passing. The actual poller was reloaded to **PID 8288**; imported hashes for
entry/review, outgoing-request policy, the approval callback and preparation all
matched the checkout. Both submission gates remained false, there were zero
supervised attempts, and the original two duplicate holds were unchanged.
