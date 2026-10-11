# First financial reconciliation: Casaman DBKL assessment

Prepared 11 October 2026. **Pending primary-document validation and explicit
production rollout approval. No production change or Telegram review was sent.**

## Selection and evidence

Casaman's DBKL assessment is the strongest provisional candidate checked. The
July bill and September notice match its assessment account and Unit 20 Casaman.
The owner explicitly confirmed financial responsibility in this chat. It avoids
insurance scope, management aggregation and tenant responsibility questions.
IWK Casaman has current evidence but unresolved tenant administration; IWK
Tropicana has no matching current bill in the inspected results; the water search
returned payment messages rather than a current bill.

Read the original 8 October assessment and the
[11 October readiness report](telegram-phase6-readiness.md), queried current Notion
sources, fetched candidate pages, then directly read the selected account, definition
and complete schemas using Wally's Notion reader. All nine typed fields below are
actually missing. No financial catalog refresh or source designation ran.

Read-only Gmail evidence:

- DBKL second-half 2026 assessment email, 26 July; attached bill dated 1 July.
  Extracted document identifies the matching account and property; the email
  supplies MYR and the document due date, 31 August 2026.
- DBKL Form E notice email, 25 September. Extracted notice is dated 1 September,
  while the email summary says 31 August; retain that discrepancy. The text
  explicitly describes twice-yearly assessment and the July–December period.
  Its balance is calculated through 31 August, not proof of today's unpaid balance.
- Owner confirmation on 11 October supports `owner`; recipient name and the
  legacy `Sean` label alone were not treated as liability evidence.

**Evidence limitation:** the connector marked both PDF extractions incomplete.
The original PDFs and complete extraction files could not be retrieved, and the
existing Wally Gmail attachment path was unavailable. No full visual/document
verification is claimed. Extracted text and provenance are retained privately;
validate originals before enabling production. Selection and corrections remain
provisional. No issued amount or invoice is registered from these documents.

## Exact proposed typed changes

Bill Account: **Assessment (Casaman)**

- Identifier Namespace: Missing → **Assessment account** (`assessment_account`).
  Matching issuer assessment-account reference and earlier assessment convention.
- Responsibility: Missing → **Owner** (`owner`). Direct owner confirmation.

Recurring Bill: **20 Casaman - DBKL Assessment**

- Charge Key: Missing → **Assessment tax** (`assessment_tax`). Issuer assessment
  bill and the earlier proposed taxonomy convention.
- Obligation Type: Missing → **Property tax** (`property_tax`). Same evidence and
  convention; these two text labels remain reviewed taxonomy choices.
- Currency: Missing → **MYR**. Issuer email/document currency.
- Amount Policy: Missing → **From source** (`source_defined`). Each issued levy
  supplies its amount; Typical Amount is not a validated contractual amount.
- Frequency: Missing → **Every six months** (`semiannual`). Issuer notice explicitly
  describes two assessment periods and two annual due dates.
- Evidence Mode: Missing → **Issued bill** (`issued_bill`). Issued assessment bill.
- Due Mode: Missing → **From document** (`from_document`). Individual document date;
  no fixed calendar pattern is introduced.

The existing legacy Frequency is **Quarterly**, conflicting with the issuer's
twice-yearly instruction. That property is outside the mapped typed edit contract
and remains unchanged in this nine-field scope. It is an explicitly outstanding
correction, not silently treated as reconciled. Legacy amounts, payment/status/due
fields, property address and relations also remain unchanged. This initialization
does not establish complete catalog/certification readiness.

Neither selected row has a canonical financial object or certificate. Initialization
creates neither. Complete catalog refresh, uniqueness/dependency review and later
certification are separate owner workflows. Material canonical edits still invalidate
certification; an edit or readback never silently recertifies.

## Narrow implementation and staging

[Finite contracts](../src/wally/ops/notion_contracts.py) add only the seven fields
needed alongside existing Frequency and Amount Policy. New selects allow only owner,
MYR, issued_bill and from_document; the three taxonomy texts allow only the exact
values above. Existing Frequency/Amount Policy replacement rules remain. No arbitrary
text, schema, identifier, amount, relation or payment writer is introduced.

Every property must explicitly opt into `allow_missing: true`. Missing selects and
genuinely empty text lists become explicit null in the immutable specification.
Zero, false, empty strings and unsupported/unknown values are not defaults. Exact
before/null/after values, protected business hashes, schema, page/source, target policy,
evidence digest and proposal version remain bound to fresh human authorization.

