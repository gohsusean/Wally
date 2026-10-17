# v0.18.2 financial certification coverage — 18 October 2026

**55 live records: 47 certified; 8 genuinely require more evidence.** All 15
providers, 15 of 19 accounts and 17 of 21 recurring bills are certified against
retained reliable evidence. This includes both personal credit cards and Arkadia
maintenance. Five evidenced property prerequisites and the personal subject are
also certified, outside the 55-record denominator. Four deleted IWK records and
all old certificates/audit history are retained separately.

Under [ADR-050](decisions.md#adr-050-financial-certification-follows-reliable-evidence),
reliable evidence reasonably verifying material facts IS certification. There is
no separate owner attestation, terminal confirmation or dependency-certification
requirement. The existing coverage review and certificate tables are reused.
Complete retained field reviews now persist certification automatically. Failed
source retrieval preserves valid verification; observed material changes,
contradictions and changed evidence invalidate affected verification. Financial
execution still requires fresh source/chain checks and all existing authorization.

## Remaining evidence: one document per property

- **Sentral Suites — DBKL assessment account and recurring bill:** primary
  assessment bill showing the account, property and bill/due rules. The retained
  Pay@KL receipt identifies an assessment account but lacks the property address
  and issued-bill rules.
- **Plaza Arkadia — DBKL assessment account and recurring bill:** the same fields
  on one issued assessment bill. Its Pay@KL receipts are retained; Arkadia's
  parcel-rent bill verifies a different obligation and cannot substitute for DBKL.
- **110 Mansions — DBKL assessment account and recurring bill:** one issued
  assessment bill tying its configured account to this property and bill/due rules.
  No usable bill was located in the connected-source searches.
- **Tropicana Gardens — MBPJ assessment account and recurring bill:** one issued
  assessment bill or complete issuer account statement showing the account,
  property and bill/due rules. The official MBPJ issuer identity is certified
  independently from its [public portal](https://epay.mbpj.gov.my/auth/login/).
  The owner reports paying on 17 October, but the promised receipt was not attached
  and is absent from connected Gmail. That receipt would verify the payment facts;
  it only substitutes for the bill where it actually supplies the missing fields.

No owner confirmation of already documented facts is requested. These four
property-specific documents cover all eight remaining records. Current balances,
actual payments and refunds remain separate from recurring certification.

## Evidence reused and verification

The complete Air Selangor original was recovered from its existing email. Relevant
account/due fields in the retained Sentral/Mansions parcel-rent extracts were
usable despite a connector truncation warning. Arkadia's original scanned parcel-
rent bill was retrieved and visually reviewed. Existing owner-to-bank account
references, bank correspondence and current annual insurance certificates support
certificate/refund **administration only**, not premiums or completed refunds.
No replacement evidence or new owner attestations were fabricated.

Private per-record reviews, source hashes, reasons, current certificates and
remaining gaps are in ignored `data/financial-certification/2026-10-18/`. A pre-
policy database backup preserves the old history. An independent live inventory
confirmed 15 providers, 19 accounts and 21 definitions. Existing personal-subject
bindings are restricted to the two named card pages; property remains required
for all other accounts. PDF passwords, full card numbers and banking credentials
were not exposed or committed.

Certification does not enable chains, portals, payments, schedules or v0.19. The
rollout is **incomplete while these eight records lack evidence**.

Validation: the exact staged implementation passed 826 isolated tests and two
reviewed local socket tests; the shared checkout passed 850 isolated tests. Ruff,
documentation links/fences, retained evidence hashes and independent certificate
counts passed. Existing chain/portal enablement and execution state is unchanged.

The following older reports are historical and their separate-confirmation policy
is superseded.

---

# Historical certification coverage — 12/17 October 2026

**17 October follow-up:** the current Notion inventory is **55 live records**:
15 providers, 19 accounts and 21 recurring bills, with **0 certified**. Both IWK
account/bill pairs are now deleted in Notion; their four historical records remain
in the catalog audit, whose total is 59. The owner deleted the empty Bill Account;
an independent Notion fetch and the runtime refresh confirm its removal. Its
historical assessment is retained; its blank identity no longer blocks accounts.

The current source assessment has **21 remaining primary/owner-evidence gaps**.
Four additional card records have complete source evidence and owner arrangement
answers but still require local personal-subject/source setup before canonical
review/certification. The
existing raw catalog worklist combines unavailable/unregistered records with
missing evidence; restricted `data/financial-certification/2026-10-17/assessed-coverage.json`
separates current records, retained deletions and source-setup blockers.

AmBank currentness and manual arrangement are owner-confirmed. The supplied Hong
Leong September statement was reviewed, with issuer, safe last four and currency
corrected and independently fetched. The owner confirms manual payment and that
the September statement due 12 October is paid; this is owner-reported settlement,
separate from recurring certification and independently checked bank evidence.
Its account notes and email source were updated and independently read back;
the recurring bill is waiting for the next bill. The owner expects Wally to handle
both cards each month as soon as a statement becomes available through the regular
statement-to-approved-payment workflow. Statement-only PDF passwords are
now operational metadata in Bill Accounts' existing `PDF Pass` field under
[ADR-049](decisions.md#adr-049-statement-pdf-passwords-are-operational-metadata).
AmBank opening through that field passed; the supplied Hong Leong copy needed no
password. No certificate, scheduler or payment capability was enabled.

The complete Sentral management-water invoice was recovered from its existing
Gmail message and visually checked. Its printed due date and invoice type fill the
two missing field checks; its proof remains unattested and uncertified. The Arkadia
water statement matches the retained original. A newly supplied GProp screenshot
establishes unit identity and November/December service-charge/sinking-fund lines;
the older fee estimate is historical. Two subsequent invoice-detail screenshots
match its service-charge invoice numbers and establish the printed invoice/due
dates. They close the remaining two recurring field checks; the proof is prepared
with attestations still false. Arkadia is now monthly in both existing frequency
fields, independently read back. The 1st-to-14th examples are 13 elapsed days,
not a universal invoice-date-plus-14-days rule. Due dates remain document-defined;
no sinking-fund deadline or current settlement is inferred from those screenshots.
Both provider URLs were independently fetched from Notion.

The owner confirmed that water charged through management billing belongs to the
tenant. Arkadia and Sentral already carried `Should I pay? = Do not pay`; both now
also have `Active` disabled and were independently read back. They remain in the
tracking inventory and certification denominator. Shared management accounts
retain owner scope for service charge/sinking fund only. No standalone water
account was reclassified. The owner subsequently confirms tenant responsibility
for water-related fees and late-payment interest, resolving the prior allocation
question. A mixed statement's total is not owner-payable evidence.

The new `Should I pay?` property is not consumed by the current canonical runtime.
The existing material `Active` field excludes these two owner-obligation chains;
their reviews were rebound after verifying that sole typed change. Arbitrary
future edits to the new column do not yet enforce runtime exclusion or invalidate
certification. Local source-designation requirements remain; no new verification
mechanism or payment capability was added.

The owner reports paying Tropicana Gardens MBPJ today. Only the Arkadia screenshot
was attached, and connected Gmail has no new MBPJ receipt. The report is recorded
separately in account notes; no amount, receipt number, covered period, zero balance
or independently verified settlement was invented. The missing MBPJ receipt remains
outstanding in this batch. The owner confirms Arkadia auto-debit is current and
covers owner service charge/sinking fund only, excluding water-related charges.
Handle invoices promptly on availability, while checking expected/actual debit
before any additional manual payment. Auto-debit enrollment is not settlement.
Rules absent from a receipt still require an issued bill. Documents will be reused across
applicable records.

All card owner questions are closed. Fresh local certification confirmations and
personal-subject/source designation remain required under ADR-044. A sandboxed
source refresh failed temporarily; the permitted-network retry succeeded without
source exceptions. No historical certificate was restored.

The following report preserves the **12 October snapshot** and its original counts.

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
