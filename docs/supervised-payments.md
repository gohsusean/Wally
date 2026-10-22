# v0.19 supervised payments

**18 October 2026: implemented sandbox foundation; live milestone incomplete.**
Every actual payment requires a fresh, exact, single-use Telegram payment approval.
Login approval, a financial certificate, an old proposal decision and bank
authentication cannot replace it. This owner instruction supersedes the manual
default for new automated routes; mandatory provider authentication remains intact.

## Authentication follow-up

[The reusable authentication foundation](authentication-foundation.md) standardizes
Chrome stable through the existing Playwright adapter with isolated Wally-owned
profiles/contexts, a 90-second scoped 1Password request and observed login-step
states. Offline two-stage Chrome authentication passed; one attended live 1Password
pair read took 25.1 seconds. Subsequent fresh access and complete Maybank login
remain unvalidated. This introduces no live payment route or bank-setting change.
The detailed observations below predate that follow-up where indicated.

## First route and practical channel assessment

**Owner selection, 18 October:** **Casaman Management service charge/sinking fund,
Q4 2026 (1 October–31 December), paid by bank transfer through Maybank2u.** This
replaces the earlier Mansions/TNB candidate. Maybank is the designated bank for
future bank transfers until the owner changes that choice; it is not a default for
card payments or a new debit mandate. The supplied [Maybank login URL](https://www.maybank2u.com.my/home/m2u/common/login.do)
and canonical account/definition references are retained in `config/finance.yaml`
under `payment_preferences`. The strict loader preserves this routing intent but
cannot enable an executor, approve a payment or introduce amount/recipient/secret
fields. No live Maybank execution profile is registered.

The selection identifies this specific pre-2027 bill for preparation; it does not
reopen other covered obligations or certify an unpaid current balance. A current
Q4 original, recipient match and duplicate/pending checks are still required.
The retained RM2,526.51 Casaman invoice covers July–September, not Q4; do not reuse
its amount or receiving account without checking the actual current bill.

**Authentication finding:** Maybank's [current authorization FAQ](https://www.maybank2u.com.my/maybank2u/malaysia/en/personal/frequent_queries/secure2u_tac/use-two-authorisation-methods.page)
lists new **and favourite** third-party, DuitNow and GIRO transfers as requiring
Secure2u on MAE. Favourite status is not evidence of an exemption. The selected
route has a documented category-B authentication requirement; its actual Wally
integration remains unvalidated. Preserve the exact Telegram approval and any
mandatory bank action. 1Password cannot replace Secure2u.

**1Password readiness observation, 18 October:** CLI 2.40.0 is installed and
`providers.secrets` is enabled. Opening the desktop app restored account discovery.
After the owner unlocked/approved access, vault and Maybank item reads succeeded,
while `op whoami` continued to report an unauthenticated account. Wally's health
check now uses the documented vault metadata probe, discarding output/errors;
the existing credential authorization gate remains intact. A subsequent request
timed out and worked after renewed local owner approval. App authorization is
not proven persistent or unattended. [Official app integration setup](https://www.1password.dev/cli/app-integration).

**Live auth-only result:** Wally's governed resolver retrieved the Maybank username
in memory and filled the supplied official login page in isolated Chrome. The
page remained at the username step after the login control was clicked, including
a check after page load. No password field or security-phrase prompt appeared.
The restricted session reported failed requests, including to the bank origin;
the cause of the stalled transition is unknown. No password was submitted, no
bank login was verified, and no transfer was initiated. The browser was closed
without storing a session, screenshots, traces or page-body extracts. This is an
operator diagnostic, not a registered operational execution route. Maybank's
actual login flow, authentication and independent settlement integration remain
acceptance gaps; do not enable payments based on the 1Password result. A final
single-read diagnostic, with no preceding health probe, timed out inside `op read`
before the username could be submitted. This remaining local authorization issue
is independent of the earlier readiness false negative. Public empty-form checks
confirmed both click and Enter invoke validation under the origin restriction;
they do not validate the saved username or establish authenticated access.

Categories: **A** completes after the Telegram tap; **B** also requires a mandatory
bank challenge; **C** is unsuitable/unvalidated for execution today. No real A
route has been demonstrated. The offline test route is A **in simulation only**.

- **TNB / myTNB saved-card or one-time card payment: C pending instrument and
  portal validation; B with the documented AmBank card challenge.** Statements
  and portal balances/history can establish account, amount and outcome.
  1Password can supply portal login and a genuinely supported TOTP. A saved token
  avoids copying card numbers; its exact identity must bind the prepared debit.
  Telegram approval remains compulsory. TNB describes online banking, cards,
  saved-card details and payment history in its [official announcement](https://www.tnb.com.my/assets/press_releases/20200535bi.pdf).
  This older announcement establishes available concepts, not today's DOM or
  frictionless authentication. AutoPay is excluded: it would create a mandate.
- **Air Selangor portal card/FPX/DuitNow: C until the funding method and portal
  are validated; B when the selected bank requires its app.** Current web guidance
  exposes bill/balance/due information, one-time account payments, gateway checkout
  and downloadable/emailed receipts. Portal login can use secret references;
  gateway authentication depends on the selected instrument. Independent receipt
  and account-history reads are required. [Official payment guide](https://www.airselangor.com/support/topic/details/cafb4d08-23e7-4b93-948c-e4233a676b4e).
- **IWK / JomPAY: B with an app-authorized funding bank; otherwise C until that
  bank is validated.** The bill/account supplies recipient reference and amount;
  IWK publishes its JomPAY route. 1Password can supply bank login but cannot replace
  transaction signing. Verify the exact bank receipt and issuer credit. Tenant
  responsibility is still excluded from owner payments. [IWK payment methods](https://www.iwk.com.my/my/payment-method).
- **DBKLBayar: C until gateway/instrument validation; B if bank signing is
  required.** Public account lookup and transaction history were independently
  exercised for the earlier Casaman payment. DBKL advertises cards, online banking
  and FPX. Gateway login may use 1Password, but authentication is instrument
  dependent. Issuer posting can lag, so retain uncertainty until exact evidence
  arrives. The settled 2026 Casaman bill must never be paid again.
  [Official portal and FAQ](https://dbayar.dbkl.gov.my/).
- **MBPJ assessment / land tax: C pending current route, recipient and bank
  validation.** Official historical MBPJ material documents ePay/JomPAY, but is
  insufficient for a current executable profile. Retrieve the current bill and
  verify the receiving destination; use 1Password only if its actual portal/bank
  supports it. Bank challenge and independent issuer posting remain unvalidated.
  [MBPJ official historical guide](https://www.mbpj.gov.my/sites/default/files/a4_edisi_1_2022.pdf).
- **Five management corporations, maintenance and separate management water:
  C for live execution today.** Casaman Q4 is now the selected first route, with
  a documented Maybank Secure2u requirement; a live adapter remains to be
  validated. Existing statements can supply current amount/reference and recipient
  evidence; no reviewed execution or independent settlement adapter exists.
  Authenticate a supported portal/bank using references only after authorization.
  Preserve known existing auto-debits in duplicate checks. Never assume all five
  management accounts have the same payment channel or enroll another debit.
- **Credit-card statement repayment / ordinary online banking: B with the
  documented Maybank, AmBank or HLB bank-app requirement; C where the actual route
  remains unvalidated.** Maybank is the owner-selected bank for transfers.
  Retained statements/certificates establish the identity
  starting point. A fresh balance and bank pending/settled/mandate check are still
  required. Login can use secret references; bank-app signing cannot be automated
  using a password-manager TOTP. Receipt verification must bind the exact debit
  and card account. Bank-insurance document/refund administration is not a payable.

The bank limitation is concrete: [AmBank's current card guidance](https://www.ambank.com.my/promotions/authorise-your-online-ambank-cards-transactions-with-amsecure)
requires AmSecure from 1 October 2026. Its [authentication FAQ](https://www.ambank.com.my/online-banking/faq/general/security/amsecure)
also describes device-bound approval and new-browser login approval.
[HLB's current security guidance](https://www.hlb.com.my/en/personal-banking/hlb-connect.html)
requires AppAuthorise for applicable monetary transactions and adds biometric
verification. These are mandatory customer actions, not stored TOTP challenges.
[PayNet FPX](https://paynet.my/faq/personal-fpx.html) provides transaction IDs and
receipts; merchant/biller APIs do not establish a household payer's authority to
move money. Telegram can authorize Wally's scope without replacing bank checks.

## Implemented behavior

`CertifiedPaymentSource` reuses the existing catalog refresh, certificates,
dependency bindings, owner responsibility and chain enablement. No recertification
prompt is added. Preparation accepts an issued instance ID, not a model-authored
amount, recipient or URL. Certified evidence is the starting point; a current
provider quote and Notion/provider duplicate checks remain necessary.

The immutable specification binds the exact account, payee/destination, invoice,
occurrence and period, MYR amount, evidence and catalog versions, funding token
label, prepared provider transaction, expiry, route and receipt target. The initial
contract permits zero fees only. Proposals contain advice and references; they
cannot be dispatched as tool payloads.

The private-owner delivered card shows full payment details and expiry. A real
callback must match the numeric owner, private chat, message, random nonce, exact
scope and allowed action. Forwarded/copied/inaccessible messages and bot senders
cannot authorize it. The review is durably consumed before effects. Approve
authorizes **two separate runtime stages**: a proposal decision and one execution.
The new worker has only payment execute/verify and context-read capabilities.
Telegram, CLI, Codex, hosted ChatGPT and generic Act & Verify acquire no payment
execution capability. No Gateway payment operation is added. Legacy n8n payment dispatch is now
blocked before local approval or credential retrieval; its login-only browser
review remains available. A local prompt cannot substitute for the Telegram card.
This deliberately trusts the private Telegram owner account and bot transport;
account/bot-token compromise can authorize a payment. Bot cloud chats are not
end-to-end encrypted, and the Mac must be awake, logged in and online.

The executor atomically reserves both proposal and canonical account/occurrence,
sharing the existing manual-handoff claim namespace. It then resolves credentials
through `GovernedSecretsResolver`, fills only configured browser fields and obtains
supported TOTP directly using 1Password's [documented OTP attribute](https://www.1password.dev/cli/reference/commands/read).
The TOTP seed never enters Wally. Credential retrieval never supplies payment
authority. Source, approval, Matter, target, provider quote, duplicate evidence,
deadline and live worker lease are reread after the tap and credential retrieval,
and immediately before submission. Only the registered deterministic adapter can
click the payment control; no LLM sees authentication values or selects actions.

Sessions use fresh in-memory browser contexts with exact origin restrictions,
blocked service workers/WebSockets, no persisted cookies, traces, screenshots,
HAR or video, and explicit cleanup. A mandatory challenge detected before submission stops the first slice. A
challenge detected after submission keeps an uncertain claim and explicitly asks
for the bank action; later recovery only reads the result. **Automatic browser
continuation through an assisted challenge is not implemented**.
An exception asks for owner action and a fresh review, not a bypass or blind retry.

Submission itself never proves payment. A separate provider-ledger read must
match the prepared transaction, funding, recipient, amount/currency and invoice,
and return settled status, a receipt reference and evidence hash. Typed evidence
and immutable event history remain in operations storage. Settled payments remain
settled if a later retrieval fails. Contradictory receipt evidence is investigated.
No unrelated Matter is automatically resolved.

`NotionPaymentHistory` checks approved operational classification, pinned schema,
parent and duplicates, creates only the reviewed receipt properties and performs
independent GET/query readback. A lost POST response preserves payment settlement
and an uncertain record claim; recovery reads the existing record and never
repeats POST. Notion lacks a demonstrated cross-writer atomic create-if-absent;
concurrent external writers remain a residual duplicate-record risk. That risk
never authorizes another payment. It does not update recurring definitions or
certify financial identities.

## Baseline, recovery and rollout

All obligations through **31 December 2026** are administratively covered, without
inventing settlement receipts. Runtime observation retains incoming evidence but
does not open/reopen financial attention from unbound invoices/reminders. New
tracking needs a canonical 2027 billing period. No existing records or Matters are
mass-closed or rewritten. An optional direct private-owner `/unpaid <instance-id>`
instruction identifies one exact old obligation as unpaid. It is bound to the
current catalog version, cannot be forwarded or inferred from source text, and
still requires a separate exact payment card. It exists only in a composed
payment runtime; the production poller has no payment factory configured.

Any started attempt, including interrupted login, preserves its duplicate claim.
Uncertain submission/settlement never retries automatically, and changing amount,
destination or invoice does not release the occurrence claim. `reconcile` reads
the provider and can recover Notion readback without calling `submit`. No automatic
failure-release API, mandate enrollment or autonomous debit is provided.

**Current readiness:** a read-only inspection of retained operations state found
21 account candidates, 23 definitions and 15 providers, but **no issued instances,
chain enablement or linked portal credential references**. Those are observed
configuration facts, not a reason to discard retained certification. No live
credential read, bill retrieval, Telegram delivery, account change or payment was
performed. The existing running services were not reloaded.

The production builder exposes a trusted `payment_factory` composition seam only.
It defaults to absent; no YAML flag enables a generic or unvalidated live route.
A real route still needs: the selected funding instrument/reference, verified
current issuer balance and duplicate inventory, reviewed provider-specific DOM/API
and history adapter, governed portal acceptance/enablement, and a pinned existing
Notion Payment History schema. The generic browser selectors and receipt schema
are a sandbox contract, **not fabricated live TNB configuration**. Gmail ingestion
and evidence certification already exist; automatic document extraction into a
certified issued instance and a monthly scheduler are not delivered by this slice.

## Reproducible dry run

Use the existing environment and installed Chrome, with a fresh temporary profile:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m wally.finance.payment_demo
```

All browser requests to the `.invalid` sandbox origin are fulfilled in process.
Notion uses an in-process HTTP transport; Telegram and 1Password use synthetic
adapters. The demo never loads `.env` or production stores and cleans up its
temporary database/profile. It verifies synthetic login/password/TOTP values in
the DOM before permitting the simulated submission. It scans retained messages,
execution data and the database for synthetic credential leakage.

**Observed acceptance, 18 October:** one simulated owner tap; one submission;
one independent ledger read; three credential/TOTP resolutions; one Notion receipt
creation and one independent readback; verified success. Same-update and new-update
button replays submitted nothing further. This establishes the sandbox workflow,
not live bank authentication, genuine Telegram human acceptance or real settlement.

Regression tests are in [test_supervised_payments.py](../tests/test_supervised_payments.py).
Validation: **905 isolated tests plus 2 reviewed local transport tests passed**;
the three real `.env`/login-Keychain mutation tests were excluded. Ruff, local
documentation links/fences and `git diff --check` passed. These checks started no
live mailbox, bot, bank or biller session.
See [ADR-051](decisions.md#adr-051-exact-telegram-supervised-payments) and the
[roadmap](roadmap.md#v019--supervised-payment-automation-in-progress).

## HLB continuation, 23 October 2026

**23 October final owner decision:** HLB is the sole transfer bank; Maybank
development and authorization-exemption investigation are stopped. Bank-app
authorization is required and the owner completes it; every payment still
needs its exact Telegram approval. The [local security-picture gate](hlb-security-picture.md)
is implemented with synthetic acceptance; owner enrollment and live acceptance remain.
This supersedes earlier bank-selection and investigation notes.
