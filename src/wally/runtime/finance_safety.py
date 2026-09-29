"""Finance-specific safety rules — bill paid evidence and approval summaries."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from wally.models.verification import VerificationReport
from wally.providers.knowledge import KnowledgeProvider
from wally.runtime.policy import PolicyDecision
from wally.runtime.verification_engine import VerificationEngine, format_verification_summary

PAID_MARKERS = re.compile(
    r"\b("
    r"paid|payment\s+complete|payment\s+sent|mark(?:ed)?\s+as\s+paid|"
    r"status:\s*paid|settled|cleared"
    r")\b",
    re.IGNORECASE,
)

VALID_EVIDENCE_TYPES = frozenset(
    {"workflow_success", "user_confirmation", "verification_provider"}
)


class PaymentEvidenceType(StrEnum):
    WORKFLOW_SUCCESS = "workflow_success"
    USER_CONFIRMATION = "user_confirmation"
    VERIFICATION_PROVIDER = "verification_provider"


def looks_like_marking_bill_paid(*, title: str | None, content: str | None) -> bool:
    """Return True when text appears to record a bill as paid."""
    combined = " ".join(part for part in (title, content) if part)
    if not combined.strip():
        return False
    return bool(PAID_MARKERS.search(combined))


def _valid_payment_evidence(evidence: object) -> bool:
    if not isinstance(evidence, dict):
        return False
    evidence_type = str(evidence.get("type", "")).strip()
    if evidence_type not in VALID_EVIDENCE_TYPES:
        return False
    if evidence_type == PaymentEvidenceType.WORKFLOW_SUCCESS:
        status = str(evidence.get("workflow_status", "")).lower()
        return bool(str(evidence.get("workflow", "")).strip()) and status in {
            "success",
            "triggered",
            "completed",
        }
    if evidence_type == PaymentEvidenceType.USER_CONFIRMATION:
        return bool(evidence.get("user_confirmed") is True)
    if evidence_type == PaymentEvidenceType.VERIFICATION_PROVIDER:
        return bool(str(evidence.get("provider", "")).strip())
    return False


def evaluate_bill_paid_write_policy(
    provider: KnowledgeProvider,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    finance_bills_role: str,
) -> PolicyDecision:
    """Block marking bills paid in knowledge without acceptable payment evidence."""
    if tool_name not in {"knowledge_create", "knowledge_update"}:
        return PolicyDecision(allowed=True)

    title = arguments.get("title")
    content = arguments.get("content")
    if not looks_like_marking_bill_paid(
        title=str(title) if title is not None else None,
        content=str(content) if content is not None else None,
    ):
        return PolicyDecision(allowed=True)

    target = provider.resolve_write_target(tool_name, arguments)
    if target is None:
        return PolicyDecision(allowed=True)

    is_finance_target = target.role == finance_bills_role
    if tool_name == "knowledge_create":
        role = str(arguments.get("role", "general"))
        is_finance_target = is_finance_target or role == finance_bills_role

    if not is_finance_target:
        return PolicyDecision(allowed=True)

    if _valid_payment_evidence(arguments.get("payment_evidence")):
        return PolicyDecision(allowed=True)

    return PolicyDecision(
        allowed=False,
        reason=(
            "Cannot mark a bill as paid without payment evidence. "
            "Provide payment_evidence with type workflow_success (after a successful "
            "n8n result), user_confirmation (explicit user confirmation), or "
            "verification_provider (future read-only financial verification)."
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
    return engine.verify_bill_payment(
        trusted=bill,
        evidence=statement if isinstance(statement, dict) else None,
        payment_amount=params.get("amount"),
        payment_method=str(payment_method) if payment_method else None,
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
        resolution.get("payment_method")
        or _field(bill, "payment_method")
        or "bank_transfer"
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
        f"  Due date: {due_date}",
        f"  Amount source: {amount_source}",
        f"  Payee / destination: {payee}",
        f"  Payment method: {payment_method}",
    ]
    if workflow != "unknown":
        lines.append(f"  Execution capability: {workflow} (runtime-selected)")
    if verification.blocked:
        lines.extend(["", "Payment blocked pending review."])
    if params and params != {"amount": amount}:
        lines.append(f"  Workflow parameters: {params}")
    lines.append(
        "  Note: Triggering payment does not mark the bill paid in knowledge."
    )
    return "\n".join(lines)


def _field(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    if value is None or value == "":
        return None
    return str(value)
