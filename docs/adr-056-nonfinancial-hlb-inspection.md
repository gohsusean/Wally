# ADR-056: Nonfinancial HLB adapter inspection before payment approval

Owner direction, 24 October 2026: stop incremental Telegram approval requests;
complete nonfinancial debugging before the single real RM1 transfer. Telegram
approval is an established component and is revisited only on regression evidence.

The explicit operator-only `python -B -m wally.runtime.hlb_diagnostics` command
may reuse governed HLB read-only login, open the empty interbank form, forward the
observed bank-generated setup request unchanged and select the exact existing
Sean UOB favourite for identity/control inspection. This narrowly extends
ADR-053/055's earlier prohibition on all transfer-form entry before Telegram.
It grants no payment authority or new interface capability.

An independent fail-closed request guard permits only one exact entry, the
observed nonpayment initialization and an exact empty-form JSF favourite lookup
executing only its selected control. Next/Review, amount/reference entry, unknown
commands, app authorization, beneficiary changes and retries are unavailable.
The inspection route overrides payment submission to reject it unconditionally.
It retains no raw forms, tokens, credentials, screenshots or bank sessions.

The payment worker retains its original exact Telegram boundary. One real MYR 1.00
transfer waits for explicit destination confirmation and then a fresh exact card;
HLB app authorization remains mandatory when requested. Independent bank history,
original receipt, guarded email and truthful Notion reconciliation establish the
outcome. Uncertain outcomes block new transfers and automatic retry.

The RM1 record belongs in existing Payment History without a fabricated Bill
Account or Recurring Bill relation. It never settles a bill obligation.
