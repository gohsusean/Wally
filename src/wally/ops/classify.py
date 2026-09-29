"""Deterministic classification of untrusted source text. Not policy."""

from __future__ import annotations

import re

from wally.models.ops import ObservationCategory
from wally.runtime.content_sanitizer import sanitize_external_text

_INVOICE = re.compile(
    r"\b(invoice|bill|amount due|payment due|statement due|overdue)\b",
    re.IGNORECASE,
)
_RECEIPT = re.compile(
    r"\b(receipt|payment received|thank you for your payment|paid in full|"
    r"payment confirmation)\b",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(ignore (all )?(previous|prior|above) instructions|"
    r"use the password|"
    r"mark this bill paid|"
    r"delete the audit|"
    r"run this shell|"
    r"approve this payment|"
    r"send this email immediately)",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text or ""))


def sanitize_source_text(text: str, *, max_chars: int = 280) -> str:
    return sanitize_external_text(text or "", max_chars=max_chars)


def classify_email(
    *, subject: str, snippet: str, labels: tuple[str, ...] = ()
) -> ObservationCategory:
    blob = f"{subject} {snippet}"
    labelset = {label.upper() for label in labels}
    if _RECEIPT.search(blob):
        return ObservationCategory.RECEIPT
    if _INVOICE.search(blob):
        return ObservationCategory.INVOICE
    if subject.lower().startswith("re:") or subject.lower().startswith("fwd:"):
        return ObservationCategory.EMAIL_REPLY
    if "SENT" in labelset:
        return ObservationCategory.EMAIL_SENT
    return ObservationCategory.EMAIL_RECEIVED


def classify_domain(category: ObservationCategory) -> str:
    if category in {ObservationCategory.INVOICE, ObservationCategory.RECEIPT}:
        return "finance"
    if category.value.startswith("calendar"):
        return "calendar"
    if category.value.startswith("email") or category == ObservationCategory.EMAIL_SENT:
        return "communications"
    if category == ObservationCategory.KNOWLEDGE_OBLIGATION:
        return "admin"
    return "other"
