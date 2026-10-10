# Runtime and operations

**Status:** Current mechanisms and unresolved deployment/recovery requirements.
Updated 6 October 2026 from the read-only handover audit. Do not treat a dated
observation as current service health. See [current architecture](current-architecture.md)
for request paths, [secrets](secrets.md) for credential policy, and
[engineering debt](engineering-debt.md) for acceptance conditions.

## Repository, machine configuration, persisted state

The repository supplies source, tests, Python dependency/lock definitions,
non-secret YAML profiles, application prompts, and CI. It does not reproduce
credentials, approved registry decisions, conversation history, operational
state, an installed LaunchAgent, Playwright browser binaries, or remote n8n
workflow definitions merely by being cloned.

`load_settings` chooses `--config`, otherwise `WALLY_CONFIG`, otherwise `macbook`.
It loads repository `.env` without overriding existing shell variables. YAML paths
are resolved relative to the project root. Telegram YAML pointers/owner ID take
precedence over their environment fallbacks. Inspect the selected profile rather
than assuming that `macmini` is a validated production deployment.

Ignored local state includes:

- `data/sessions.db`: full conversation messages, FTS5 indexes, summaries.
- `data/knowledge_registry.db`: discovered database mappings and approved
  classifications/roles. This is not regenerated safely from Git alone.
- `data/operations.db`: Observations, Matters, checkpoints, proposal versions and
  decisions, execution attempts, Gateway requests, ActiveMatter/session/correlation
  links, Telegram updates/cursor/lease, notification outbox and additive certified
  finance objects/versions/certificates/audit/enablement/acceptance tables.
- `data/audit/*.jsonl`: append-only daily UTC audit events.
- `data/logs/telegram-poll.{stdout,stderr}.log`: LaunchAgent output/error streams.
- `.env`: provider credentials and local integration endpoint configuration.
- `.venv/` and test/lint/bytecode caches: environment/cache state, not durable
  business records. Interpreter identity still matters to Keychain authorization.

Do not delete/reset/replace any of these as cleanup. Database constructors perform
additive schema setup; running the CLI is not equivalent to a read-only inspection.
App bootstrap may initialize stores and discover providers even for a status
command. Use direct read-only mechanisms for audits, with SQLite `mode=ro` and
`query_only` when inspecting databases; never print message/evidence bodies by default.

## Machine and integration prerequisites

- Python >=3.12 and the existing project virtualenv; uv is the documented setup
  tool, but `uv run`/sync/install may change that environment. Use the existing
  `.venv/bin/python` for constrained checks.
- OpenAI API key for reasoning and Notion integration key for knowledge.
- Google desktop OAuth client credentials/refresh token for Gmail and Calendar;
  the setup script is an operator action, not a health check.
- `N8N_WEBHOOK_BASE_URL` and separately deployed workflows matching the registry.
  Adapter configuration/HTTP acceptance does not prove a transfer or backup ran.
- Playwright Python extra and compatible browser binaries for live browser work.
  Package health is not proof that browser launch/portal selectors work.
- 1Password CLI/app authorization for `op://` references and credential copying;
  macOS Security.framework and unlocked login Keychain for unattended Telegram.
- User GUI login session/launchd and network access to Telegram for polling.
- Approved database classifications, correct roles and structured metadata for
  operational Knowledge features. D03 uses a separate typed certified catalog;
  general metadata grants no financial authority. Live designation/certification
  and auth-only acceptance remain operator work; see [finance operations](finance-catalog.md).

The 5 October audit found the Telegram process running, source version v0.18.0,
and stale installed distribution metadata v0.13.0. It did not authenticate remote
providers, inspect n8n internals, validate Mac Mini deployment, or reinstall
anything. No direct host processes for other Wally servers/n8n/HA were found;
remote services/containers were not ruled out.

## Telegram LaunchAgent

Implementation: [launchd helper](../src/wally/telegram/launchd.py),
[poll loop](../src/wally/telegram/poll.py),
[ingress](../src/wally/telegram/ingress.py), [outbox](../src/wally/telegram/outbox.py).

