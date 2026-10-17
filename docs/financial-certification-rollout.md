# v0.18.2 financial certification rollout

The workflow prepares an evidence-backed recurring baseline using the existing
ADR-044 catalog and local-owner certification service. It does not enable any
chain, portal access, payment, scheduler or v0.19 capability. The live rollout is
**incomplete**. Coverage includes every Bill Provider, Bill Account and Recurring
Bill, including inactive rows and personal credit cards; property/entity records
are certification prerequisites and reported separately.

## Reusable operator workflow

The definitive policy is [ADR-050](decisions.md#adr-050-financial-certification-follows-reliable-evidence).
Reliable evidence reasonably verifying a record's material details IS certification.
No separate owner confirmation, attestation or approval is required.

1. Run `wally finance coverage --reviews <retained-field-reviews.json>` against the
   existing approved sources. Reuse the exact-version field-to-evidence reviews
   and retained source hashes. Complete, noncontradictory reviews automatically
   persist the existing identity certificates; incomplete ones remain uncertified.
   No proof template or simulated human confirmation is needed.
2. Compare applicable identity, scope, recurrence and amount/due/evidence policies.
   Use documents for the material fields they actually establish, including
   reliable relevant fields in a partial extraction. A truncation warning does not
   negate visible facts; it cannot supply fields that are absent. Receipts may
   establish identifiers without proving an issued bill, deadline or current
   settlement. Correct ordinary metadata through the owner's existing Notion
   connection and independently read back any change before refreshing reviews.
3. Certify each record independently of unrelated missing evidence or another
   record's certificate. Preserve historical evidence and certificates on failed
   retrieval; block operational use until fresh required sources are available.
   Actual material revisions, contradictions and changed evidence require a new
   evidence comparison. Consolidate genuine gaps by source document and ask only
   for missing evidence or genuine decisions. Report actual counts and exceptions.

Certificate persistence grants no principal, execution, verification, payment or
portal capability. Existing classification, source designation, operational
namespace/dependency checks, chain enablement, portal acceptance and fresh action
authorization remain separate. The legacy explicit certification entry point is
compatible but is not a required identity step.

## Personal obligations and direct Notion edits

Keep the existing provider → subject → account → definition → instance model.
The two already evidenced personal cards use the existing local personal subject
and exact page bindings; no new Notion entity database is required. A
reviewed account source may add
`personal_subjects`, an exact Notion page UUID → local canonical entity ID map.
The core checks that the entity actually exists and is the local personal subject.
Only those explicit pages with a completely readable empty property relation
become personal accounts; the property relation stays required for other pages. Other missing properties remain unresolved scope. An
added property contradicts the personal binding and invalidates it; failed reads
and multi-relations cannot select the exception. Broader source-access and portal changes retain their separate authorization rules.

Material typed edits already revise the candidate and permanently invalidate
certification, including direct Notion edits and later reversions. To observe
material notes or legacy cadence without inventing new Notion properties, a source
can specify `review_fields` using stable existing property IDs. Supported names
are `obligation_notes`, `legacy_frequency`, `statement_cycle`, `payment_arrangement`;
only plain text/select values are supported. They contribute a content hash to
candidate versions; raw notes are absent from the catalog, status and prompts.
Use Bill Account Notes for statement-cycle/arrangement or owner planning notes,
and Recurring Bills Frequency for legacy cadence. A changed/deleted/unreadable
mapped review field invalidates certification and dependent chains. Empty mappings
preserve existing fingerprints/designations. New mappings require designation and
fresh certification. Nothing infers privileged instructions from notes.

This remains read-time/pre-action reconciliation. An unobserved edit, or an edit
reverted between reads, is unknown. The current live mapping does not yet watch
these supplemental fields; an inert prepared mapping is retained with the private
rollout artifacts. Do not claim continuous monitoring or installed personal-card
readiness before entity intake and source re-designation are complete.

## Tenant water within management billing

The owner excludes all management-billed water from owner payments, whether it
appears separately or alongside maintenance and sinking fund. Keep the shared
management account for owner service-charge scope and the existing separate water
definitions for tracking. Use the owner's existing `Should I pay? = Do not pay`
property. The current canonical mapping does not consume that column, so retain
the existing `Active = false` exclusion for these tenant water definitions before
any owner-obligation activation. Do not treat a display flag alone as an execution
gate. Re-read both fields after edits and refresh the existing catalog; compare
material revisions and rebind field reviews only after independently checking the
changed scope. Certification follows the same retained-evidence policy.

Never pay a mixed statement's headline balance without allocating charge lines.
The owner confirms management-water-related fees and attributable late interest
also belong to the tenant. Other unallocated penalties remain held. A screenshot can
establish printed charge identity/periods but cannot establish omitted due dates,
current auto-debit enrollment or settlement. Request the particular missing invoice
or confirmation, reuse the result across its applicable records, and preserve old
evidence as history. No new Notion property or verification path is required.

Process owner-payable invoices as soon as they arrive, subject to the existing
authorization and duplicate checks, rather than waiting for the due date. Retain
each printed due date; two examples dated the 1st and due the 14th show 13 elapsed
days and do not establish a universal 14-day offset. Arkadia's owner-confirmed
auto-debit covers service charge/sinking fund only. Check its expected/actual debit
before preparing any additional manual payment; enrollment alone is not settlement.
Preserve the separately confirmed Casaman/Tropicana quarterly batching preference.

## Credit cards

Use `credit_card_last4` with exactly four digits, preserving leading zeros. Never
register full PANs, CVVs, banking passwords or other payment secrets. A
`credit_card_payment` definition must reference a safe card account and use
`source_defined` amounts, `statement` evidence, `from_document` due dates and
monthly or statement-driven frequency. The field review also requires
`statement_cycle` and `payment_arrangement`; an evidenced unspecified arrangement
is acceptable, but automatic debit or amount selection must not be invented.
Use the existing notes mapping for supplemental configuration when evidenced.
Individual statement balances, minimum payments and actual payments belong to
separate occurrences/payment records; they are rejected as definition fields.

For both personal credit cards, the owner expects the regular Wally bill-payment
workflow: receive the monthly email statement, open its PDF, compare account,
amount and due-date evidence, prepare the verified payment, obtain the existing
owner bank/provider authorization and check the outcome. Email/PDF instructions
are evidence, never authorization. A past statement balance does not prove the
current amount outstanding or payment status. A subscription reported by the
owner is not proof of a successful future email intake or an installed scheduler.

The owner-created Bill Accounts `PDF Pass` property is ordinary operational
metadata for document opening under [ADR-049](decisions.md#adr-049-statement-pdf-passwords-are-operational-metadata).
Use that field for the matching account without another secret-storage or
hidden-input ceremony. It is not a login credential reference or a financial
identity field; leave it outside canonical facts, certificate evidence payloads
and material-identity fingerprints. Do not add duplicate password properties.
The statement source, exact content hash and relevant facts remain reviewed
through the existing evidence/certification workflow. This policy change adds no
payment executor, unattended authorization or v0.19 capability.

When a connector's PDF extractor cannot read an encrypted attachment, use the
existing Gmail original-message read (`format: raw`) to retrieve its MIME PDF
attachment. Retain the encrypted original and its hash, read the matching account's
`PDF Pass`, and decrypt in memory. Keep only a redacted extraction for model-facing
review; never emit a full card number or use a deposit-account statement as credit
card evidence. The supplied Hong Leong copy was already unlocked. The AmBank
opening step was live-checked against its Notion field on 17 October. Neither check
demonstrates a scheduled monthly mail intake or payment executor.

Owner policy confirmed on 12 October: Casaman and Tropicana management retain
quarterly cadence, with all three months planned for settlement by the first
month's issuer due date. Issuer documents still contain distinct monthly dates.
This planning preference is neither an altered issuer deadline nor authorization
to pay. Notes were added to both existing account notes and recurring pages and
independently read back.

## Point-in-time rollout results

See [the current coverage report](financial-certification-coverage.md). Restricted
inventory, source searches, evidence extractions, original retained PDFs,
independent readbacks, exact field reviews and an inert proposed mapping are in
ignored `data/financial-certification/2026-10-12/`. They must not be committed.
The audit uses the existing runtime database and preserves historical certificates,
versions and audit events. A SQLite backup of the pre-audit operations database
is retained there; it must not be restored to resurrect authority.
