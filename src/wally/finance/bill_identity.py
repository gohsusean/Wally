"""Billing-account identity, distinct from the beneficiary's bank account."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from wally.finance.models import FinanceError, digest


class BillingAccountError(FinanceError):
    """Bounded owner-facing diagnostic, never provider exception text or secrets."""


def normalize_billing_account(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BillingAccountError("Billing account identifier is missing; payment is blocked.")
    # Remove only formatting. Never drop/reorder letters or digits, strip leading
    # zeros, use fuzzy/OCR substitutions, or fall back to a bank account/address.
    normalized = re.sub(r"[\s\-\u2010-\u2015]", "", value)
    if not re.fullmatch(r"[A-Za-z0-9]{1,100}", normalized):
        raise BillingAccountError("Billing account identifier cannot be matched confidently.")
    return normalized.upper()


def require_billing_account_match(notion: str, invoice: str) -> None:
    if not isinstance(notion, str) or not notion.strip():
        raise BillingAccountError(
            "Notion Bill Accounts.Account Number is missing; payment is blocked."
        )
    if not isinstance(invoice, str) or not invoice.strip():
        raise BillingAccountError(
            "Invoice billing account identifier is missing; payment is blocked."
        )
    if normalize_billing_account(notion) != normalize_billing_account(invoice):
        raise BillingAccountError(
            "Invoice billing account does not match Notion Bill Accounts.Account Number; "
            "payment is blocked. Neither value was changed."
        )


@dataclass(frozen=True)
class InvoiceAccounts:
    reference: str
    content_hash: str
    identifiers: tuple[str, ...]


class RetainedInvoiceAccounts:
    """Trusted composition supplies exact documents and reviewed issuer extractors.

    An extractor receives the original bytes and returns only labelled customer /
    property / unit / billing identifiers. It must return all candidates, including
    contradictions. It cannot choose a match using the Notion value. No text from
    another invoice, bank destination or postal address is a fallback.
    """

    def __init__(self, bindings):
        self.bindings = dict(bindings)  # evidence reference -> (private path, extractor)

    def read(self, evidence):
        documents = []
        for proof in evidence:
            if proof.kind not in {"invoice", "statement"}:
                continue
            binding = self.bindings.get(proof.reference)
            if binding is None:
                raise BillingAccountError("This invoice has no reviewed billing-account extractor.")
            path, extractor = binding
            path = Path(path)
            try:
                original = path.read_bytes()
                if hashlib.sha256(original).hexdigest() != proof.content_hash:
                    raise BillingAccountError(
                        "Invoice evidence changed; billing-account check is stale."
                    )
                identifiers = extractor(original)
                if not isinstance(identifiers, tuple) or any(
                    not isinstance(value, str) for value in identifiers
                ):
                    raise BillingAccountError("Invoice billing-account extraction is unreadable.")
                if hashlib.sha256(path.read_bytes()).hexdigest() != proof.content_hash:
                    raise BillingAccountError("Invoice evidence changed during account extraction.")
            except BillingAccountError:
                raise
            except OSError:
                raise BillingAccountError(
                    "Original invoice is unavailable for account matching."
                ) from None
            except Exception:
                raise BillingAccountError(
                    "Invoice account extraction failed; payment is blocked."
                ) from None
            documents.append(InvoiceAccounts(proof.reference, proof.content_hash, identifiers))
        return tuple(documents)


def reviewed_account_match(notion_account, evidence, documents, notion_binding):
    # Require complete coverage of THIS occurrence's actual invoice/statement proof.
    expected = {
        (e.reference, e.content_hash) for e in evidence if e.kind in {"invoice", "statement"}
    }
    actual = [(d.reference, d.content_hash) for d in documents]
    if not expected or set(actual) != expected or len(actual) != len(set(actual)):
        raise BillingAccountError(
            "Billing-account extraction is missing, ambiguous or from another invoice."
        )
    first = ""
    for document in documents:
        if not document.identifiers:
            raise BillingAccountError(
                "Invoice billing account identifier is missing; payment is blocked."
            )
        normalized = {normalize_billing_account(value) for value in document.identifiers}
        if len(normalized) != 1:
            raise BillingAccountError(
                "Invoice has ambiguous billing account identifiers; payment is blocked."
            )
        for identifier in document.identifiers:
            require_billing_account_match(notion_account, identifier)
        first = first or document.identifiers[0]
    return {
        "account_reference": notion_account,
        "invoice_account_reference": first,
        "account_match_binding": digest(
            [
                notion_binding,
                notion_account,
                [asdict(d) for d in documents],
            ]
        ),
    }
