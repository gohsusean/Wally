# Certified financial catalog (D03)

Implemented 6 October 2026 under [ADR-044](decisions.md#adr-044-certified-financial-identities-and-local-owner-certification).
Code/fixture validation is complete separately from live readiness. No existing
Notion row is certified or enabled by this milestone. The next operator exercise
is one utility account for one property, separately authorized by the owner.

## Model and authority

The enduring hierarchy is provider + subject → account → definition → instance.
Properties, personal/legal entities, providers, accounts, definitions and instances
have immutable Wally `fin_…` IDs. Notion database, data-source and page IDs are
source locators. Titles, copied Wally IDs, `Verified`, SOP prose, Payment Method
`Runtime Action`, Tasks and Payment History create no authority.

Account uniqueness uses provider, subject, identifier namespace and the complete
validated customer ID. Customer IDs are strings, preserving leading zeros.
Definition uniqueness uses account + charge-stream key. Instance uniqueness uses
definition + owner-validated occurrence discriminator. Amount/date edits revise
facts without allocating another occurrence. A correction with a different invoice
reference must explicitly replace the old reference. Historical reference aliases
prevent another occurrence from claiming an earlier reference. A duplicate source
locator cannot allocate another ID; duplicate semantic identities block certification.
Concurrent source identities are never merged automatically. Removing an observed
duplicate preserves its history and cannot restore the affected certificates.

Definitions support monthly, quarterly, semiannual, annual, irregular,
statement-driven and one-off streams in the existing Recurring Bills catalog.
Calendar patterns require an anchor, explicit due-day/months, timezone and
month-end handling when needed. They do not schedule or generate occurrences.
Source-defined variable amounts are supplied only in issued instances. Fixed
contract instances must match the certified decimal amount exactly. Supported
currency precision is explicit (MYR/USD/EUR/GBP/SGD: two places; JPY: zero).
Money accepts decimal strings, never floats, locale guesses or currency prefixes.

An `expected` instance has no amount, amount basis or invoice reference. An
`issued` instance requires an explicit amount basis and primary invoice, statement
or contract evidence. Intake does not choose a payment amount. Evidence is a
source locator plus SHA-256 and document kind, manually validated by the owner;
Wally does not extract documents or authenticate their contents. Receipts cannot
be primary obligation evidence and remain independent FYI observations. Even a
claimed owner confirmation cannot bind a receipt or resolve a financial Matter.

## Source contracts

[Reviewed configuration](../config/finance.yaml) starts with no designated sources
or profiles. A privacy restriction masks the existing Bill Accounts database in
general knowledge serialization before certification. Configured sources are also
restricted; full customer IDs are consumed only by the typed finance path. The
restricted SQLite catalog contains identifiers, evidence references and secret
locators and must receive the same local access protection as operational state.
Never put secrets, tokens or payment/card credentials into intake files or Notion.

Each source declares exact database ID, exact data-source ID, known record kind,
`finance` role and canonical-field → stable Notion property-ID/type mappings.
Relation mappings also pin the target data-source ID. Human property names are
used only in the additive schema preview, never to read material facts. Renames
preserve mappings; deleting/recreating a property blocks them. Typed reads use
Notion version `2025-09-03`, verify the database/data-source parent, fully paginate
queries, hydrate truncated relation properties and reject missing/inaccessible,
multi-target or incorrectly scoped dependencies. No global title search supplies
financial candidates. Complete enumeration precedes the trusted projection.

A source must pass approved operational classification, explicit local designation,
known kind, valid typed mapping, current record certification, certified dependencies
and explicit definition-chain enablement. Governance/pending/general sources fail
closed. Designation changes the registry role to `finance` but certifies no rows.
Unreadable/partial sources invalidate affected certificates and dependency chains.
Record-local failures are isolated using the identity proof below. Recovery and
reverting edits require fresh certification; neither restores old authority.

### Incremental certification in a partially dirty database

Every designated source is enumerated completely. Every row participates in the
identity/ambiguity proof, including rows that cannot become typed candidates.
No enrollment checkbox, title filter or owner-declared quarantine removes a row
from that proof. Wally separates three failure levels:

- **Source/schema failure:** missing/recreated properties, incompatible mapping
  types, wrong relation schema targets, invalid classification/designation,
  ambiguous source identity or incomplete query pagination prevent use of that
  source. With no complete identity inventory, its entire record kind may conflict
  across designated sources. Records of that kind and dependent chains fail
  closed; independent kinds can remain usable. An unreadable/ambiguous global
  configuration still blocks the catalog.
- **Record Needs Attention:** missing fields, unsupported values, invalid recurrence
  or unavailable dependencies prevent registration/use of that row. Existing
  versions are retained as history and marked invalid. New malformed rows appear
  in `finance status` by source locator, without a canonical ID or raw source values.
  They never enter the trusted projection.
- **Chain-local ambiguity:** independently readable identity coordinates use the
  same normalization as canonical identities. A malformed row is proven disjoint
  only when at least one known identity coordinate cannot match the candidate.
  Otherwise it blocks that identity namespace and all dependent chains. Unknown
  identifiers or relations are wildcards, never evidence of uniqueness.

For example, an account with malformed responsibility can still expose its exact
provider, subject, namespace and customer identity. It blocks a matching account,
but not a different property/account chain. An unreadable customer identifier
still blocks all matching provider/subject/namespace accounts; a known different
subject can prove disjointness. Definitions use account + charge-stream key.
Properties/entities/providers must themselves pass uniqueness before their
dependent accounts can become ready. A complete multi-target relation remains
invalid, but its full target set can prove disjointness from a third chain.
An incomplete, wrongly scoped or unhydrated relation cannot do so.
Mappings must target the correct record kinds as well as the pinned data-source
IDs. A contradictory account subject kind contributes no subject disjointness
evidence; it cannot evade a potential cross-property/entity collision.

Current diagnostics persist only locators, bounded reasons and hashes of partial
identity coordinates in `finance_read_issues`; audit history records issue changes
and clearance. These hashes are conflict evidence, not certification. Full
identifiers and malformed source values are absent from diagnostics/status.
Trusted candidates still require every D03 gate and explicit owner certification.
No extra enrollment/quarantine authority or Notion schema changes are introduced.

When a new malformed row appears, a provably disjoint enabled chain retains its
exact certificates and enablement. A possible collision permanently invalidates
affected certificates and dependent certificates on refresh. Fixing/removing the
row clears the diagnostic but does not restore authority: explicitly recertify
affected records/dependencies and renew chain enablement. Source failures likewise
require complete reads and fresh certification after recovery. Existing rows and
audit/certification history are preserved; this refinement performs no data cleanup.

Example mapping shape (use real property IDs from schema preview):

```yaml
schema_version: 1
restricted_databases: []
sources:
  - database_id: <existing Bill Accounts database UUID>
    data_source_id: <its exact data-source UUID>
    kind: account
    role: finance
    fields:
      provider:
        property_id: <stable Provider relation ID>
        type: relation
        target_data_source: <Bill Providers data-source UUID>
      subject:
        property_id: <stable Property relation ID>
        type: relation
        target_data_source: <Properties data-source UUID>
      identifier:
        property_id: <existing AccountNumber text ID>
        type: rich_text
      namespace:
        property_id: <namespace text ID>
        type: rich_text
      responsibility:
        property_id: <responsibility select ID>
        type: select
    constants:
      subject_kind: property
profiles: []
```

Optional account username/password reference mappings use `rich_text`; values
must be valid `op://` or `keychain://` locators. A personal/entity account may use
an explicitly configured local canonical entity ID as its subject constant.
Other dependency fields must be mapped relations. Configuration changes themselves
are application changes and require review and renewed designation.

## Persistence and certification

Additive `finance_*` tables in the configured operations database store current
objects, immutable versions, certificates, append-only certificate events,
designations, chain enablement, auth-only acceptance, reference aliases and audit
events. Existing operational/registry rows and decision/audit histories are not reset.
Canonical candidates remain draft until reviewed. Material edits append a version
and permanently invalidate prior certificates. Missing/unusable certificates or
changed dependencies produce needs-attention state. Tracking may be certified
while portal access remains unavailable.

`CERTIFY_FINANCIAL_DATA` is granted only through authenticated local-owner CLI/REPL
operations. Services check PrincipalAuthority-issued contexts and additionally
require the local terminal authentication/channel. Telegram, current ChatGPT,
Gateway payloads, generic tools and provenance cannot mint certificates. Fresh
explicit confirmation binds the candidate ID, exact material version/fingerprint,
scope, schema version, dependency certificate IDs, evidence hashes/references and
owner attestations. The service rereads sources/dependencies after the prompt.
Certificates persist certifier provenance, timestamp and revocation history.
A source checkbox or an editable Wally-ID field is never a certificate.

Identity/tracking certificates depend on the certified hierarchy. Portal-review
certificates additionally bind the identity certificate and reviewed profile
fingerprint. Profiles contain version, supported `review_bill` intent, exact HTTPS
origins, selectors and auth-success conditions. Provider URL and account credential
references come from certified typed facts. Profile drift invalidates portal scope;
identity/tracking can remain valid. Technical Notion prose cannot select browser
primitives, selectors, workflows or execution capabilities.

## Operator sequence for the separately authorized pilot

Use the existing environment; do not invoke these commands as a development check.
App startup initializes stores and can discover providers. Certification/schema
application are blocked in dry-run mode. Back up existing state using the normal
operator procedure before a live schema operation; preserve every existing row/ID.

1. Review `config/finance.yaml`. Configure the four existing Properties, Bill
   Providers, Bill Accounts and Recurring Bills database/data-source pairs. Use
   `finance schema <source-key>` to inspect schema and pin reusable property IDs.
   Never create a second one-off catalog. A source key is
   `notion:<database-UUID>:<data-source-UUID>`.
2. `finance schema <source-key> --apply` adds only the typed columns shown in its
   preview, after an exact-schema fresh prompt. It does not rename/delete columns,
   rewrite rows, create databases, populate facts or certify anything. Pin newly
   allocated property IDs in reviewed config afterward. Map existing compatible
   Address/UnitNumber, PortalURL, AccountNumber text, Active and relations where
   appropriate. Ambiguous legacy numeric/payment fields stay reference-only.
3. Ensure sources have owner-approved operational classification. Run
   `finance designate <source-key>` for each completed mapping. Then
   `finance status` enumerates provisional IDs and fingerprints without customer IDs.
4. Owner reviews primary property/provider/account/obligation documents and fills
   the required canonical facts for this one chain. Certify property/entity and
   provider first, then account, then definition using
   `finance certify <fin-ID> --scope identity --proof <restricted-proof.json>`.
   The local review summary masks identifiers, credential locators and portal URLs;
   source locators and normalized facts identify the exact reviewed candidate.
   The proof contains only `evidence` and `attestations`; every attestation must be
   explicitly true: `primary_evidence_reviewed`, `identity_and_scope_confirmed`,
   `no_secret_values`. Each evidence entry has `source`, `reference`, `content_hash`
   and `kind`. No secret values or document contents belong in the proof.
5. `finance enable <definition-ID> --scope identity` freshly confirms the exact
   chain. `finance intake instance --facts <restricted-facts.json>` registers a
   manually validated expected or issued occurrence, still provisional. Certify
   it separately. Use `--revise <instance-ID>` for corrections to that occurrence.
6. Review a bounded profile and valid credential locators; certify the definition
   with `--scope portal_review`. Produce a review proposal with `wally brief`,
   approve that exact proposal, and explicitly run
   `finance accept-portal <proposal-ID>`. This is a separate authenticated execute
   request with a fresh prompt and post-prompt revalidation. It logs in, enforces
   reviewed origins, independently checks auth and stops; no payment occurs.
   A successful exact-version acceptance is required for
   `finance enable <definition-ID> --scope portal_review`. Inconclusive attempts
   block automatic retries. A later claimed outcome cannot manufacture acceptance.

CLI syntax is `wally finance …`; REPL syntax is `/finance …`. No generic model tool
implements these commands. `finance revoke <fin-ID> --scope …` records owner
revocation. A new certificate changes dependent bindings and requires dependent
recertification and renewed chain enablement; approvals never transfer automatically.

## Operational integration and limits

Certified instances project into Observations and Matters with canonical occurrence
IDs and certificate/version bindings. Revisions are observable within the same
period and retain the Matter ID. Unready chains block open financial Matters;
recertification produces a fresh observation and proposal version. Proposal records
remain advice and references, with no executable financial payload. Portal review
rereads current candidates, certificates, dependencies and profile after the human
prompt and before credentials resolve. Generic `KnowledgeAsset.metadata` supplies
neither canonical obligations nor portal plans. Old stored Matters/approvals remain
history but cannot supply a certified execution target.

D01/D02 legacy dispatch verification and fresh authorization remain intact. D03 IDs
are explicitly refused by legacy payment preparation. No D03 definition, expected
occurrence or issued instance enables payment. Payment instruments/beneficiaries,
amount selection, receipt binding/settlement, automated extraction, recurrence
scheduling, n8n deployment, Payment History synchronization, historical closure
repair and unrelated calendar D07 work remain outside this milestone.

Notion reads are snapshots, not remote transactions. Changes observed before or
during confirmation invalidate readiness; polling cannot detect an edit reverted
between reads. Live portal compatibility, primary-document truth and first-chain
acceptance remain owner/operator work. Source failures deliberately block affected
identity proofs and chains until complete reads and explicit recertification restore readiness.

The typed adapter follows the official [data-source query API](https://developers.notion.com/reference/query-a-data-source),
[relation property pagination](https://developers.notion.com/reference/retrieve-a-page-property)
and [additive data-source update API](https://developers.notion.com/reference/update-a-data-source).

Blank legacy rows with no readable identity coordinates can still block an entire
record kind: Wally cannot prove they are unrelated. Prepare enough identity fields
to establish disjointness, or resolve the ambiguity through separately authorized
owner data work. Complete queries and source/schema contracts remain prerequisites;
preparing one chosen chain cannot bypass either. Malformed unrelated non-identity
fields no longer require database-wide cleanup before incremental certification.
Recording/simulation adapters cannot establish portal acceptance or execute D03
portal reviews; live browser evidence is a runtime provider capability. Browser
origin routes are installed before navigation, service workers are blocked and
WebSockets are disabled on the auth-only path. Certificate/config restoration
cannot restore authority after an observed contract failure.
