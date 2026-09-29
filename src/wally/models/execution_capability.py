"""Execution capability identifiers — how Wally executes, not what is being paid."""

from __future__ import annotations

from enum import StrEnum


class CapabilityDomain(StrEnum):
    PAYMENT = "payment"
    COMMUNICATION = "communication"
    DOCUMENT = "document"
    RECONCILIATION = "reconciliation"


class PaymentMethod(StrEnum):
    BANK_TRANSFER = "bank_transfer"
    CARD_PORTAL = "card_portal"
    API = "api"
    DIRECT_DEBIT = "direct_debit"


DEFAULT_PAYMENT_METHOD = PaymentMethod.BANK_TRANSFER
