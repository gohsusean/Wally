"""Finance safety rule tests."""

from dataclasses import dataclass

from wally.audit.logger import AuditLogger
from wally.config.loader import NotionDatabaseConfig
from wally.models.actions import ToolCall
from wally.orchestrator.tools import ToolRegistry
from wally.runtime.finance_safety import (
    evaluate_bill_paid_write_policy,
    format_finance_approval_summary,
    looks_like_marking_bill_paid,
)
from wally.runtime.principals import PrincipalAuthority
from wally.safety.gates import ApprovalGate


@dataclass
class _FinanceBillTargetKnowledge:
    role: str = "finance"

    @property
    def name(self) -> str:
        return "knowledge"

    def is_healthy(self) -> bool:
        return True

    def resolve_write_target(self, tool_name: str, arguments: dict) -> NotionDatabaseConfig | None:
        if tool_name == "knowledge_create":
            role = str(arguments.get("role", "general"))
            return NotionDatabaseConfig(
                name="bills",
                id="db-1",
                role=role,
                readable=True,
                writable=True,
            )
        if tool_name == "knowledge_update":
            return NotionDatabaseConfig(
                name="bills",
                id="db-1",
                role=self.role,
                readable=True,
                writable=True,
            )
        return None


def test_looks_like_marking_bill_paid() -> None:
    assert looks_like_marking_bill_paid(title="Electricity", content="Status: paid")
    assert not looks_like_marking_bill_paid(title="Electricity", content="Due 15th")


def test_bill_paid_write_denied_without_evidence() -> None:
    knowledge = _FinanceBillTargetKnowledge()
    decision = evaluate_bill_paid_write_policy(
        knowledge,
        "knowledge_update",
        {
            "asset_id": "bill-1",
            "content": "Paid on 1 July",
        },
        finance_bills_role="finance",
    )
    assert not decision.allowed
    assert "payment evidence" in (decision.reason or "").lower()


def test_bill_paid_write_denied_with_self_asserted_user_confirmation() -> None:
    knowledge = _FinanceBillTargetKnowledge()
    decision = evaluate_bill_paid_write_policy(
        knowledge,
        "knowledge_update",
        {
            "asset_id": "bill-1",
            "content": "Marked as paid",
            "payment_evidence": {
                "type": "user_confirmation",
                "user_confirmed": True,
            },
        },
        finance_bills_role="finance",
    )
    assert not decision.allowed


def test_bill_paid_write_denied_with_self_asserted_workflow_success() -> None:
    knowledge = _FinanceBillTargetKnowledge()
    decision = evaluate_bill_paid_write_policy(
        knowledge,
        "knowledge_update",
        {
            "asset_id": "bill-1",
            "content": "Payment complete",
            "payment_evidence": {
                "type": "workflow_success",
                "workflow": "pay-bill-bank-transfer",
                "workflow_status": "success",
            },
        },
        finance_bills_role="finance",
    )
    assert not decision.allowed


def test_finance_approval_summary_includes_bill_fields() -> None:
    summary = format_finance_approval_summary(
        {
            "workflow": "pay-bill-bank-transfer",
            "_payment_resolution": {"payment_method": "bank_transfer"},
            "parameters": {"amount": 142.5, "account": "ACC-1"},
            "bill": {
                "provider": "Electricity Co",
                "amount": "142.50",
                "due_date": "2026-07-15",
                "amount_source": "Notion asset bill-1",
                "payee": "Electricity Co BPay 12345",
                "bank_account": "123456789",
            },
            "statement": {
                "amount": "142.50",
                "payee": "Electricity Co BPay 12345",
            },
        }
    )
    assert "Electricity Co" in summary
    assert "142.50" in summary
    assert "2026-07-15" in summary
    assert "Notion asset bill-1" in summary
    assert "pay-bill-bank-transfer" in summary
    assert "Payment method: bank_transfer" in summary
    assert "BPay 12345" in summary
    assert "Verification:" in summary
    assert "does not mark the bill paid" in summary


def test_finance_approval_summary_used_by_tool_registry(tmp_path) -> None:
    from tests.test_finance import _bill_knowledge, _MockWorkflow, _settings
    from wally.adapters.finance.local import LocalFinanceAdapter
    from wally.models.actions import ActionClass
    from wally.models.workflow import WorkflowDefinition

    finance = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_bill_knowledge(
            {
                "provider": "Electricity Co",
                "amount": "142.50",
                "due_date": "2026-07-15",
                "amount_source": "finance_bills_search",
                "payee": "Electricity Co",
                "bank_account": "123456789",
            }
        ),
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
            return False

    authority = PrincipalAuthority()
    registry = ToolRegistry(
        providers={"finance": finance},
        knowledge=None,
        finance=finance,
        finance_bills_role="finance",
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
        approval=_CapturingApproval(),
        audit=AuditLogger(tmp_path / "audit"),
        dry_run=False,
        authority=authority,
    )
    registry.execute(
        ToolCall(
            call_id="c1",
            name="finance_trigger_payment",
            arguments={
                "parameters": {"amount": "142.50"},
                "bill": {
                    "asset_id": "bill-1",
                    "provider": "Electricity Co",
                    "amount": "142.50",
                    "due_date": "2026-07-15",
                    "amount_source": "finance_bills_search",
                    "payee": "Electricity Co",
                    "bank_account": "123456789",
                },
                "statement": {"amount": "142.50", "payee": "Electricity Co"},
            },
        ),
        context=authority.issue("repl"),
    )
    assert captured
    assert "Verification:" in captured[0]
    assert "Electricity Co" in captured[0]
    assert "142.50" in captured[0]
