"""Execution capability routing tests."""

from wally.models.actions import ActionClass
from wally.models.workflow import WorkflowDefinition, WorkflowParameter
from wally.runtime.execution_router import (
    ExecutionCapabilityRouter,
    prepare_finance_payment_arguments,
)


def _bank_transfer_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="pay-bill-bank-transfer",
        description="Bank transfer",
        webhook_path="pay-bill-bank-transfer",
        action_class=ActionClass.FINANCIAL,
        capability_domain="payment",
        capability="bank_transfer",
        parameters=(WorkflowParameter(name="amount", param_type="number", required=True),),
    )


def test_resolve_payment_defaults_to_bank_transfer() -> None:
    router = ExecutionCapabilityRouter([_bank_transfer_workflow()])
    resolution, error = router.resolve_payment(
        {
            "provider": "TM110",
            "amount": "RM356.40",
            "bank_account": "123456789",
        }
    )
    assert error is None
    assert resolution is not None
    assert resolution.workflow == "pay-bill-bank-transfer"
    assert resolution.payment_method == "bank_transfer"
    assert resolution.parameters["amount"] == "RM356.40"
    assert resolution.parameters["bank_account"] == "123456789"


def test_resolve_payment_honours_explicit_payment_method() -> None:
    router = ExecutionCapabilityRouter([_bank_transfer_workflow()])
    resolution, error = router.resolve_payment(
        {"provider": "TM110", "payment_method": "bank_transfer", "amount": 100}
    )
    assert error is None
    assert resolution is not None
    assert resolution.workflow == "pay-bill-bank-transfer"


def test_resolve_workflow_name_returns_canonical_name() -> None:
    router = ExecutionCapabilityRouter([_bank_transfer_workflow()])
    assert router.resolve_workflow_name("pay-bill-bank-transfer") == "pay-bill-bank-transfer"
    assert router.resolve_workflow_name("unknown-workflow") is None


def test_prepare_finance_payment_injects_runtime_workflow() -> None:
    router = ExecutionCapabilityRouter([_bank_transfer_workflow()])
    prepared, error = prepare_finance_payment_arguments(
        {
            "bill": {
                "provider": "Electricity Co",
                "amount": "142.50",
                "bank_account": "123456789",
            },
            "parameters": {"amount": "142.50"},
        },
        router,
    )
    assert error is None
    assert prepared["workflow"] == "pay-bill-bank-transfer"
    assert prepared["_payment_resolution"]["payment_method"] == "bank_transfer"
    assert prepared["parameters"]["amount"] == "142.50"
    assert prepared["parameters"]["provider"] == "Electricity Co"


def test_unknown_payment_method_returns_error() -> None:
    router = ExecutionCapabilityRouter([_bank_transfer_workflow()])
    resolution, error = router.resolve_payment(
        {"provider": "TM110", "payment_method": "card_portal"}
    )
    assert resolution is None
    assert error is not None
    assert "card_portal" in error
