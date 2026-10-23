# HLB security-picture confirmation

The owner's final decision on 23 October 2026 is **HLB only** for bank transfers.
Maybank development is stopped. Assume bank-app authorization is required; do not
investigate exemptions. Every payment still requires its specific Telegram card,
and the owner completes required bank-app authorization. Login/enrollment never
initiates or authorizes a payment. See [ADR-052](decisions.md#adr-052-hlb-only-and-local-security-picture-enrollment).

**Live acceptance, 23 October 2026:** the owner confirmed the actual picture in
Chrome and its private fingerprint was enrolled. Two attended bank logins succeeded;
the second used the existing fingerprint without another picture-confirmation prompt
and the updated command detected the authenticated dashboard. No transfer, payment
or beneficiary change occurred. Earlier enrollment-pending status notes are superseded.

## Enrollment and login

Use the existing isolated Playwright/Chrome session and governed 1Password resolver.
The internal `HLBSecurityPicture` gate checks the exact official HTTPS origin,
`https://s.hongleongconnect.my`, before reading/interacting. Normal TLS verification
and origin-restricted networking remain enabled. It requires one visible loaded
same-origin IMG, bounded intrinsic dimensions and opaque nonblank pixels. An
unreadable/cross-origin/ambiguous picture fails closed; there is no fallback to
alt text, filenames, a URL match, approximate similarity or LLM vision.

Enrollment captures decoded RGBA pixels **in memory**, asks the owner to recognize
the actual picture in the local bank window, then rechecks it after confirmation.
It stores only a SHA-256 fingerprint of the exact dimensions/pixels in
`data/private-auth/hlb/picture.sha256` (0700 directory, 0600 file). The picture itself
is never written, attached to a chat, sent to Notion, logged or backed up. The hash
is Git-ignored with `data/`, outside Wally's explicit iCloud backup allowlist, and
is never included in audit/tool results. Unsafe permissions/symlink paths fail
closed. Browser traces, screenshots, HAR, video and persistent sessions stay off.

Future logins compare repeated exact pixel fingerprints to the enrolled reference,
pinning that reference for the whole attempt and rechecking origin, reference and
image after the authorization guard. A replacement reference stops the current
attempt even when the displayed image matches the replacement. Only a match
can tick the native checkbox. A normal focus click enables HLB's password field;
the existing guarded login helper then fills/submits the scoped 1Password password
once. The picture and checkbox are rechecked before password fill and submission.
Missing/mismatching/changing images or references stop without learning anything.
Replacement requires a separate explicit local owner confirmation; normal login
never calls enrollment. Browser/OS rendering changes can cause a safe false
mismatch; investigate before explicitly enrolling a replacement.

## Operator command

Run from the repository's local terminal. This starts no Wally services/payment
worker and performs no transfers. Supply the **reviewed actual HLB selectors** and
1Password references; these are trusted operator inputs, never extracted from
emails, page instructions or model-proposed action payloads:

```sh
.venv/bin/python -B -m wally.runtime.hlb_login --help
```

The command requires `--username-ref` and `--password-ref`. Its selector defaults
were reviewed against the live page on 23 October: `#idLoginId` / `#idBtnSubmit1`,
the image immediately preceding `#idSBCBConfirmPic`, the native checkbox,
`#idPswd` / `#idBtnSubmit2`, and a visible `a[href*="logout" i]` inside the single
dashboard iframe. `--authenticated-frame-selector` uses Playwright's public
`frame_locator` API; the other selector overrides remain available to trusted
operators. No account content is collected to detect login success. A failed
transition closes the session without retrying; no production payment profile is
installed. Wait for the known username control, with origin checks before and
after the bounded wait, because HLB navigates after the initial DOMContentLoaded.

Add `--enroll` for the one-time enrollment. Inspect the actual picture in the
visible HLB browser, then type `CONFIRM HLB PICTURE` in the local terminal. A
successful enrollment closes the browser **before password entry**. Run again
without `--enroll` for the picture-gated auth-only login. `--replace-picture`
explicitly replaces an existing reference with the same owner ceremony; it is
never automatic. No image or fingerprint is printed. A failed/denied 1Password
request, new bank challenge or failed login requires an attended fresh attempt.
For initial setup in one browser session, `--enroll-and-login` enrolls only after
the same explicit owner confirmation and then runs the unchanged image/password
gate. Declining enrollment stops before password entry. It cannot replace an
existing reference. Fixed phase labels distinguish credential failures before bank
navigation from later failures without exposing provider exceptions or page data.

Trusted payment profiles may use the same `hlb_picture` field; HLB profiles without
it are refused. The selectors are part of the profile fingerprint. No live payment
profile, route, executor or service is enabled by this change.

## Validation and remaining acceptance

Unit checks cover exact match, mismatch, missing/hidden/ambiguous/unreadable images,
HTTPS origin rejection, reference permissions/symlinks, explicit replacement,
post-confirmation drift and the existing payment adapter's password gate. The
local-sync test verifies the private reference stays outside its backup allowlist.
Reference-and-image replacement during confirmation or before password focus also
stops the attempt; a fresh login is required after explicitly enrolling a change.

```sh
.venv/bin/python -B -m wally.runtime.hlb_authentication_demo
.venv/bin/python -B -m wally.runtime.authentication_demo
```

The first uses real Chrome, a synthetic gradient and synthetic 1Password values
through the existing governed resolver. All network requests are fulfilled locally.
It exercises native checkbox/focus, password submission and an observed login
condition, and proves mismatch/missing/ambiguous/unloaded/cross-origin images leave
the password blank and checkbox unticked. Only a temporary hash and sanitized
audit records are written. The second retains the existing staged-login regression
and context-isolation check. These fixtures do not establish live HLB acceptance.

The initial implementation below used synthetic evidence only. The later attended
acceptance in this document establishes live image recognition and login on this
machine at that time; it does not establish permanently unattended 1Password access
or a live payment route. The earlier [attended HLB success](authentication-foundation.md#attended-hong-leong-result-23-october-2026)
preceded automatic enrollment and dashboard detection.

Verification on 23 October: **963 isolated tests passed** against the staged source,
with the three real `.env`/Keychain mutation tests and two socket tests excluded.
All 82 focused authentication/picture/preferences/sync tests, Ruff, local documentation
links/fences, `git diff --check` and both offline Chrome authentication demos passed.
Unrelated pre-existing working-tree changes are excluded from this commit.

Reference-binding follow-up, 23 October: two regression tests first reproduced
acceptance of a replacement reference/image during an active login. The attempt
now retains its original fingerprint through checkbox confirmation, password fill
and submission. **965 isolated tests passed** against a clean source snapshot plus
this follow-up, with the same five exclusions; all **84 focused tests**, Ruff,
documentation links/fences, `git diff --check` and both offline Chrome demos passed.
The snapshot used the existing virtualenv without installing dependencies. No usable
local enrollment reference was present; owner enrollment and live acceptance remain.

### Attended enrollment and login acceptance, 23 October

The official HTTPS origin was checked before interaction. The owner viewed the
actual security picture in Wally's isolated Chrome window and explicitly confirmed
it in this chat. Enrollment then re-read the unchanged pixels and stored only the
fingerprint locally (0600 file, 0700 directory). The first login used the automated
image gate and 1Password password, and the owner confirmed the dashboard. The old
outer-page detector missed it; repeated read-only checks found a visible logout
link inside the iframe while both login fields were absent. The detector now
supports a reviewed next-state frame without changing image verification.

One additional controlled bank login ran through the updated operator command.
It recognized the enrolled picture without manual picture confirmation, submitted
once and returned `advanced` from the framed dashboard detector. Both isolated
browser sessions were closed. No further bank logins were performed. Earlier
read-only public-page inspections interrupted by navigation submitted no username;
credential retrieval failures also stopped before opening the bank.

1Password reads from an agent-launched PTY sometimes timed out before navigation.
The visible local Terminal command completed after local 1Password authorization.
Future runs can still need owner unlock/authorization in 1Password. Payment-specific
Telegram approval and owner bank-app authorization remain separate requirements;
neither was exercised or changed here. The fingerprint and actual image remain
outside Git, reports, Wally backups and Notion.

Verification: **975 isolated tests passed**, with the three real `.env`/Keychain
mutation tests and two socket tests excluded. Ruff and the offline Chrome HLB
demo passed; that demo now exercises a dashboard iframe as well as match/mismatch,
missing/ambiguous/unloaded/cross-origin pictures. New tests cover framed success,
missing framed evidence, bounded form readiness, declined enrollment and safe
credential-failure diagnostics. No dependencies, live services or payment routes
were installed, restarted or enabled. Unrelated local edits remain excluded.
