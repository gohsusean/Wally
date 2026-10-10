# Human-first communication

Wally is rigorous internally and clear externally. This standard applies to Telegram,
ChatGPT, Codex, briefs, reports, reminders, results and recovery messages. Everyday
messages should support comprehension and decisions. Development, debugging and
audit investigations can disclose the technical detail needed for that work.

## Shared conventions

- Lead with the outcome or the decision needed. Use concise, natural language.
- Use meaningful record and property names. Keep internal IDs, hashes, authorization
  vocabulary and provider exception text in runtime records, not ordinary messages.
- Disclose every material change and consequence. Progressive disclosure is for
  technical detail, never for the scope the owner is being asked to authorize.
- Distinguish approved and awaiting execution, in progress, verified completion,
  known failure and uncertain outcome. A triggered workflow or successful login is
  insufficient evidence of completion.
- Show risks in ordinary language. Financial edits show “This change will require
  recertification.” Avoid repetitive policy disclaimers on ordinary metadata cards.
- Use **Asia/Kuala_Lumpur (MYT)** for human-facing dates and times. Prefer
  `11 Oct 2026, 1:22 am MYT`; use today/tomorrow only against an explicit local day.
  Omit seconds, fractional seconds, offsets and ISO strings unless needed for the
  decision or requested for technical work.
- Match detail to the audience and context. Retain exact IDs and precise timestamps
  in structured technical responses, immutable specifications and audit evidence.

[Shared formatting](../src/wally/presentation.py) supplies enum/property labels,
complete change lines and aware-instant conversion to MYT. It changes presentation
only: authorization deadlines still use the existing UTC/epoch clocks and durations.
Unknown labels retain their full value; neither values nor names are truncated.
Naive times are rejected instead of assuming an ambiguous timezone.

This milestone applies the standard to scoped Telegram Notion reviews and results
first. Legacy briefs and other channels migrate incrementally; it is not a claim
that every historical message or technical JSON timestamp has been rewritten.

## Representative approval before and after

Before (the internal identifiers below are placeholders for the real fields):

```text
NOTION CHANGE PROPOSAL
Each button covers every change shown in this card.

Record: Sandbox — Frequency acceptance
Page: <page UUID>
Record link: https://www.notion.so/<page UUID>
Proposal: <proposal identifier>
Version: <version hash>
Property: frequency (<property identifier>)
Current value: monthly
Proposed value: quarterly

Approve & Execute authorizes this exact edit and independent verification.
Never pays or certifies. Financial certification is invalidated if applicable.
Use individual cards to approve selected records.
Review expires: 2026-10-10T17:22:45.123456+00:00
```

After (the actual new renderer's layout with that record and change):

```text
📝 Notion update

Sandbox — Frequency acceptance

Frequency
Monthly → Quarterly
Expires today at 1:22 am MYT

[✅ Apply change] [❌ Reject]
[⏰ Later]        [View in Notion]
```

A financial target also shows `⚠️ This change will require recertification.`
Batch cards say `📝 Notion updates — apply all shown`, list every record and change,
and use `✅ Apply all`. Record links get separate rows. An oversized batch falls
back to individual complete cards; an oversized single card is withheld and audited.
No shortened scope becomes actionable. Later retains the existing one-hour defer.
“Apply change” still authorizes the same separate internal decision/execution stages
under [ADR-046](decisions.md#adr-046-telegram-authorizes-eligible-scoped-notion-edits).

## Completion and recovery

```text
✅ Notion updated

Sandbox — Frequency acceptance

Frequency
Monthly → Quarterly

Verified successfully.
```

Rejection names the affected records and says `❌ Change rejected` / `No changes made.`
Deferral says the review was postponed for one hour and no changes were made.
An approved, unstarted change says `⏳ Change approved` / `Awaiting execution`.
Known pre-dispatch failures say no change was sent and request a new review.
Uncertain outcomes say `⚠️ Change outcome uncertain` and direct the owner to check
Notion and ask Wally to verify, without applying again. Raw errors stay out of messages.
A successful result describes the independent check at that time; a later external
edit can stale it. A new verification does not certify financial correctness.

See [reconciliation](notion-reconciliation.md) for source changes and current trust.
