# Billing-account matching and receipt confirmations

## Mandatory billing identity

Every supervised bill payment compares the related current Bill Accounts
`Account Number` with the customer/property/unit/billing identifier extracted from
that occurrence's actual invoice or statement. This is separate from validating
the beneficiary's receiving bank account. Neither value is overwritten.

`CertifiedPaymentSource` refreshes the existing mapped catalog, then invokes a
trusted, explicitly registered invoice-account reader. `RetainedInvoiceAccounts`
reads the original document, checks its retained SHA-256 before extraction and
again afterwards, and binds every invoice/statement evidence reference. An issuer
extractor receives only the original bytes; it must return all labelled billing
identifiers without selecting a match using the Notion value. There is no default
guessed parser, model payload, previous-invoice fallback, address inference or bank
account substitution. Missing extraction registration or evidence fails closed.

Comparison removes spaces/hyphens and ignores ASCII letter case. Letters/digits,
leading zeros and ordering remain exact. Unsupported characters, absent values,
contradictory candidates and incomplete/wrong-invoice coverage block preparation.
Repeated identical identifiers across a multi-invoice PDF are valid. All documents
in a quarterly bundle must match. The raw references and evidence binding join the
immutable payment specification; changes require a fresh review, including changes
observed after the Telegram decision or credential retrieval. The Telegram card
shows the invoice and Notion billing references separately from the bank destination.

Typical Amount remains advisory and never supplies an invoice amount. A comparison
requires one complete comparable billing cadence: a partial month/quarter is not
multiplied to make it comparable. The existing inclusive 10% Decimal threshold and
Approve availability remain unchanged.

## Associated receipt email

The same exact payment card includes the linked Bill Providers confirmation
requirement and email address. Stable `payment_review_fields` mappings read the
checkbox and email after following the issued instance -> recurring definition ->
bill account -> provider relations. Recipient/requirement/binding drift before
execution requires a fresh review. The narrow worker alone handles the associated
email; no generic email operation or new surface capability is exposed.

After independent bank settlement, the trusted bank adapter must retrieve and
match the actual original PDF receipt, including the bank transaction date. A
generated success summary is not a bank receipt. The original is retained privately
under the existing evidence directory. Confirmation includes the billing reference,
period, actual amount/date and bank reference and attaches those exact bytes.

An additive claim in `payment_attempts`, unique by bank namespace/reference, is
reserved before Gmail send. A deterministic Message-ID permits independent Gmail
SENT readback, verifying recipient, subject, body and attachment hash. Lost responses,
ambiguous inventory, failed sends or mismatched readback retain an uncertain email
claim. Recovery reads Gmail and never repeats send or bank submission. An email
problem preserves verified payment settlement and leaves a durable follow-up event.

The existing Notion receipt is independently created/read back before its exact
email metadata is updated. The evidence field retains the ledger hash plus email
recipient/send evidence. Optional reviewed properties support the recurring-bill
relation, bank payment date and confirmation checkbox/date. Narrow idempotent PATCH
recovery reads an already applied update without repeating payment or email. The
general opening baseline and unrelated Notion records are untouched.

## Current acceptance boundary, 23 October 2026

Tropicana Gardens Q4 replaces Casaman as the first-payment selection. Casaman stays
on hold pending its invoice. The original Tropicana three-page invoice, its hash,
live Notion comparisons and the owner's specific unpaid instruction are retained
privately under `data/manual-payment/`; no invoice or owner data belongs in Git.
That instruction is evidence of the baseline exception, never transfer approval.

Read-only HLB login/picture verification succeeded during this continuation. The
temporary navigation inspector stopped before any transfer entry; a later fresh
session failed governed 1Password retrieval. Existing-beneficiary entry, review,
complete history/scheduled inventory and independently matched original bank-receipt
download remain unvalidated. No guessed selectors or production payment factory
are installed. The local Gmail runtime also failed OAuth refresh; the connected
Gmail search tool remains available, which does not validate unattended runtime
send access.

The existing production Notion Payment History uses native Receipt files and lacks
some columns of the synthetic receipt contract. Its live target/native receipt
attachment/readback still needs supported mapping and validation. The adapter
extensions and simulated Gmail/Notion tests do not prove that integration. No live
submission, payment card, email or settlement write occurred. Submission remains
disabled until the exact transaction receives its authenticated Telegram approval.