The per-user label is `com.wally.telegram-poll`, with plist under
`~/Library/LaunchAgents/`. It invokes the repository `.venv/bin/python -m wally
telegram poll`, sets the repo working directory, and supplies only `HOME` and a
controlled `PATH`. It does not source shell profiles or carry a bot token,
1Password session, or Gateway grant. Moving the checkout/interpreter requires
explicit operator reinstallation; Git checkout alone does not update the plist.

RunAtLoad starts it after login; KeepAlive restarts failed/crashed runs with a
30-second throttle. It is not a boot daemon and cannot operate while the Mac is
off/asleep. A locked Keychain/configuration failure can make it exit and retry.
Transient network timeouts/429/5xx are retried in-process. Running status alone is
not proof of current Bot API connectivity; stdout includes sanitized network errors.

The poller resolves its token at startup and builds a restricted in-process
Gateway. It reads stored attention and proposals; it does not refresh Gmail,
Calendar or Notion on a timer. CLI observation can produce stored proposals that
the poller later delivers. There is no general scheduler.

Processed updates and offset are durable. A 90-second database lease prevents
normal duplicate consumers; it is local to this shared database, not a
cross-machine bot lock. Restart can wait for the previous lease to expire. The
record/advance crash seam and slow-processing lease window remain D05/D11.

Decision notifications deduplicate by proposal/fingerprint. Sends are at-least-once:
Telegram may accept a message before Wally records its ID, so a crash can produce
a duplicate card. Known delivery is retained; stale sending leases recover,
failures retry and eventually become terminal. Repeated decision buttons remain
idempotent. `Not now` dismisses notification only; no reminder is scheduled.

### Read-only status inspection

From the existing checkout/virtualenv, this helper inspects the plist and filters
launchctl status without bootstrapping App/providers/databases:

```sh
.venv/bin/python -B -c 'from wally.telegram.launchd import status_agent; status_agent()'
```

Review log metadata/sanitized summaries without exposing private content. Do not
resolve credentials or call Telegram simply to establish process status.

### Explicit operator actions, not verification commands

These change machine/runtime state and must be run only for a requested operation:

```sh
.venv/bin/python -m wally telegram install
.venv/bin/python -m wally telegram start
.venv/bin/python -m wally telegram stop
.venv/bin/python -m wally telegram restart
.venv/bin/python -m wally telegram uninstall
```

Install writes/bootstrap/enables the plist and starts the agent; start/restart may
install if needed. Stop unloads/disables it; uninstall also removes the plist.
Uninstall retains the Keychain token. These commands also use normal App bootstrap.

## Secrets and configuration boundaries

Provider API keys/OAuth credentials come from environment/ignored `.env`.
SecretsProvider dispatches `op://` references to 1Password and `keychain://service/account`
to login Keychain. References/owner IDs are configuration, not secret values.
There is no model-facing resolve-secret tool. Action credentials resolve after
execution authorization; bot startup authentication is a separate purpose.

The current Telegram runtime pointer is `keychain://com.wally.telegram/bot-token`;
its source pointer remains in `telegram.bot_token_source_ref`. The operator's
credential-install command copies the source value into Keychain and must run
with the repo's runtime Python. Check resolves and audits without printing the
value; it is **not** a strictly read-only audit command. Remove deletes the copy
but retains the 1Password source. All are explicit credential operations:

```sh
.venv/bin/python -m wally telegram credential install
.venv/bin/python -m wally telegram credential check
.venv/bin/python -m wally telegram credential remove
```

Keychain trusts the interpreter's code signature, not a particular Wally script.
Other scripts using that binary can read the item while Keychain is unlocked;
a different binary can prompt, which launchd cannot answer. After replacing the
interpreter, reinstallation of this credential may be required. Rotation is an
operator procedure: update bot credential/source, install the copy with runtime
Python, then restart the poller. This document does not authorize rotation.

Environment token injection is used only when no Telegram reference is set.
The poller uses a configured Gateway credential or a random process-local one.
ChatGPT separately needs owner/Gateway secrets, optional subject/org allowlists
and write flags; the old confirmation flag cannot enable hosted decisions.
Bearer grants stay in memory. Its localhost flow
is not a validated hosted connector. Keep decision tools disabled until D15 is
resolved; never expose the owner-secret grant through a tunnel as a shortcut.

