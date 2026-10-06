"""Deterministic evidence verification against trusted Knowledge Assets."""

from __future__ import annotations

import re
from typing import Any

from wally.models.execution_capability import PaymentMethod
from wally.models.verification import VerificationCheck, VerificationReport, VerificationStatus


def normalize_bank_account(value: str | None) -> str:
    """Canonical bank account digits for comparison."""
    if not value:
        return ""
    return re.sub(r"\D", "", str(value))


def normalize_amount(value: object | None) -> str:
    """Normalize monetary amounts for comparison."""
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"^[A-Z]{2,3}\s*", "", text, flags=re.IGNORECASE)
    text = text.replace(",", "").replace(" ", "")
    text = re.sub(r"^[^\d-]+", "", text)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return match.group(0) if match else ""


def normalize_text(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


class VerificationEngine:
    """Compare external evidence to trusted knowledge before high-trust actions."""

    def verify_bill_payment(
        self,
        *,
        trusted: dict[str, Any],
        evidence: dict[str, Any] | None,
        payment_amount: object | None = None,
        payment_method: str | None = None,
    ) -> VerificationReport:
        """Verify bill payment fields. Trusted = Knowledge Asset; evidence = statement/email."""
        method = _normalize_payment_method(
            payment_method or _first(trusted, "payment_method")
        )
        if method == PaymentMethod.CARD_PORTAL:
            return self._verify_card_portal_payment(
                trusted=trusted,
                evidence=evidence or {},
                payment_amount=payment_amount,
            )
        return self._verify_bank_transfer_payment(
            trusted=trusted,
            evidence=evidence or {},
            payment_amount=payment_amount,
        )

    def _verify_bank_transfer_payment(
        self,
        *,
        trusted: dict[str, Any],
        evidence: dict[str, Any],
        payment_amount: object | None,
    ) -> VerificationReport:
        checks: list[VerificationCheck] = []
        blocked = False
        block_reason: str | None = None

        trusted_payee = _first(trusted, "payee", "provider", "destination")
        evidence_payee = _first(evidence, "payee", "provider")
        checks.append(_compare_text("Payee", trusted_payee, evidence_payee, critical=True))

        trusted_amount = _first(trusted, "amount") or payment_amount
        evidence_amount = _first(evidence, "amount")
        checks.append(
            _compare_amount("Amount", trusted_amount, evidence_amount, payment_amount)
        )

        trusted_due = _first(trusted, "due_date")
        evidence_due = _first(evidence, "due_date")
        checks.append(_compare_optional_text("Due date", trusted_due, evidence_due))

        evidence_source = _first(evidence, "source", "statement_source")
        if evidence_source:
            checks.append(
                VerificationCheck(
                    field="statement_source",
                    status=VerificationStatus.VERIFIED,
                    message=f"Statement source: {evidence_source}",
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    field="statement_source",
                    status=VerificationStatus.MISSING,
                    message="Statement source: not provided",
                )
            )

        trusted_bank = _first(trusted, "bank_account", "account_number")
        evidence_bank = _first(evidence, "bank_account", "account_number")

        bank_check, bank_blocked, bank_reason = _verify_bank_account(
            trusted_bank, evidence_bank
        )
        checks.append(bank_check)
        if bank_blocked:
            blocked = True
            block_reason = bank_reason

        for check in checks:
            if check.critical and check.status == VerificationStatus.MISMATCH and not blocked:
                blocked = True
                block_reason = check.message

        return VerificationReport(
            checks=tuple(checks),
            blocked=blocked,
            block_reason=block_reason,
        )

    def _verify_card_portal_payment(
        self,
        *,
        trusted: dict[str, Any],
        evidence: dict[str, Any],
        payment_amount: object | None,
    ) -> VerificationReport:
        checks: list[VerificationCheck] = []
        blocked = False
        block_reason: str | None = None

        trusted_provider = _first(trusted, "provider", "payee", "destination")
        evidence_provider = _first(evidence, "provider", "payee")
        checks.append(
            _compare_text("Provider", trusted_provider, evidence_provider, critical=True)
        )

        trusted_amount = _first(trusted, "amount") or payment_amount
        evidence_amount = _first(evidence, "amount")
        checks.append(
            _compare_amount("Amount", trusted_amount, evidence_amount, payment_amount)
        )

        trusted_portal = _first(trusted, "payment_portal_url", "portal_url")
        evidence_portal = _first(evidence, "payment_portal_url", "portal_url")
        checks.append(
            _compare_text(
                "Payment portal URL",
                trusted_portal,
                evidence_portal,
                critical=True,
            )
        )

        trusted_reference = _first(trusted, "account_reference", "account")
        evidence_reference = _first(evidence, "account_reference", "account")
        checks.append(
            _compare_text(
                "Account reference",
                trusted_reference,
                evidence_reference,
                critical=False,
            )
        )

        evidence_source = _first(evidence, "source", "statement_source")
        if evidence_source:
            checks.append(
                VerificationCheck(
                    field="statement_source",
                    status=VerificationStatus.VERIFIED,
                    message=f"Statement source: {evidence_source}",
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    field="statement_source",
                    status=VerificationStatus.MISSING,
                    message="Statement source: not provided",
                )
            )

        for check in checks:
            if check.critical and check.status == VerificationStatus.MISMATCH and not blocked:
                blocked = True
                block_reason = check.message

        return VerificationReport(
            checks=tuple(checks),
            blocked=blocked,
            block_reason=block_reason,
        )


def format_verification_summary(report: VerificationReport) -> list[str]:
    """Concise verification lines for approval prompts."""
    return [f"  {check.message}" for check in report.checks if check.message]


def _normalize_payment_method(value: object | None) -> str:
    if value is None or str(value).strip() == "":
        return PaymentMethod.BANK_TRANSFER
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for method in PaymentMethod:
        if normalized == method.value:
            return method.value
    return normalized


def _first(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _compare_text(
    label: str,
    trusted: str | None,
    evidence: str | None,
    *,
    critical: bool,
) -> VerificationCheck:
    if not evidence:
        if trusted:
            return VerificationCheck(
                field=label.lower().replace(" ", "_"),
                status=VerificationStatus.UNKNOWN,
                message=f"{label}: not on statement; trusted value from Knowledge Asset",
            )
        return VerificationCheck(
            field=label.lower().replace(" ", "_"),
            status=VerificationStatus.MISSING,
            message=f"{label}: unknown",
        )
    if not trusted:
        return VerificationCheck(
            field=label.lower().replace(" ", "_"),
            status=VerificationStatus.UNKNOWN,
            message=f"{label}: {evidence} — statement only (no Knowledge Asset value)",
        )
    if normalize_text(trusted) == normalize_text(evidence):
        return VerificationCheck(
            field=label.lower().replace(" ", "_"),
            status=VerificationStatus.VERIFIED,
            message=f"{label}: {evidence} — matches Knowledge Asset",
        )
    return VerificationCheck(
        field=label.lower().replace(" ", "_"),
        status=VerificationStatus.MISMATCH,
        message=f"{label}: mismatch between statement and Knowledge Asset",
        critical=critical,
    )


def _compare_optional_text(
    label: str, trusted: str | None, evidence: str | None
) -> VerificationCheck:
    if not evidence and not trusted:
        return VerificationCheck(
            field=label.lower().replace(" ", "_"),
            status=VerificationStatus.MISSING,
            message=f"{label}: not available",
        )
    if evidence and trusted and normalize_text(evidence) != normalize_text(trusted):
        return VerificationCheck(
            field=label.lower().replace(" ", "_"),
            status=VerificationStatus.MISMATCH,
            message=f"{label}: {evidence} — differs from Knowledge Asset ({trusted})",
        )
    value = evidence or trusted or "unknown"
    return VerificationCheck(
        field=label.lower().replace(" ", "_"),
        status=VerificationStatus.VERIFIED if evidence else VerificationStatus.UNKNOWN,
        message=f"{label}: {value}",
    )


def _compare_amount(
    label: str,
    trusted: object | None,
    evidence: object | None,
    payment_amount: object | None,
) -> VerificationCheck:
    trusted_norm = normalize_amount(trusted)
    evidence_norm = normalize_amount(evidence)
    payment_norm = normalize_amount(payment_amount)

    if trusted_norm and payment_norm and trusted_norm != payment_norm:
        return VerificationCheck(
            field="amount",
            status=VerificationStatus.MISMATCH,
            critical=True,
            message="Amount: execution differs from Knowledge Asset",
        )

    if evidence_norm and trusted_norm and evidence_norm == trusted_norm:
        return VerificationCheck(
            field="amount",
            status=VerificationStatus.VERIFIED,
            message=f"{label}: {evidence} — verified against statement",
        )
    if evidence_norm and payment_norm and evidence_norm == payment_norm:
        return VerificationCheck(
            field="amount",
            status=VerificationStatus.VERIFIED,
            message=f"{label}: {evidence} — verified against statement",
        )
    if trusted_norm and payment_norm and trusted_norm == payment_norm and not evidence_norm:
        return VerificationCheck(
            field="amount",
            status=VerificationStatus.UNKNOWN,
            message=f"{label}: {payment_amount} — matches Knowledge Asset (no statement amount)",
        )
    if evidence_norm:
        return VerificationCheck(
            field="amount",
            status=VerificationStatus.MISMATCH,
            message=f"{label}: mismatch between statement, Knowledge Asset, or payment parameters",
            critical=True,
        )
    if trusted_norm or payment_norm:
        display = payment_amount or trusted
        return VerificationCheck(
            field="amount",
            status=VerificationStatus.UNKNOWN,
            message=f"{label}: {display} — from Knowledge Asset / workflow parameters",
        )
    return VerificationCheck(
        field="amount",
        status=VerificationStatus.MISSING,
        message=f"{label}: unknown",
    )


def _verify_bank_account(
    trusted: str | None,
    evidence: str | None,
) -> tuple[VerificationCheck, bool, str | None]:
    if not trusted:
        return (
            VerificationCheck(
                field="bank_account",
                status=VerificationStatus.MISSING,
                message=(
                    "Bank account: no verified account in Knowledge Asset. "
                    "Payment blocked pending review."
                ),
                critical=True,
            ),
            True,
            "No verified bank account in Knowledge Asset. Payment blocked pending review.",
        )

    trusted_norm = normalize_bank_account(trusted)
    if not evidence:
        return (
            VerificationCheck(
                field="bank_account",
                status=VerificationStatus.UNKNOWN,
                message=(
                    "Bank account: not shown on statement; "
                    f"using verified account from Knowledge Asset ({trusted})"
                ),
            ),
            False,
            None,
        )

    evidence_norm = normalize_bank_account(evidence)
    if trusted_norm == evidence_norm:
        return (
            VerificationCheck(
                field="bank_account",
                status=VerificationStatus.VERIFIED,
                message=f"Bank account: {evidence} — matches Knowledge Asset",
            ),
            False,
            None,
        )

    return (
        VerificationCheck(
            field="bank_account",
            status=VerificationStatus.MISMATCH,
            message="Bank account: mismatch between statement and Knowledge Asset",
            critical=True,
        ),
        True,
        "Bank account mismatch between statement and Knowledge Asset. "
        "Payment blocked pending review.",
    )
