# Banking authentication foundation

18 October 2026; attended evidence updated 22 October. This extends the existing Playwright and SecretsProvider adapters;
it adds no browser system, payment route, authority or approval ceremony. The
MacBook profile selects Playwright's `chrome` channel (installed Google Chrome
stable), using [Playwright’s supported channel](https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge). Earlier Maybank checks and the payment sandbox already used Playwright
with Chrome. Production browser selection had still used bundled Chromium.

## Browser isolation and authentication steps

Playwright launches its own temporary browser profile. Each session uses a new
non-persistent context; Wally never attaches to personal Chrome, loads its profile,
or saves browser storage/session state. [Playwright’s page/context isolation](https://playwright.dev/python/docs/api/class-browser#browser-new-page)
is retained. Screenshots, traces, HAR and video remain
disabled. Downloads and service workers are blocked on these sessions.

The existing adapter now exposes internal, reviewed login-step helpers. Inputs
and next/error/challenge selectors come from trusted runtime/operator code, never
from source prose or a model-selected executable payload. The helper verifies the
origin and caller's authorization guard before filling and again before submitting.
It submits at most once, then checks an observable next state. A click is not
successful authentication. Its result contains only fixed state codes and whether
submission was attempted: ready, hidden, disabled, read-only, rejected, advanced,
challenge, stalled, changed origin/authority or failed interaction. It never returns
input values, page text, URLs with query parameters, cookies or raw exceptions.

A CAPTCHA, MFA, security-image confirmation or bank-signing challenge must be
handled through the provider's supported owner interaction. The helper pauses at
reviewed challenge selectors; it does not click them or solve them. An ambiguous
submission is not automatically repeated. Callers close failed/expired sessions.
Only a reviewed authenticated-page condition can establish login success.

The existing payment browser profile optionally carries two reviewed login steps
and its authenticated-page selector. These form part of its reviewed fingerprint;
each staged step must retain the mandatory challenge selector. The same payment
adapter invokes the helper, so future registered bank routes can use staged login
without another runner. No bank-specific live profile is registered here.

## Scoped 1Password access

The first live scoped username/password read succeeded after local owner approval,
taking **25.1 seconds**. The earlier default was 15 seconds. That budget could
expire while a legitimate approval was still pending; this observation explains
some earlier timeouts, not every subsequent failure.

The configured request budget is now 90 seconds, bounded to at most 120. The
account is pinned by a non-secret reference. A standard username/password pair
from the same vault/item uses one documented, field-scoped `op item get --fields`
request by field ID, not mutable labels; it never retrieves the complete item or an
OTP seed. Other references
retain individual field reads. All references and runtime authorization are checked
before any access. Both legacy portal login construction and the supervised-payment
adapter use the same resolver. A batch is not a credential cache: no values are
stored across requests, written to disk or returned through a model tool.

These login paths run no metadata health probe before credential acquisition. The actual read enforces
1Password's access controls. CLI stdin is closed so Wally cannot silently fall back
to entering an account password in a subprocess. Raw CLI output/errors stay out of
reports; audits contain references, purpose and bounded outcome messages only.
Denied/failed/partial requests return no partial credentials and are not retried.
The existing [desktop integration](https://www.1password.dev/cli/app-integration)
still controls unlock and local CLI approval; Wally changes neither its security
settings nor the bank's settings. A locked or unapproved new process remains an
owner-action blocker. A passed probe does not prove unattended availability.

Both review-login and payment authentication resolve authorized credentials before
opening the bank page. Re-read the exact configuration/target binding
after the prompt, then open an isolated session and re-check field state. This
avoids leaving an idle bank page behind the password-manager dialog. No production
service was reloaded during this work.

## Evidence and remaining live blockers

- Unit tests cover scoped account/field selection, rejection of incomplete responses,
  no secret-bearing errors/audits, no retries, authorization before any read,
  transition observation, origin/authority drift, challenge stops and Chrome selection.
- `python -B -m wally.runtime.authentication_demo` completed a two-stage login in
  actual Playwright-controlled Chrome using routed `.invalid` HTML and synthetic
  credentials. A second context did not inherit authentication. This proves the
  reusable browser sequence and isolation, not bank acceptance or live 1Password
  availability.
- One owner-approved live 1Password pair read initially succeeded. Later fresh reads timed
  out awaiting access. The desktop app was running and no shell-session,
  service-account or disabled-integration override was present. No security setting
  was relaxed. Two further attended reads succeeded on 22 October, as described
  below; reliable unattended access has not been demonstrated.
- The successful live read was followed by a browser timeout in the username
  operation before a post-step result was recorded. Its exact cause is unproven;
  it must not be described as a bank rejection or completed username submission.
  A public, credential-free check kept the username field ready for 30 seconds;
  a general idle timeout does not explain it. Earlier empty-form click and Enter
  checks both invoked validation. The new state helper makes the next attended
  check diagnosable without page dumps or another browser framework.
- Maybank login remains unverified. No bank password was submitted, no payment
  initiated and no bank settings modified. No live payment profile is enabled.

### Attended follow-up, 22 October 2026

Two fresh, owner-approved scoped pair reads through the committed resolver and
1Password adapter succeeded in **16.0 seconds** and **12.6 seconds**. This also
accepts the committed bare field-ID selection (`username,password`) against the
live item. Both reads preceded Chrome startup; no health probe, credential cache,
stdin password entry or automatic retry was used. The earlier expired request did
not resume when its delayed approval reply arrived.

Both fresh Playwright-controlled Chrome sessions found the username field ready.
The helper submitted once per session, then reported `transition_not_observed`;
the username field became disabled and no visible password field or recognized
challenge appeared. The owner confirmed a loading indicator in the first session.
The second, separately attended diagnostic observed a same-origin HTTP 201 response
and a failed same-origin XHR during this step. These observations establish that
the browser reached submission, not that Maybank accepted or rejected the username.
The failed request's detailed Chromium error was not captured by the bounded
diagnostic, so its browser/network/backend cause remains unknown.

A separate credential-free inspection of scripts already loaded by Chrome found
blocked public resources, including `abc-api.maybanksandbox.com`. The relationship
of that request to authentication was not established. The origin allowlist was
not expanded, and no bank or password-manager security setting was changed. No
response bodies, payloads, cookies, session tokens, page dumps or screenshots were
reported. Both owned login sessions were closed; neither submitted a bank password.

The continuation at that checkpoint was to identify the failed same-origin XHR's fixed Chromium error
code and its endpoint using public-script evidence, without recording request or
response data. Establish the supported cause before another credential attempt;
do not relax origin restrictions or bank security based on the loading indicator.
Once resolved, observe the username transition, satisfy any bank-required identity
check locally and submit the password at most once in the same bounded session.
Stop after an independently observed login condition, before transfer controls.
The investigation below supersedes this earlier checkpoint.

### Precise failure investigation, 22 October 2026

The attended diagnostic captured **`net::ERR_HTTP2_PROTOCOL_ERROR`** for the
same-origin XHR to the public endpoint suffix `/common/mbbPortalAccess.do`.
Playwright's `requestfailed` event and Chromium's `Network.loadingFailed` agreed;
the request was not canceled and had no reported CORS/block reason. No HTTP status
was observed for that failed request. A separate same-origin request returned
HTTP 201 over HTTP/2; it is not evidence of successful token bootstrap or login.

Public frontend code identifies the failing endpoint as `getTokens`, before the
separate `mbbLoginCheckAdapt.do` username check. Its native bootstrap routine was
then reproduced without credential retrieval, username or password fields. It
failed with the same exact Chromium error. Consequently an invalid banking
credential is not needed to trigger this failure. Request/response payloads and
session tokens were not reported or saved.

Controlled comparisons (one explicit transport experiment, then credential-free probes):

- The owner reported that ordinary Chrome and fresh Incognito both advanced past
  username. These are owner observations, not instrumented Wally acceptance.
- No environment or macOS HTTP/HTTPS/SOCKS/automatic proxy was configured. The
  observed failure is at HTTP/2, after access to the bank origin; no DNS or TLS
  failure was reported for the request. A network middlebox/filter is not excluded.
- Chromium reported eight cookies and no blocked-cookie reasons. Incognito's
  success argues against reliance on existing personal-session cookies; it does
  not certify the automated session's state.
- A single explicit `--disable-http2` experiment negotiated HTTP/1.1 for another
  request but left the bootstrap pending and the username field disabled. The
  pending request was canceled on session closure. This did **not** validate a
  fix; the experimental flag/API was removed and no browser configuration changed.
- Credential-free native bootstrap still failed when the public feature-config
  origin was allowed and when request interception was absent entirely. Neither
  adding that origin nor removing interception is established as a correction.
  Unrestricted networking was confined to the credential-free public comparison.
- Separate read-only HEAD probes returned HTTP 503 with and without interception.
  They did not execute the native POST flow and cannot establish authentication
  rejection or explain the POST's protocol failure.

The precise failure location and Chromium error are established. Its underlying
cause remains unconfirmed: browser configuration/compatibility, bank edge/backend
handling and a security restriction cannot yet be distinguished. No CAPTCHA,
Secure2u prompt or other mandatory bank challenge was observed, so this is **not**
reported as a confirmed mandatory security requirement or an explicit automation
ban. No security-identification flags, TLS checks, cookie protections or bank
controls were changed. No bank password or payment was submitted.

Public code also declares a custom password field, `#my-password-input`, initially
with text input type. The earlier generic password-type selector is not a reviewed
Maybank success condition. No live Maybank profile was registered or enabled from
that source inspection; actual post-username field/challenge states remain to be
accepted in a working session.

The smallest validated code change retains bounded first-party XHR/fetch network
error codes and HTTP error statuses separately in the existing `LoginResult`.
Only fixed network-error codes and integer HTTP statuses can leave the observer;
unknown error strings are replaced, and URLs, payloads, headers and cookie values
are never returned. Listeners are removed after each step. This closes the earlier
diagnostic gap; it does **not** claim to fix bank authentication. Playwright
[distinguishes failed requests from HTTP error responses](https://playwright.dev/python/docs/api/class-request#request-failure).

### 1Password authorization is recurring

Enabling desktop integration is setup; granting CLI access is a runtime security
requirement. [1Password's documented authorization model](https://www.1password.dev/cli/app-integration-security)
requires authorization in each new terminal session, expires after ten minutes of
inactivity, has a twelve-hour hard limit and revokes prior access when the desktop
app locks. Same-session activity can reuse a still-valid grant. Wally's separate
attended checks ran in fresh terminal sessions and requested fresh local approvals.
A payment executed later can therefore require local authorization again; one
successful setup does not establish unattended credential availability. No keepalive,
credential cache, service-account substitution or security-setting change was added.

Shortest supported continuation: obtain a bank-supported explanation/remedy for
the anonymous token-bootstrap protocol failure in this managed Chrome configuration,
then perform one fresh end-to-end auth-only acceptance. Share only the fixed error,
public endpoint and comparison outcomes with support, never a HAR, session tokens
or credential-bearing logs. No external message was sent. A supported remedy must
preserve browser isolation, runtime origin policy and all bank-required checks;
no live payment route is enabled by these diagnostics.

## Fit for one Telegram approval

Maybank's [current authorization FAQ](https://www.maybank2u.com.my/maybank2u/malaysia/en/personal/frequent_queries/secure2u_tac/use-two-authorisation-methods.page)
explicitly includes new and favourite fund transfers in Secure2u-on-MAE scope.
Maybank therefore has a documented additional owner-authentication requirement;
1Password cannot replace it. One exact Telegram approval plus unavoidable bank
approval is the supported target for such a route, not a demonstrated live flow.

Hong Leong remains a possible alternative. Its [current Connect FAQ, section E,
AppAuthorise Q3](https://www.hlb.com.my/en/connect/faq-en.html/)
places favourite transfers above RM10,000 in one category and non-favourite
transfers at any amount in another. This supports investigating an existing
favourite transfer below RM10,000, but the FAQ says its list is not exhaustive.
The [general Connect page](https://www.hlb.com.my/en/personal-banking/hlb-connect.html)
also uses broader authorization wording. No exemption for this account/recipient/
transaction has been confirmed; no HLB login, transfer or setting change was tried.
Maybank remains the owner's designated transfer bank until changed.

Authentication does not approve a payment, verify an unpaid balance or establish
settlement. Preserve the exact Telegram transaction approval, worker-only payment
capabilities and independent outcome verification. Obligations through December
2026 remain administratively covered unless the owner explicitly identifies a
specific obligation as unpaid. This work changes no financial baseline or certificates.

## Final verification

960 isolated tests and two local transport tests passed. The transport checks used
socket permission after preserving any existing Gateway test socket. The three
repository `.env`/login-Keychain mutation tests remain excluded. Ruff, documentation
links/fences and `git diff --check` passed. Both offline Chrome demonstrations
passed, including the pre-existing single-Telegram-approval payment simulation.
The authentication fixture also passed through the existing payment adapter’s
staged authentication method, without invoking any payment method. These are not
live bank or payment acceptance results.

The earlier 22 October follow-up changed this evidence report only. All 39 focused secrets
and browser-authentication tests passed again, as did Ruff and `git diff --check`.
The earlier full-suite result above was not rerun or represented as a new run.

The subsequent precise-error investigation added the bounded login diagnostics and
two privacy/cleanup tests. **962 isolated tests passed, five deselected** (the three
real `.env`/Keychain mutation tests and the two socket transport tests). The latter
were not rerun for this browser-only change. All 41 focused authentication/secrets
tests, Ruff, local documentation links/fences and `git diff --check` passed. The
existing offline Chrome authentication demo passed again through the updated
adapter, with synthetic credentials, separate contexts and no live bank network.
No successful live bank authentication or unattended secrets access is claimed.
