"""Finance policy tests."""

from tests.test_finance import _MockWorkflow
from wally.models.actions import ActionClass
from wally.models.workflow import WorkflowDefinition, WorkflowParameter
from wally.runtime.policy import evaluate_finance_policy


def test_finance_payment_allowed_for_registered_workflow() -> None:
    finance = _MockWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
                parameters=(
                    WorkflowParameter(
                        name="amount", param_type="number", description="", required=True
                    ),
                ),
            )
        ]
    )
    from tests.test_finance import _MockKnowledge, _settings
    from wally.adapters.finance.local import LocalFinanceAdapter

    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=finance,
    )
    decision = evaluate_finance_policy(
        adapter,
        "finance_trigger_payment",
        {
            "workflow": "pay-bill-bank-transfer",
            "bill": {"provider": "Electricity Co", "amount": "100", "bank_account": "123"},
            "parameters": {"amount": 100},
        },
    )
    assert decision.allowed


def test_finance_payment_denied_for_unknown_workflow() -> None:
    from tests.test_finance import _MockKnowledge, _settings
    from wally.adapters.finance.local import LocalFinanceAdapter

    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=_MockWorkflow(workflows=[]),
    )
    decision = evaluate_finance_policy(
        adapter,
        "finance_trigger_payment",
        {"workflow": "pay-bill-bank-transfer", "bill": {"provider": "Electricity Co"}},
    )
    assert not decision.allowed
    assert decision.reason is not None
    assert "financial workflow" in decision.reason