For incomplete financial rows, `finance_issue_digest` pins the exact persisted
diagnostic and locator. Operational classification, approved financial role,
designated stable mapping and reviewed evidence remain required. Source-wide failure,
changed diagnostic/mapping, or a canonical object appearing at that locator refuses
initialization. Existing canonical objects use their normal identity path. This
cannot bypass financial uniqueness, certification or dependency gates.

Two exact proposals and a consolidated Telegram-format preview are retained under
ignored `data/phase6-first-reconciliation/`, using consistent private database copies
and fresh provider reads. Both staged gates are false. The evidence digest pins the
extracts and owner confirmation; their limitation is visible in the review. Changing
evidence requires a fresh exact proposal. These are inspectable previews, not live
approvals or production authority. Production policy remains unchanged with no targets.
Installed processes have not been reloaded during this task.

The existing selected/batch decision machinery can review both records. Execution
remains property-specific and independently verified; two writes are not a provider-
atomic transaction. Future cards must be generated freshly, without inherited
approval, expired proposal versions or copied nonces.

## Production gate and owner decisions

Before any production enablement:

1. Obtain readable original DBKL documents and validate matched identity, currency
   and cadence. No invoice registration is necessary.
2. Accept or amend the nine exact facts, including the taxonomy. Responsibility
   is already answered; it does not need to be asked again.
3. Explicitly accept concurrency risk for these two rows/properties. Wally rereads
   after confirmation, after credentials immediately before PATCH, and independently
   afterward. Observed drift refuses stale execution. The remaining Notion GET/PATCH
   seam permits an intervening writer to be overwritten without observable evidence.
   This is **not atomic CAS**. Avoid simultaneous edits during the supervised
   interaction; that reduces risk without guaranteeing zero lost updates. If zero
   risk is required, keep disabled until conditional writes or enforceable exclusive
   access is available.
4. Separately authorize temporary production registration for the two page IDs,
   nine property IDs/values below, explicit null initialization, fresh diagnostic/
   evidence/source bindings and the owner's private Telegram subset. Keep local
   writes false. Load reviewed code in the existing MCP/poller under supervised
   rollout, issue complete expiring cards and require the owner's Apply change/
   Apply all press. Chat approval of rollout does not execute edits.
5. Independently verify every approved value and protected property. On ambiguity,
   retain the claim, inspect and explicitly verify; never repeat PATCH or substitute
   a new proposal. Restore disabled production policy after the pilot.

Current blockers: primary-original validation, scope/race acceptance, temporary
registration and loading reviewed code. Legacy Quarterly correction and later
identity certification remain outside this typed initialization scope. Financial
writes remain disabled; no continuous monitor or financial action is introduced.

## Technical scope for inspection

Account page: `38b4d585-1c89-8157-bd44-c8c7ef9e8596`.
Account source: `3884d585-1c89-806f-8820-000babad80b8`.
Property IDs: namespace `YTUf`; responsibility `~[R|`.

Definition page: `38b4d585-1c89-810a-9bd0-c47de591a67f`.
Definition source: `3884d585-1c89-8029-9974-000bb244b0e0`.
Property IDs: charge key `iitb`; obligation type `IhF~`; currency `>ZKb`;
amount policy `a@\U`; frequency `WQFT`; evidence mode `[fpW`; due mode `XCBy`.

Private provenance: Gmail messages `19f9ae204495f31d` and `1a0d923e0d8caf45`,
filenames `2612621354093.pdf` and `N612620056371.pdf`. Customer identifiers,
raw extracts and live snapshots remain outside Git.

The four certified Mansions/TNB records, account ending 2003, recurring electricity,
staged September TNB invoice, all production financial tables, classifications,
services, credentials and five pre-existing finance changes were preserved.

## Validation and delivery

793 audit-safe tests passed on the final code; three tests were excluded because
two rewrite the real repository `.env` and one writes/deletes a login-Keychain item.
Local socket tests ran with explicit socket permission after checking that the
repository test socket was absent. The focused coverage includes null/value contracts,
all selected selects/texts, stale source/evidence/mapping, incomplete-to-canonical
transition refusal, expiry/replay/rejection, independent verification, certification
invalidation, no-retry uncertainty/recovery, HTML escaping and historical plain results.
Existing target fingerprints remain compatible when new options are absent.

Ruff, local documentation links/fences and `git diff --check` passed. Preservation
checks compared all eleven production financial tables and the five pre-existing
finance files plus the original disabled policy; all matched the baseline. Synthetic
tests are not live initialization or visual Telegram acceptance. Only this task's
tracked changes are committed; private evidence/review state remains ignored.