Never print/decrypt credentials for handover. Do not put values in command
arguments, shell history, logs, transcripts or Git. Audit uses references and
purpose. Browser screenshots/traces/HAR/video remain disabled. See [secrets](secrets.md).

## Backup/recovery: not established

**Unknown:** usable backup destination/frequency, retention, encryption/access,
restore procedure, acceptable data loss, and time-to-recover. A registry entry
named `weekly-backup` is not evidence that Wally's state is backed up. Workflow
exports are absent and automated n8n deployment is not implemented (D08/D09).

Before defining a recoverable deployment, obtain operator answers and document:

1. Which current n8n workflows exist, their exports, effects, credentials by
   reference, verification and deployment procedure.
2. Where recoverable copies of all three databases and audit history live, and
   the accepted recovery point/time objectives.
3. How local `.env` credentials, 1Password source, Keychain copy and external
   OAuth permissions are recovered/rotated without storing values in Git.
4. How a new machine recreates Python/browser dependencies, approved registry
   state, canonical catalog/role mappings and the login LaunchAgent.

A future procedure must take a consistent SQLite backup (not copy a live main
file while ignoring journal/WAL state), preserve relationships/decisions and
classifications, and restore to an isolated location first. Review uncertain
execution attempts and Telegram delivery/cursor/leases before reconnecting:
restoring an old snapshot must not replay consequential actions or activate two
pollers. Never clear uncertain records just to make recovery appear successful.

The audit found private local files with `0644` read bits; access/retention and
interpreter-scoped Keychain trust need an explicit threat-model decision (D13).
No backup, chmod, Keychain, lease or service changes were made by the handover.

## D03 operator boundary

The [certified catalog guide](finance-catalog.md) gives local CLI/REPL designation,
additive schema preview/application, primary-evidence intake, exact-version
certification, revocation, chain enablement and governed auth-only acceptance.
`config/finance.yaml` starts with empty source/profile lists, so the milestone
automatically certifies or enables no existing record. The Bill Accounts privacy
restriction applies before designation. Never print raw typed catalog records
or put restricted intake/proof files into Git. Existing state remains untouched
by development verification. Configure/review one live property/utility chain only
under separate owner authorization; code tests are not live readiness evidence.

`finance status` also reports source/record Needs Attention diagnostics by locator.
Malformed legacy rows do not block a provably disjoint certified chain, but unknown
or overlapping identity coordinates block affected namespaces and dependencies.
Never skip such rows to claim uniqueness. Fixing an observed ambiguity does not
restore certificates or enablement; recertify affected records and enable the exact
new binding. See the guide's incremental-certification rules before preparing data.


## Scoped Notion edits: disabled rollout

[Interface-neutral approvals](interface-neutral-approvals.md) documents the new
local stdio command, native biometric confirmation, exact write scope, semantic
verification and deployment gates. `config/notion-edits.yaml` ships with empty
targets, no configured native helper and writes off. `python -m wally.codex` is an
explicit startup operation; normal App/ChatGPT/Telegram startup does not run it.
Like existing runtimes it performs additive database initialization, so it is not
a read-only audit command. It does not refresh providers or financial certification
at startup. Existing ignored state and the Phase 6 checkpoint are preserved.

The local helper must be separately reviewed, built, pinned and validated with the
owner's enrolled biometrics before writes can be enabled. The
[local rollout record](local-codex-rollout.md) records the installed isolated stdio
configuration and signed helper, plus the owner hardware/discovery gates. Use
`--state-dir` and `--edit-policy` for isolated acceptance. Credentials, production
services and live financial data were not changed. `inspect_notion_execution`
reads even RUNNING attempts but cannot release locks or authorize retries.
External Notion writers are not fenced by SQLite; establish a controlled writer
policy and uncertain-attempt recovery first. Never clear unresolved page claims
or re-certify a record as an incidental retry. Hosted ChatGPT decisions remain
hard-disabled pending D15; no public/tunneled approval endpoint is provided.
