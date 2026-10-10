# External Notion reconciliation

Updated 11 October 2026. [ADR-047](decisions.md#adr-047-human-first-presentation-and-external-notion-reconciliation)
adopts reconciliation instead of requiring exclusive Wally write access. The owner
and other authorized integrations can edit Notion directly. Wally cannot prevent
those edits using a local SQLite claim or a database lock in Notion.

## Implemented foundation

The scoped edit service offers `reconcile_notion_record(target_key)` through the
existing authenticated Gateway and local Codex adapter. It requires read capability,
accepts only reviewed registered targets, makes no Notion write, and records local
trust/audit changes. Hosted ChatGPT composition and disabled decision policy remain
unchanged. Telegram acquires no execute, verify or certification capability.

Reconciliation also runs on staging, explicit proposal reads, successful-attempt
inspection, and existing execution-time semantic preconditions. Polling renders
stored reviews without provider reads; it only delivers already-recorded findings.
The immutable review, latest approval, Matter, target and source are still rechecked
at the existing authorization/credential/dispatch boundaries.

Additive `notion_trusted_snapshots` and `notion_reconciliation_events` tables retain
bounded enum values, protected business hashes, prior financial version references,
changed-property identities, timestamp and authority-free provenance. The first
ordinary source read is **observed**, not verified. Financial enum metadata can be
compared with existing canonical catalog facts even on the first read. Existing
verified attempts supply a baseline on additive rollout without changing history.
Successful independent verification publishes the new baseline atomically with the
attempt outcome and page-claim release. No external edit automatically replaces it.

Comparisons ignore page timestamps/actors and the explicitly registered Audit History
field. A last-edited timestamp alone is not evidence of business change. Protected
business hashes and schema/policy/catalog contracts are compared instead. Complete
scope must be available; malformed, truncated or unsupported state fails closed.
An unavailable explicit read marks prior verification stale and returns a plain error.

The response distinguishes prior observation from verified state, lists full old/new
values for registered enum fields, counts other protected changes and reports whether
verification is current and mapped certification is affected. Unrestricted property
contents are not newly copied into messages, state or audit. For those changes Wally
asks the owner to review the source, and the complete typed finance catalog supplies
its existing restricted domain evidence. Writer attribution is **unknown** for a
changed state; an unchanged independently verified Wally baseline has known execution
lineage. A matching result alone cannot prove who wrote it or whether a writer was
malicious. Stored successful attempts remain historical evidence, never rewritten
as failed because Notion subsequently changed.

## Certification and consequential actions

Mapped financial property drift invalidates the affected record's certification
through the existing catalog store; certificate evidence and audit events are retained.
Unrelated protected-property changes stale the whole scoped source verification but
do not revoke unrelated financial certification. Canonical values remain distinct
from source values. Reconciliation never registers an invoice, overwrites Notion,
issues a certificate or reenables a financial chain.

The existing `FinanceService.refresh` remains the complete financial reconciliation
mechanism for all configured mapped properties, dependencies, malformed inventory
and uniqueness. `review_plan`/`tracked_instances`/financial status refresh source
facts; action preflight and post-confirmation planning require the current certified
chain. Material revisions retain old versions and revoke affected authority; metadata
revision timestamps are excluded from the material fingerprint. Fresh local-owner
certification is a separate existing workflow, with fresh evidence and exact-version
binding. A newly certified catalog version matching current scoped enum values is
not revoked again merely because an older scoped verification still differs.
Other mapped protected changes remain conservative until separately reconciled.

Reverting source values alone does not silently restore verification. An explicit
`verify_notion_edit` can freshly check the original approved properties and every
protected property, schema and catalog version; only an exact match republishes the
current source verification. It preserves the historical attempt, audits the fresh
read, refuses unresolved page claims and never restores financial certification.
Changed approved/business values need the appropriate fresh review/domain evidence,
not execution of the already-applied external change. RUNNING and uncertain attempts
retain their existing manual/read-only recovery and no-retry rules.

## Owner experience

A relevant persisted change gets one deduplicated ordinary Telegram notice:

```text
📝 Notion change detected

TNB — Electricity

Frequency
Monthly → Quarterly

This differs from Wally’s last verified record.
Review the change before relying on this information.
⚠️ This change will require recertification.

[Review in Notion] [Later]
```

The warning appears only when mapped certification is affected. The notice describes
an existing change: there is no Apply/Approve & Execute button. Review opens Notion;
Later dismisses the notice without scheduling a reminder or changing trust. Technical
investigation can use the structured reconciliation response and immutable audit.
Certification remains a separate local-owner operation. An independently reverified
baseline stops new delivery of resolved findings while keeping all historical events.

## Limits and rollout

There is no new scheduler or continuous Notion watch. A change is discovered only
when the relevant read/refresh/pre-action path runs; the poller does not discover
source changes on a timer. The scoped mechanism covers registered edit targets and
complete supported snapshots. The existing finance reader covers configured,
designated financial sources; unconfigured data and unobserved intervening edits
cannot be detected. Notifications for scoped drift are emitted from its persisted
findings; broader finance diagnostics continue through the existing catalog UX.

Notion PATCH has no demonstrated atomic conditional write. External edits between
Wally's last GET and PATCH can still race, including same-property overwrites that
post-write verification cannot reconstruct. Wally detects observed drift, refuses
stale proposals and flags mismatched/uncertain outcomes; it does not promise to fence
external writers. No automatic rollback, overwrite or retry repairs such a race.
The owner accepts this limitation for a reviewed scope under ADR-047; production
financial rollout remains disabled and needs separate authorization/recovery acceptance.

Tests use isolated databases and synthetic transports only. This implementation does
not edit the four Phase 6 records, register the September invoice, change integrations,
restart the poller or enable production writes. Installed processes/MCP connections
need operator-controlled code reload before the new UI/tool is active. Test success
is not a second live owner acceptance.
