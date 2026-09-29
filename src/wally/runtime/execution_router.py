"""Deterministic routing from provider metadata to execution capabilities."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from wally.models.execution_capability import (
    DEFAULT_PAYMENT_METHOD,
    CapabilityDomain,
    PaymentMethod,
)
from wally.models.finance import PaymentResolution
from wally.models.workflow import WorkflowDefinition


class ExecutionCapabilityRouter:
    """Map trusted provider metadata to registered execution capabilities."""

    def __init__(self, workflows: Sequence[WorkflowDefinition]) -> None:
        self._workflows = {workflow.name: workflow for workflow in workflows}
        self._aliases: dict[str, str] = {}
        self._payment_capabilities: dict[str, WorkflowDefinition] = {}
        for workflow in workflows:
            for alias in workflow.aliases:
                self._aliases[alias] = workflow.name
            if (
                workflow.capability_domain == CapabilityDomain.PAYMENT
                and workflow.capability
            ):
                self._payment_capabilities[workflow.capability] = workflow

    def resolve_workflow_name(self, name: str) -> str | None:
        """Resolve canonical workflow name."""
        if name in self._workflows:
            return name
        return self._aliases.get(name)

    def get_workflow(self, name: str) -> WorkflowDefinition | None:
        canonical = self.resolve_workflow_name(name) or name
        return self._workflows.get(canonical)

    def execution_backend(self, workflow_name: str) -> str:
        definition = self.get_workflow(workflow_name)
        if definition is None:
            return "n8n"
        return definition.execution_backend

    def resolve_payment(
        self,
        bill: dict[str, Any],
        *,
        workflow_override: str | None = None,
        extra_parameters: dict[str, object] | None = None,
    ) -> tuple[PaymentResolution | None, str | None]:
        """Select payment execution capability from provider knowledge."""
        payment_method = _normalize_payment_method(bill.get("payment_method"))

        if workflow_override:
            canonical = self.resolve_workflow_name(workflow_override.strip())
            if canonical is None:
                return None, (
                    f"Workflow '{workflow_override}' is not registered. "
                    "Runtime selects execution capabilities from provider payment_method."
                )
            workflow = self._workflows[canonical]
            if workflow.capability and workflow.capability != payment_method:
                return None, (
                    f"Workflow '{workflow_override}' does not match provider "
                    f"payment_method '{payment_method}'."
                )
        else:
            workflow = self._payment_capabilities.get(payment_method)
            if workflow is None:
                return None, (
                    f"No execution capability registered for payment method "
                    f"'{payment_method}'."
                )

        parameters = _build_payment_parameters(bill, extra_parameters)
        return (
            PaymentResolution(
                workflow=workflow.name,
                payment_method=payment_method,
                capability_domain=workflow.capability_domain or CapabilityDomain.PAYMENT,
                capability=workflow.capability or payment_method,
                parameters=parameters,
            ),
            None,
        )


def prepare_finance_payment_arguments(
    arguments: dict[str, Any],
    router: ExecutionCapabilityRouter,
) -> tuple[dict[str, Any], str | None]:
    """Inject runtime-selected workflow and merged parameters into payment arguments."""
    bill = arguments.get("bill")
    if not isinstance(bill, dict) or not bill:
        return arguments, "Trusted bill data from Knowledge Provider is required."

    workflow_override = str(arguments.get("workflow", "")).strip() or None
    resolution, error = router.resolve_payment(
        bill,
        workflow_override=workflow_override,
        extra_parameters=arguments.get("parameters"),
    )
    if error or resolution is None:
        return arguments, error or "Could not resolve payment execution capability."

    merged = {
        **arguments,
        "workflow": resolution.workflow,
        "parameters": resolution.parameters,
        "_payment_resolution": {
            "payment_method": resolution.payment_method,
            "capability_domain": resolution.capability_domain,
            "capability": resolution.capability,
        },
    }
    return merged, None


def _normalize_payment_method(value: object | None) -> str:
    if value is None or str(value).strip() == "":
        return DEFAULT_PAYMENT_METHOD
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for method in PaymentMethod:
        if normalized == method.value:
            return method.value
    return normalized


def _build_payment_parameters(
    bill: dict[str, Any],
    extra: dict[str, object] | None,
) -> dict[str, object]:
    """Merge provider-specific fields for n8n — business logic stays in Wally."""
    parameters: dict[str, object] = {}
    if extra:
        parameters.update(extra)

    for key in (
        "amount",
        "provider",
        "payee",
        "destination",
        "due_date",
        "bank_account",
        "account_reference",
        "payment_portal_url",
        "payment_reference_type",
        "asset_id",
    ):
        value = bill.get(key)
        if value is not None and str(value).strip() and key not in parameters:
            parameters[key] = value

    if "account" not in parameters:
        account_ref = bill.get("account_reference") or bill.get("account")
        if account_ref is not None and str(account_ref).strip():
            parameters["account"] = account_ref

    return parameters
