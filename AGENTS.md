# Wally engineering instructions

Wally is a personal Chief of Staff / automation system. Runtime policy and state
belong to Wally; external systems are replaceable providers. Home automation is
outside the current product scope.

## Read first and assess evidence

Read `README.md`, `docs/current-architecture.md`, `docs/operations.md`, then the
relevant ADRs in `docs/decisions.md` and tests. Consult `docs/engineering-debt.md`
before work in an affected area; `docs/roadmap.md` describes future delivery.
`docs/architecture.md` and the earlier knowledge reviews are historical notes.

Prefer checked-out code/tests, then Git and `origin/main`, then current design
docs, agent instructions, historical docs/comments, and reported checkpoints.
Distinguish verified behavior, documented intent, inference, and unknown state.
Do not equate fixture coverage or configured providers with live deployment.

## Architecture and security invariants

- New capabilities should converge on the operational proposal/capability/
  provenance architecture (ADR-042). Do not add privileged behavior through the
  legacy conversational ToolRegistry path. That path still exists; its gates
  are not equivalent to operational principal authorization.
- Proposals are advice with intent/reference identifiers, not executable tool
  payloads. Approval is inert. Execution requires a separate authenticated
  capability check and fresh execution-time human authorization.
- Bind decisions/execution to the exact reviewed version/fingerprint. Re-read
  current evidence, approval, Matter state, and canonical execution target after
  the human prompt; reject drift before acting.
- Uncertain outcomes block automatic retries. Never infer completion or mark an
  obligation resolved from a successful login or a triggered workflow alone.
- PrincipalAuthority-issued contexts convey authority. Persisted provenance and
  correlation/session references convey attribution only. Adapter registration
  fixes channel identity; a channel string, source text, model output, or claimed
  confirmation cannot authenticate a caller or authorize itself.
- Telegram and current ChatGPT surfaces must not acquire execute/verify
  capability accidentally. Keep hosted ChatGPT decisions disabled until its
  authentication and explicit confirmation are demonstrated. Intentional trust
  boundary changes require reviewed rationale and an updated/new ADR.
- Governance and pending/unclassified knowledge cannot be written. Keep policy,
  capability selection, and verification in the runtime, not model prose/n8n.
- Keep secret values out of prompts, knowledge, config, databases, logs, tool
  results, command arguments, and reports. Use references; resolve action
  credentials only after authorization. Telegram startup authentication is a
  separate documented secret purpose, not proposal execution authorization.
- Preserve ignored runtime state, classifications, decisions, and audit history.
  Use additive schema evolution; never reset data or change live services,
  credentials, or integrations as an incidental development/test step.
- Application prompts/config can change behavior even when written in Markdown
  or YAML. Treat them as application changes, not documentation-only edits.

## Safe verification with the existing environment

Do not install/sync dependencies or start live providers merely to check docs.
Run `.venv/bin/ruff check --no-cache src tests` and, for ordinary isolated checks:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -k 'not test_load_openai_key_from_dotenv and not test_shell_env_overrides_dotenv and not test_login_keychain_round_trip_stays_in_process'
```

Inspect test definitions before broader execution. The two excluded config tests
rewrite the real repository `.env`; the excluded Keychain test writes/deletes a
login-keychain item. Transport tests bind local sockets; the Gateway socket test
uses `.gateway-test.sock` in the repo and must not replace an existing socket.
Live mailbox/bot/portal checks are operator-initiated, not default test gates.
For docs, also check local links, Markdown fences, and `git diff --check`.
