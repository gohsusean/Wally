"""VerificationEngine tests."""

from wally.audit.logger import AuditLogger
from wally.models.actions import ToolCall
from wally.models.verification import VerificationStatus
from wally.orchestrator.tools import ToolRegistry
from wally.runtime.finance_safety import (
    evaluate_finance_verification_policy,
    format_finance_approval_summary,
    verify_finance_payment,
)
from wally.runtime.verification_engine import VerificationEngine, normalize_bank_account
from wally.safety.gates import ApprovalGate


def _trusted_bill(**overrides) -> dict:
    base = {
        "provider": "TM110 Management Corporation",
        "payee": "TM110 Management Corporation",
        "amount": "RM356.40",
        "due_date": "15 Jul 2026",
        "bank_account": "123456789",
    }
    base.update(overrides)
    if "bank_account" in overrides and overrides["bank_account"] is None:
        del base["bank_account"]
    return base


def _payment_args(**overrides) -> dict:
    base = {
        "workflow": "pay-bill-bank-transfer",
        "_payment_resolution": {"payment_method": "bank_transfer"},
        "parameters": {"amount": "RM356.40"},
        "bill": _trusted_bill(),
        "statement": {
            "payee": "TM110 Management Corporation",
            "amount": "RM356.40",
            "due_date": "15 Jul 2026",
            "bank_account": "123456789",
            "source": "email statement",
        },
    }
    base.update(overrides)
    return base


def test_normalize_bank_account_ignores_formatting() -> None:
    assert normalize_bank_account("123-456-789") == "123456789"
    assert normalize_bank_account("123 456 789") == "123456789"
    assert normalize_bank_account("123.456.789") == "123456789"
    assert normalize_bank_account("123/456/789") == "123456789"


def test_bank_account_formatting_only_does_not_mismatch() -> None:
    engine = VerificationEngine()
    report = engine.verify_bill_payment(
        trusted=_trusted_bill(),
        evidence={"bank_account": "123-456-789"},
    )
    bank = next(c for c in report.checks if c.field == "bank_account")
    assert bank.status == VerificationStatus.VERIFIED
    assert not report.blocked


def test_bank_account_matches_knowledge_asset_approval_can_proceed() -> None:
    report = verify_finance_payment(_payment_args())
    assert not report.blocked
    assert evaluate_finance_verification_policy(report).allowed
    bank = next(c for c in report.checks if c.field == "bank_account")
    assert bank.status == VerificationStatus.VERIFIED
    assert "matches Knowledge Asset" in bank.message


def test_bank_account_missing_on_statement_proceeds_with_warning() -> None:
    report = verify_finance_payment(
        _payment_args(statement={"amount": "RM356.40", "payee": "TM110 Management Corporation"})
    )
    assert not report.blocked
    assert evaluate_finance_verification_policy(report).allowed
    bank = next(c for c in report.checks if c.field == "bank_account")
    assert bank.status == VerificationStatus.UNKNOWN
    assert "not shown on statement" in bank.message
    assert "Knowledge Asset" in bank.message


def test_bank_account_mismatch_blocks_payment() -> None:
    report = verify_finance_payment(
        _payment_args(statement={"bank_account": "987654321", "amount": "RM356.40"})
    )
    assert report.blocked
    policy = evaluate_finance_verification_policy(report)
    assert not policy.allowed
    assert "mismatch" in (policy.reason or "").lower()
    bank = next(c for c in report.checks if c.field == "bank_account")
    assert bank.status == VerificationStatus.MISMATCH


def test_missing_trusted_bank_account_blocks_payment() -> None:
    report = verify_finance_payment(
        _payment_args(bill=_trusted_bill(bank_account=None), statement={})
    )
    assert report.blocked
    policy = evaluate_finance_verification_policy(report)
    assert not policy.allowed
    assert "Knowledge Asset" in (policy.reason or "")


def test_verification_summary_in_approval_prompt() -> None:
    summary = format_finance_approval_summary(_payment_args())
    assert "Verification:" in summary
    assert "Amount:" in summary
    assert "verified against statement" in summary
    assert "matches Knowledge Asset" in summary
    assert "pay-tm110-maintenance" not in summary or "Execution capability" in summary


def test_card_portal_skips_bank_account_verification() -> None:
    engine = VerificationEngine()
    report = engine.verify_bill_payment(
        trusted={
            "provider": "Streaming Co",
            "payment_method": "card_portal",
            "payment_portal_url": "https://pay.example.com/streaming",
            "amount": "19.99",
            "account_reference": "ACC-42",
        },
        evidence={
            "provider": "Streaming Co",
            "payment_portal_url": "https://pay.example.com/streaming",
            "amount": "19.99",
            "account_reference": "ACC-42",
        },
        payment_method="card_portal",
    )
    assert not report.blocked
    fields = {check.field for check in report.checks}
    assert "bank_account" not in fields
    assert "payment_portal_url" in fields or any(
        "portal" in check.message.lower() for check in report.checks
    )


def test_tool_registry_blocks_before_approval_on_verification_mismatch(tmp_path) -> None:
    from tests.test_finance import _MockKnowledge, _MockWorkflow, _settings
    from wally.adapters.finance.local import LocalFinanceAdapter
    from wally.models.actions import ActionClass
    from wally.models.workflow import WorkflowDefinition

    finance = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=_MockWorkflow(
            workflows=[
                WorkflowDefinition(
                    name="pay-bill-bank-transfer",
                    description="Pay",
                    webhook_path="pay-bill-bank-transfer",
                    action_class=ActionClass.FINANCIAL,
                    capability_domain="payment",
                    capability="bank_transfer",
                    parameters=(),
                )
            ]
        ),
    )
    captured: list[str] = []

    class _CapturingApproval:
        def request_approval(self, summary: str, *, action_class: str) -> bool:
            captured.append(summary)
            return True

    registry = ToolRegistry(
        providers={"finance": finance},
        knowledge=None,
        finance=finance,
        finance_bills_role="finance",
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
        approval=_CapturingApproval(),
        audit=AuditLogger(tmp_path / "audit"),
        dry_run=False,
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="finance_trigger_payment",
            arguments=_payment_args(
                statement={"bank_account": "000000000", "amount": "RM356.40"}
            ),
        )
    )
    assert result.denied
    assert "verification_blocked" in (result.action_taken or "")
    assert not captured
