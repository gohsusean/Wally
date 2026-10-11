# v0.18.2 financial certification coverage — 12 October 2026

The rollout is **incomplete**. A fresh, fully paginated Notion inventory and an
independent runtime refresh contain **59 records: 15 Bill Providers, 21 Bill
Accounts and 23 Recurring Bills**, including both new personal credit cards.
All 59 records were assessed; malformed rows were retained in the denominator.
Five property prerequisites are assessed separately, outside these totals.

- **Currently certified against primary evidence: 0.**
- **Uncertified: 59.** Of these, 45 have field evidence reviews, nine lack usable
  primary evidence, and five have unresolved card identity/configuration gaps.
- **Missing or incomplete supporting evidence: 31 records**, overlapping those
  categories: two providers, 13 accounts and 16 recurring definitions.
- **Complete field reviews awaiting owner certification: 28 baseline records**
  (13 providers, eight accounts, seven definitions), plus five properties. This
  means fields were compared, not that dependencies or certificates are ready.
- **Outstanding owner policy decisions: 0.** The quarterly planning decision is
  recorded. Fresh local terminal confirmations, primary documents and personal
  subject/source designation remain required; they are not waived by this count.

## What changed and what remains

The pre-audit runtime had six historical identity certificates, four then current:
the TNB provider/account/definition and TM110 property. Their original bill and
portal evidence hashes were checked and reused. The first runtime source refresh
encountered a sandbox network failure; existing fail-closed reconciliation
invalidated those certificates. A subsequently permitted network refresh succeeded.
Recovery does not resurrect authority. Fresh owner certification is required;
historical certificates, versions, events and the pre-audit backup were preserved.

Objectively evidenced corrections were applied through the owner's existing Notion
connection and independently fetched: three missing physical property addresses;
Arkadia's printed management issuer; HSBC Amanah rather than HSBC Bank; full SCB
and IWK issuer names; the HLB issuer name; and MBPJ's current council name from
its [official portal search excerpt](https://www.mbpj.gov.my/home). The complete
MBPJ page could not be retrieved, so that provider remains in the missing-evidence
count. Credit card typed metadata now uses
the safe last-four namespace and statement-defined variable amounts/due dates.
No safe card digits or currency were invented. No statement balance, minimum
payment, actual payment or settlement status was altered by this rollout.

Casaman and Tropicana retain quarterly planning cadence: all three calendar-quarter
months are planned for settlement by the first month's issuer due date. The issuers
still issue separate monthly charges and deadlines. The owner rule was added to
existing account notes and recurring-page content and independently read back.
It is not a changed legal deadline or authorization to pay.

## Evidence batches

Every provider and related account/definition is represented in these batches.
The private journal records each source locator, exact candidate fingerprint,
reviewed fields, evidence references/hashes and missing fields.

- **Management — Casaman, Mansions, Sentral, Tropicana, Arkadia:** original
  retained management PDFs and a complete Tropicana primary extraction establish
  issuer/unit identities. Four maintenance definitions have complete field reviews;
  Arkadia's original document establishes its separate monthly water charge, not
  regular service-charge cadence or contract. Sentral water has only a truncated
  extraction. Those two definitions need complete obligation evidence. Historical
  auto-debit information is not confirmation of a new payment or current balance.
- **Utilities — TNB, IWK, Air Selangor:** TNB original evidence is reused,
  including the portal identity screenshot because its bill masks the account
  number. Casaman IWK has a complete current account statement and semiannual
  charge periods, but needs issued due-rule evidence for its definition. Tropicana
  IWK's 2024 statement is historical; current identity/due evidence remains absent.
  Air Selangor has a truncated older invoice; current complete account/obligation
  evidence remains required. Tenant responsibility and inactive/conservative
  configuration are preserved from earlier explicit owner decisions.
- **Taxes — DBKL, PTGWP, MBPJ:** the retained Casaman DBKL primary bill supports
  that account and semiannual definition. Other DBKL account searches yielded
  receipts rather than issued obligation evidence. PTGWP evidence for Sentral,
  Mansions and Arkadia is partial, historical or receipt-only; all three account
  and definition pairs need complete current primary documents. MBPJ has no usable
  primary provider/account/definition evidence in the available search results.
  Casaman's already recorded settlement is separate from recurring certification.
- **Bank insurance administration — HSBC Amanah and SCB:** primary certificate
  extracts identify the mortgagees and associated properties. Owner-confirmed
  scope is certificate collection/submission and refund follow-up, not premiums.
  Complete bank identity documents and current administration/due rules remain
  missing for both account/definition pairs. Neither a sent refund request nor an
  insurance period proves a refund, new premium obligation or payment completion.
- **Personal credit cards — AmBank and Hong Leong:** both provider/account/
  recurring chains are included. HLB's primary approval email supports issuer
  identity only. AmBank notifications cover AmBank/AmBank Islamic without resolving
  the actual card issuer, and statement attachment retrieval was unavailable.
  Neither card has a usable redacted statement or safe account digits. Both require
  issuer/account identity, statement cycle, frequency, document-defined due rules,
  currency and any evidenced payment arrangement. No property association is
  applicable. Its absence is not a financial error; the currently designated
  property-only ingestion mapping still needs explicit personal-subject binding.

## Unavoidable exceptions and operator handoff

Attachment extraction was attempted for remaining source groups. Full attachment
download was unavailable; some connector extractions were explicitly truncated.
Readable primary issuer lines can establish provider identity without establishing
the missing account or obligation fields. Notifications, receipts, legacy typical
amounts and owner-sent requests were not promoted into missing primary facts.
Additional documents should be redacted before intake, especially card statements.
Full card numbers, CVVs, PINs and banking credentials must never enter these artifacts.

The existing certification boundary requires authenticated local-owner authority
and fresh confirmation of the reviewed version ([ADR-044](decisions.md#adr-044-certified-financial-identities-and-local-owner-certification)).
Telegram has no certification capability, so its metadata approval mechanism cannot
complete this step. Ordinary authorized metadata corrections used the existing
Notion connection without granting Telegram or Codex new financial authority.

The [reusable workflow](financial-certification-rollout.md) includes explicit
personal exceptions and material-note/legacy-cadence fingerprinting using existing
properties. These supplemental mappings are **prepared, not live-designated**;
local entity intake and fresh source designation are still required. Typed material
edits already invalidate certificates on refresh. Supplemental edits will invalidate
them once the reviewed mappings are designated. This is read-time reconciliation;
edits reverted between reads cannot be detected by the current provider interface.

Restricted artifacts are retained in ignored
`data/financial-certification/2026-10-12/`: `assessed-coverage.json`,
`assessment-journal.json`, `field-reviews.json`, `retained-evidence-index.json`,
`review-order.json`, independent readbacks and `prepared-finance.yaml`.
There are 33 ordered proof templates covering complete field reviews and property
prerequisites; all owner attestations are deliberately **false** until the owner
reviews and fills them. Refresh before each certification and re-review any drift.
Do not restore the backup to resurrect certificates or activate the placeholder
personal-subject mapping.

The workflow changes are tested; the live baseline is not certified. No new payment
capability, financial transaction, chain enablement or v0.19 implementation was
started. v0.19 remains gated on completing certification or explicitly reviewing
the remaining exceptions without misrepresenting them as certified.

Validation: the exact staged commit passed 818 isolated tests and two reviewed
socket tests, independently of existing uncommitted work. The shared working tree
passed 842 isolated tests plus those two socket tests. Ruff, local documentation
links/fences and diff checks passed. Tests that rewrite the real `.env` or touch the
login Keychain were excluded; no dependencies were installed or live providers
started for verification.
