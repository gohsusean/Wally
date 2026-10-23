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
reserved before Gmail send. A unique confirmation reference in the exact body permits independent Gmail
SENT readback even when Gmail rewrites Message-ID, verifying recipient, subject,
body and attachment hash. Returned Gmail IDs are also retained before readback. Lost responses,
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

After local owner unlock and fresh CLI approval, governed HLB credential retrieval,
picture verification and login succeeded. An observed menu fragment exposed an
overstrict origin check: URL fragments do not change the exact HTTPS origin. The
check now allows same-origin fragments while still rejecting wrong host/scheme,
ports and URL credentials; picture and transfer evidence checks are unchanged.
The actual DuitNow-to-account/IBG transfer-entry and View History pages were reached
without entering an amount or submitting a transfer. The observed favourites were
Casaman Management and Sean SCB; no Tropicana beneficiary was verified. The owner
was asked for its existing label. No favourite creation/modification is authorized.
Amount/review, complete history/scheduled inventory and original bank-receipt
matching remain unvalidated. No guessed production selector profile or live payment
factory is installed. Casaman was not selected or acted on.

The production Notion Payment History mapping is now pinned to the existing
property IDs and both relation targets. Its operational classification, database
parent and property types passed the real adapter's read-only gate. It uses the
existing Reference Number, Notes, Paid Date, Bill and Provider Account fields,
with a reviewed fixed MYR currency because there is no currency column.
`NativeNotionPaymentHistory` attaches the retained original PDF to Receipt files,
then downloads the saved attachment through a separate unauthenticated client
and compares its SHA-256. No schema changes or fictitious payment records were
made. File uploads use private exclusive claims; uncertain creates/sends cannot
repeat POST. Unknown download hosts fail closed. Simulated tests cover original
bytes, readback, relation drift, currency mismatch and lost upload responses;
production upload/download remains unproven until a real verified receipt exists.

The local Gmail refresh failed with Google's `invalid_grant`. The repaired
`scripts/google_auth.py` opens local browser consent using configured client
credentials, verifies refresh and granted scopes, and atomically saves only the
replacement refresh token in the ignored `.env`. It never accepts client secrets
on the command line or prints tokens/callback URLs; sensitive OAuth library logs
are suppressed. Existing configuration and tokens survive incomplete consent.
The initial consent attempt timed out; a fresh attended consent completed and the
replacement token refreshed successfully with the required granted scopes. The
runtime mailbox matched the owner's existing Gmail connection. Self-addressed
transport diagnostics demonstrated that Gmail rewrites supplied Message-ID even
with complete headers. Receipt recovery therefore searches a unique bank-reference
binding carried in the exact email body, checks one SENT result, exact recipient,
subject/body and original attachment SHA-256, and retains Gmail's actual IDs as
send evidence. Successful send responses are persisted before readback. Lost
responses remain claimed and recover by reads without sending again. Legacy claims
retain their original Message-ID matching. A self-addressed synthetic PDF passed
the production matcher including attachment download/hash. No property-manager
email or actual invoice export occurred.

The new native receipt mapping does not install a production payment factory or
validate live HLB selectors. No live submission, payment card, payment-confirmation
email or settlement write occurred. Submission remains disabled until the exact transaction receives
its authenticated Telegram approval.

Implementation follows Google's [desktop OAuth flow](https://developers.google.com/identity/protocols/oauth2/native-app)
and Notion's [native file upload flow](https://developers.notion.com/guides/data-apis/uploading-small-files).
