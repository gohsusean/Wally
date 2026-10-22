# HLB security-picture confirmation

The owner's final decision on 23 October 2026 is **HLB only** for bank transfers.
Maybank development is stopped. Assume bank-app authorization is required; do not
investigate exemptions. Every payment still requires its specific Telegram card,
and the owner completes required bank-app authorization. Login/enrollment never
initiates or authorizes a payment. See [ADR-052](decisions.md#adr-052-hlb-only-and-local-security-picture-enrollment).

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
rechecking origin, reference and image after the authorization guard. Only a match
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

The command accepts `--username-ref`, `--password-ref`, `--picture-selector`,
`--checkbox-selector`, `--password-selector`, `--submit-selector` and
`--authenticated-selector`. The public username controls observed on 23 October
are `#idLoginId` and `#idBtnSubmit1`. The post-username selectors and a minimal
HLB authenticated-only condition still need live operator acceptance; no guessed
production profile is installed. A failed transition closes the session without
retrying. Do not select account-content dumps as a success condition.

Add `--enroll` for the one-time enrollment. Inspect the actual picture in the
visible HLB browser, then type `CONFIRM HLB PICTURE` in the local terminal. A
successful enrollment closes the browser **before password entry**. Run again
without `--enroll` for the picture-gated auth-only login. `--replace-picture`
explicitly replaces an existing reference with the same owner ceremony; it is
never automatic. No image or fingerprint is printed. A failed/denied 1Password
request, new bank challenge or failed login requires an attended fresh attempt.

Trusted payment profiles may use the same `hlb_picture` field; HLB profiles without
it are refused. The selectors are part of the profile fingerprint. No live payment
profile, route, executor or service is enabled by this change.

## Validation and remaining acceptance

Unit checks cover exact match, mismatch, missing/hidden/ambiguous/unreadable images,
HTTPS origin rejection, reference permissions/symlinks, explicit replacement,
post-confirmation drift and the existing payment adapter's password gate. The
local-sync test verifies the private reference stays outside its backup allowlist.

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

**One-time owner enrollment remains required.** No real picture was captured and no
new credentialed HLB login or payment was attempted in this implementation task.
The previous [attended HLB success](authentication-foundation.md#attended-hong-leong-result-23-october-2026)
remains historical evidence. After enrollment, perform an attended auth-only check
against reviewed selectors before claiming the automated HLB login is live-validated.

Verification on 23 October: **963 isolated tests passed** against the staged source,
with the three real `.env`/Keychain mutation tests and two socket tests excluded.
All 82 focused authentication/picture/preferences/sync tests, Ruff, local documentation
links/fences, `git diff --check` and both offline Chrome authentication demos passed.
Unrelated pre-existing working-tree changes are excluded from this commit.
