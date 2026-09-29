# Observe & Brief — data minimization

Phase 1 persistence lives in `data/operations.db` (SQLite). The audit directory is unchanged (`data/audit/`).

## Stored

- Source and message/event IDs
- Thread IDs (Gmail) and event IDs (Calendar)
- Timestamps (`observed_at`, source timestamps, due/expected)
- Short sanitized titles and snippets (whitespace-normalized, length-capped; receipts shorter than general email)
- Calendar descriptions with known Google auto-event boilerplate and calendar app URLs removed
- Fingerprints / checkpoints for incremental ingest
- Matter summaries, status, explainable priority reasons
- Knowledge asset IDs when an obligation is tied to a designated asset
- Provenance flags (`trusted`, `authority`, `injection_suspected`)

## Not stored

- Full email bodies (Observe uses search snippets only; it does not call `get_email`)
- Attachments
- Secret values or 1Password payloads
- Browser DOM, screenshots, or traces
- Token-level model traces / chain-of-thought
- Arbitrary raw Notion page dumps (only title + short content excerpt when an obligation fingerprint is created from metadata)

## Trust

Email and calendar content is always `trusted=false` with `external_communications` authority. Instruction-like text is recorded as data and may be flagged; it cannot grant approval, trigger sends/payments, or resolve secrets.
