"""Finance-specific safety rules — bill paid evidence and approval summaries."""

from __future__ import annotations

import re
from typing import Any

from wally.models.verification import VerificationCheck, VerificationReport, VerificationStatus
from wally.providers.knowledge import KnowledgeProvider
from wally.runtime.execution_router import canonical_payment_parameters
from wally.runtime.policy import PolicyDecision
from wally.runtime.verification_engine import (
    VerificationEngine,
    format_verification_summary,
    normalize_text,
)

PAID_MARKERS = re.compile(
    r"\b("
    r"paid|payment\s+complete|payment\s+sent|mark(?:ed)?\s+as\s+paid|"
    r"status:\s*paid|settled|cleared"
    r")\b",
    re.IGNORECASE,
)


def looks_like_marking_bill_paid(*, title: str | None, content: str | None) -> bool:
    """Return True when text appears to record a bill as paid."""
    combined = " ".join(part for part in (title, content) if part)
    if not combined.strip():
        return False
    return bool(PAID_MARKERS.search(combined))


def evaluate_bill_paid_write_policy(
    provider: KnowledgeProvider,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    finance_bills_role: str,
) -> PolicyDecision:
    """Model payloads never establish payment evidence; require runtime human verification.

    All finance writes require review: free text cannot reliably distinguish a
    payment-state change from a harmless edit. Paid claims elsewhere do too.
    """
    if tool_name not in {"knowledge_create", "knowledge_update"}:
        return PolicyDecision(allowed=True)

    title = arguments.get("title")
    content = arguments.get("content")
    paid_claim = looks_like_marking_bill_paid(
        title=str(title) if title is not None else None,
        content=str(content) if content is not None else None,
    )

    target = provider.resolve_write_target(tool_name, arguments)
    if target is None:
        return PolicyDecision(allowed=False, reason="Could not resolve knowledge write target.")

    is_finance_target = target.role == finance_bills_role
    if tool_name == "knowledge_create":
        role = str(arguments.get("role", "general"))
        is_finance_target = is_finance_target or role == finance_bills_role

    if not is_finance_target and not paid_claim and "payment_evidence" not in arguments:
        return PolicyDecision(allowed=True)

    return PolicyDecision(
        allowed=False,
        reason=(
            "Payment evidence cannot come from tool arguments. "
            "This write requires authenticated, explicit human verification."
        ),
    )


def verify_finance_payment(arguments: dict[str, Any]) -> VerificationReport:
    """Run VerificationEngine for finance_trigger_payment arguments."""
    bill = arguments.get("bill") or {}
    statement = arguments.get("statement")
    params = arguments.get("parameters") or {}
    resolution = arguments.get("_payment_resolution") or {}
    payment_method = resolution.get("payment_method") or bill.get("payment_method")
    engine = VerificationEngine()
    report = engine.verify_bill_payment(
        trusted=bill,
        evidence=statement if isinstance(statement, dict) else None,
        payment_amount=params.get("amount"),
        payment_method=str(payment_method) if payment_method else None,
    )
    # Check every additional financial identity the provider can receive, rather
    # than checking only the first payee/account alias and silently ignoring others.
    checks = list(report.checks)
    canonical = canonical_payment_parameters(bill)
    for key, value in params.items():
        if key not in canonical or value != canonical[key]:
            checks.append(
                VerificationCheck(
                    field=str(key),
                    status=VerificationStatus.MISMATCH,
                    critical=True,
                    message=f"{key}: execution differs from canonical knowledge",
                )
            )
    for key in (
        "currency",
        "destination",
        "recipient",
        "account",
        "account_reference",
        "account_number",
        "bill_id",
        "asset_id",
        "payment_reference_type",
    ):
        expected = params.get(key, bill.get(key))
        observed = statement.get(key) if isinstance(statement, dict) else None
        if (
            expected is not None
            and observed is not None
            and normalize_text(expected) != normalize_text(observed)
        ):
            checks.append(
                VerificationCheck(
                    field=key,
                    status=VerificationStatus.MISMATCH,
                    critical=True,
                    message=f"{key}: statement differs from canonical execution parameters",
                )
            )
    reason = next(
        (c.message for c in checks if c.critical and c.status == VerificationStatus.MISMATCH), None
    )
    return VerificationReport(
        checks=tuple(checks),
        blocked=report.blocked or reason is not None,
        block_reason=report.block_reason or reason,
    )


def evaluate_finance_verification_policy(report: VerificationReport) -> PolicyDecision:
    if report.blocked:
        return PolicyDecision(
            allowed=False,
            reason=report.block_reason or "Payment blocked pending verification review.",
        )
    return PolicyDecision(allowed=True)


def format_finance_approval_summary(
    arguments: dict[str, Any],
    *,
    verification: VerificationReport | None = None,
) -> str:
    """Build a financial approval prompt with bill context and verification summary."""
    workflow = str(arguments.get("workflow", "unknown"))
    params = arguments.get("parameters") or {}
    bill = arguments.get("bill") or {}
    resolution = arguments.get("_payment_resolution") or {}

    bill_provider = _field(bill, "provider") or _field(bill, "bill_provider") or "unknown"
    amount = _field(bill, "amount") or params.get("amount", "unknown")
    due_date = _field(bill, "due_date") or "unknown"
    amount_source = _field(bill, "amount_source") or "unknown"
    payee = _field(bill, "payee") or _field(bill, "destination") or "unknown"
    payment_method = (
        resolution.get("payment_method") or _field(bill, "payment_method") or "bank_transfer"
    )

    if verification is None:
        verification = verify_finance_payment(arguments)

    lines = [
        "Approve financial payment?",
        "",
        "Verification:",
        *format_verification_summary(verification),
        "",
        "Payment details:",
        f"  Bill / provider: {bill_provider}",
        f"  Amount: {amount}",
        f"  Currency: {_field(bill, 'currency') or 'unknown'}",
        f"  Bill asset: {_field(bill, 'asset_id') or 'unknown'}",
        f"  Due date: {due_date}",
        f"  Amount source: {amount_source}",
        f"  Payee / destination: {payee}",
        f"  Payment method: {payment_method}",
    ]
    if workflow != "unknown":
        lines.append(f"  Execution capability: {workflow} (runtime-selected)")
    target = arguments.get("_execution_target")
    if isinstance(target, dict):
        lines.append(
            f"  Execution target: {target.get('execution_backend')} / {target.get('webhook_path')}"
        )
    if verification.blocked:
        lines.extend(["", "Payment blocked pending review."])
    if params and params != {"amount": amount}:
        lines.append(f"  Workflow parameters: {params}")
    lines.append("  Note: Triggering payment does not mark the bill paid in knowledge.")
    return "\n".join(lines)


def _field(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    if value is None or value == "":
        return None
    return str(value)
