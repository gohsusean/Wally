# ADR-055: Exact owner-account test transfers

**Status:** Owner directed, 24 October 2026; live acceptance pending.

The owner authorized reusable preparation of real HLB acceptance transfers to the
existing **Sean UOB** favourite, **UOB account 2203062747**, exactly **MYR 1.00**,
with the original bank receipt emailed to **gohsusean@gmail.com**. This is permission
to prepare and propose tests, never blanket permission to transfer money.

Use the existing supervised-payment proposals, exact private Telegram callback,
separate PrincipalAuthority decision/execution confirmations, narrow worker,
durable occurrence/prepared-transaction claims and independent bank verification.
No channel receives new execute/verify capabilities. Every distinct test needs
fresh Telegram approval before bank login/transfer entry; AppAuthorise remains a
separate mandatory owner action. Replay and uncertain outcomes block further
submission; an uncertain test also blocks creating another test to evade its hold.

The trusted test composition alone omits invoice, billing-account, billing-period,
due-date, Typical Amount and genuine bill reconciliation requirements. Its source
reconstructs only the exact owner-account allowlist; ordinary bill services cannot
load its specifications, and retain all existing validation and disabled live gates.
The fixed policy lives in `config/test-transfer.yaml`; changing it is refused
pending explicit owner review. No beneficiaries are created or edited.

The live test provider validates the selected favourite, bank/account, current
one-time date, HLB source suffix, amount and references before Next/Review. A
per-command request guard checks the official URL, current runtime authorization,
exact encoded form values and command; unknown requests latch the route closed.
Review is claimed before its only click. It may initiate payment/AppAuthorise;
there is no later Submit click and no automatic retry.

The RM1 DuitNow fee assumption uses HLB's published
[Connect fee table](https://www.hlb.com.my/en/connect/faq-en.html), which lists
zero online transfer fees. Any displayed nonzero or unreadable fee still stops
the test before its initiating action.

Only an independently navigated bank history/detail match can establish settlement.
The exact original bank PDF is retained and sent through the existing guarded
Gmail receipt claim/readback. Email failure never repeats a transfer. Tests are
identified in existing `payment_specs`, `payment_attempts`, execution and event
records, with receipt files under `data/supervised-payment`; the local history
adapter writes no Notion bills or property obligations. No new database or test
management framework is introduced.

`/pay test` requests a distinct test review. It does not approve it. Production
registers this narrow route alongside the disabled ordinary bill composition.
Startup never creates or executes a test. Synthetic unit tests check the boundary;
they do not establish real HLB acceptance.

## Live evidence, 24 October 2026

Real Telegram owner callbacks were recorded before worker authorization and HLB
login. The running LaunchAgent retrieved the HLB login through 1Password after
the owner authorized its Python CLI prompt, and verified the authenticated bank
dashboard. No earlier test reached Next/Review or dispatched an initiating request;
each stopped attempt remains durably recorded as `not_submitted`.

Execution exposed an unstable implementation fingerprint: Python marshal output
changed while the actual submit method was active. Stable loaded-code semantic
material now binds the route; a regression exercises the real submit frame and
its separate callback greenlet without contacting the bank.

The bank dashboard confirms **PAY & TRANSACT → DuitNow to Account/ IBG** uses
the shared transfer endpoint with a menu parameter. Navigating to its bare URL
selected the internal HLB-account variant. The worker now clicks PAY & TRANSACT
and waits for the exact visible DuitNow/IBG link, pinning its complete destination
before navigation. It refuses the bare endpoint and ambiguous menu destinations.

As of 09:45 UTC, the corrected-menu attempt awaits a new Telegram approval.
No RM1 transfer, original test receipt or test confirmation email has yet been
verified. End-to-end bank acceptance remains pending that approved execution.
The original Tropicana claims and confirmation evidence remain unchanged.

At 22:19 MYT, another fresh owner Telegram callback preceded successful HLB
login. The worker stopped before transfer entry because hovering did not expose
the submenu. An isolated read-only session then verified that clicking the
PAY & TRANSACT toggle exposes DuitNow to Account/ IBG (`o=1`); it opened no
transfer form. Commit `89dc812` deploys that verified click and visibility wait.
As of 22:28 MYT, its fresh Telegram review remains unapproved; no bank session
has started for that attempt. Ordinary bill submission remains disabled.
