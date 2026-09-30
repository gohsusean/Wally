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

## Approval records (v0.14)

A decision stores the proposal id, the decision, timestamps, a trusted origin (`user_cli` or `user_repl`), an optional short note, and the proposal fingerprint that was reviewed. The audit event for a decision carries the same fields. It does not store the email body, the browser DOM, or a secret.

## Execution records (v0.15)

An execution record stores these fields and nothing else:

- execution id and proposal id
- the approved proposal fingerprint, the Matter id, and the intent
- status, origin, and timestamps
- the executor name and a SHA-256 digest of the trusted plan
- preflight, authorization, and verification results
- a failure category and short Wally-authored outcome text
- minimal evidence flags (login status, verify status, authenticated true or false)

It does not store:

- secret values or resolved credentials
- page text, the DOM, screenshots, or traces
- the portal URL
- email or Notion content

The `secrets_resolve` audit event records the `op://` reference and purpose, never the value. Canary-secret tests assert that no value appears in the database, audit log, CLI output, or recorded browser actions.

## Not stored

- Full email bodies (Observe uses search snippets only; it does not call `get_email`)
- Attachments
- Secret values or 1Password payloads
- Browser DOM, screenshots, or traces
- Token-level model traces / chain-of-thought
- Arbitrary raw Notion page dumps (only title + short content excerpt when an obligation fingerprint is created from metadata)

## Trust

Email and calendar content is always `trusted=false` with `external_communications` authority. Instruction-like text is recorded as data and may be flagged; it cannot grant approval, trigger sends/payments, or resolve secrets. A proposal decision is recorded only from an explicit CLI or REPL command. Approval does not resolve a secret. Execution starts only from `wally execute` or `/execute`, and secrets are resolved only after the execution-time prompt is granted.
