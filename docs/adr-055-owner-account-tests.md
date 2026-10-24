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
