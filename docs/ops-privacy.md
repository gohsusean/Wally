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

A decision stores the proposal id, the decision, timestamps, the channel (`cli` / `repl`; `user_cli` / `user_repl` on v0.14 rows), the principal, an optional correlation id, an optional short note, and the proposal fingerprint that was reviewed. The audit event carries the same fields. It does not store the email body, the browser DOM, a secret, or an authentication grant.

## Request provenance (v0.15)

Proposals and executions may carry `RequestProvenance`: channel, principal, optional external session/request refs, and a correlation id. This is audit metadata so a later interface can trace request → proposal → approval → execution → verification. It is not part of the proposal fingerprint and it does not authorize anything. Conversation transcripts are not stored on the proposal.

## Gateway records (v0.16)

A gateway request stores its id, correlation id, optional active-matter id, the channel and principal the runtime assigned, optional external session and request refs, and up to eight evidence items. Each item stores a kind, length-capped text, and content hash, and is marked untrusted. The audit event stores the count and the hashes, not the text.

An ActiveMatter stores an id, an optional canonical Matter id, a short title, and `active` or `archived`. That flag means whether Wally should keep offering the continuity handle. It is not a copy of Matter status. Correlation links and session refs `(channel, external_session_ref)` with first-seen and last-seen timestamps are separate rows. A new session does not delete an older one.

Gateway responses and audit events do not include the HMAC grant, the adapter credential, or evidence text. `get_context` returns Matter status, proposal and execution ids and statuses, and the handle. It does not return the evidence capsule.

## ChatGPT connection (v0.17)

The owner secret, the Gateway credential, and the bearer token are process memory. They are not written to `operations.db` or the audit log. An OpenAI subject, when the host supplies one, is stored as audit metadata on a Gateway call. It is not part of a proposal fingerprint and it is not a credential. Decision notes stay the short note the decide path already stores.

## Telegram outbox (v0.18)

`notification_outbox` stores the dedupe key, kind, Matter and correlation ids when known, proposal or execution id, fingerprint, callback nonce, allowed actions, owner user id, chat id, status, attempt count, lease and retry timestamps, a short last error, the Telegram message id after a known delivery, and created, delivered, and dismissed timestamps. It does not store the bot token, the Gateway credential, or a grant.

`telegram_updates` stores the update id and an outcome. `telegram_cursor` stores the next offset and the last private chat id. `telegram_lease` stores the poller holder and expiry. Message text stays in the existing bounded evidence capsule. Audit events store ids and hashes.

## Execution records (v0.15)

An execution record stores these fields and nothing else:

- execution id and proposal id
- the approved proposal fingerprint, the Matter id, and the intent
- status, origin/channel, principal, correlation id, and timestamps
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

Email and calendar content is always `trusted=false` with `external_communications` authority. Instruction-like text is recorded as data and may be flagged; it cannot grant approval, trigger sends/payments, or resolve secrets. A proposal decision is recorded only after `PrincipalAuthority` authorizes `DECIDE_PROPOSAL`. Approval does not resolve a secret. Execution starts only after `EXECUTE_PROPOSAL` is authorized on a request context the authority issued, and secrets are resolved only after the execution-time prompt is granted.
