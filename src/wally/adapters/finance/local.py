"""Local finance adapter — composes Knowledge and Workflow providers."""

from __future__ import annotations

import json
from typing import Any

from wally.config.loader import Settings
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ActionClass, PlannedAction
from wally.models.finance import BillSummary, PaymentWorkflowSummary
from wally.models.knowledge import KnowledgeAsset
from wally.providers.knowledge import KnowledgeProvider
from wally.providers.workflow import WorkflowProvider
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.execution_router import (
    ExecutionCapabilityRouter,
    prepare_finance_payment_arguments,
)
from wally.runtime.secret_resolver import GovernedSecretsResolver


def _asset_to_bill(asset: KnowledgeAsset) -> BillSummary:
    return BillSummary(
        asset_id=asset.id,
        title=asset.title,
        content=asset.content,
        database=asset.database,
        domain=asset.domain,
    )


class LocalFinanceAdapter:
    """Finance tools backed by Notion knowledge and n8n payment workflows."""

    def __init__(
        self,
        *,
        settings: Settings,
        knowledge: KnowledgeProvider,
        workflow: WorkflowProvider | None,
        browser_executor: GovernedBrowserExecutor | None = None,
        secrets: GovernedSecretsResolver | None = None,
    ) -> None:
        self._settings = settings
        self._knowledge = knowledge
        self._workflow = workflow
        self._browser_executor = browser_executor
        self._secrets = secrets
        self._bills_role = settings.finance_bills_role

    @property
    def name(self) -> str:
        return "finance"

    def is_healthy(self) -> bool:
        return self._knowledge.is_healthy()

    def search_bills(self, query: str, *, limit: int = 10) -> list[BillSummary]:
        result = self._knowledge.retrieve(
            query,
            role=self._bills_role,
            limit=limit,
        )
        return [_asset_to_bill(asset) for asset in result.assets]

    def list_payment_workflows(self) -> list[PaymentWorkflowSummary]:
        if self._workflow is None:
            return []
        summaries: list[PaymentWorkflowSummary] = []
        for definition in self._workflow.list_workflows():
            if definition.action_class != ActionClass.FINANCIAL:
                continue
            summaries.append(
                PaymentWorkflowSummary(
                    name=definition.name,
                    description=definition.description,
                    parameters=tuple(param.name for param in definition.parameters),
                    capability_domain=definition.capability_domain,
                    capability=definition.capability,
                )
            )
        return summaries

    def payment_router(self) -> ExecutionCapabilityRouter:
        workflows = self._workflow.list_workflows() if self._workflow else []
        return ExecutionCapabilityRouter(workflows)

    def prepare_payment(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Resolve execution capability and merge provider parameters (runtime routing)."""
        return prepare_finance_payment_arguments(arguments, self.payment_router())

    def trigger_payment(
        self,
        workflow: str,
        parameters: dict[str, object] | None = None,
        *,
        bill: dict[str, Any] | None = None,
        payment_method: str | None = None,
        authorized: bool = False,
    ) -> dict[str, object]:
        backend = self.payment_router().execution_backend(workflow)
        if backend == "browser":
            if self._browser_executor is None:
                raise ProviderUnavailableError(
                    self.name,
                    "Card portal payments require browser automation. "
                    "Enable providers.browser in config.",
                )
            return self._browser_executor.run_card_portal_payment(
                bill=bill or {},
                parameters=parameters,
                authorized=authorized,
            )

        if self._workflow is None:
            raise ProviderUnavailableError(
                self.name,
                "Workflow provider is not configured. Enable workflows for payments.",
            )
        resolved_parameters = dict(parameters or {})
        injected_names: list[str] = []
        secret_values: tuple[str, ...] = ()
        if self._secrets is not None:
            from wally.runtime.secrets_safety import scrub_data, workflow_secret_refs

            refs = workflow_secret_refs(bill or {})
            injected_names = list(refs)
            if refs:
                resolved_parameters = self._secrets.inject_workflow_parameters(
                    knowledge=bill or {},
                    parameters=parameters,
                    authorized=authorized,
                )
                secret_values = tuple(
                    str(resolved_parameters[name])
                    for name in injected_names
                    if name in resolved_parameters
                )
        result = self._workflow.trigger(workflow, parameters=resolved_parameters)
        return {
            "workflow": result.workflow,
            "status": result.status,
            "response": scrub_data(result.response, secret_values)
            if secret_values
            else result.response,
            "execution": "n8n",
            "payment_method": payment_method,
            "secret_parameter_names": injected_names,
        }

    def resume_browser_payment(self, session_id: str) -> dict[str, object]:
        if self._browser_executor is None:
            raise ProviderUnavailableError(
                self.name,
                "Browser automation is not configured.",
            )
        return self._browser_executor.resume_card_portal_session(session_id.strip())

    def cancel_browser_payment(self, session_id: str) -> dict[str, object]:
        if self._browser_executor is None:
            raise ProviderUnavailableError(
                self.name,
                "Browser automation is not configured.",
            )
        return self._browser_executor.cancel_card_portal_session(session_id.strip())

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        if tool_name == "finance_bills_search":
            return PlannedAction("finance", "bills_search", arguments, ActionClass.READ)
        if tool_name == "finance_payment_workflows":
            return PlannedAction(
                "finance", "payment_workflows", arguments, ActionClass.READ
            )
        if tool_name == "finance_trigger_payment":
            return PlannedAction(
                "finance", "trigger_payment", arguments, ActionClass.FINANCIAL
            )
        if tool_name == "finance_browser_resume":
            return PlannedAction(
                "finance", "browser_resume", arguments, ActionClass.READ
            )
        if tool_name == "finance_browser_cancel":
            return PlannedAction(
                "finance", "browser_cancel", arguments, ActionClass.READ
            )
        return None

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "finance_bills_search",
                "description": (
                    "Search personal knowledge for bills, due dates, payment amounts, "
                    "and recurring charges. Use for questions like 'what bills are due' "
                    "or 'electricity bill amount'. Reads from Notion — not web search."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Bill name, vendor, or topic (e.g. electricity)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default 10)",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "type": "function",
                "name": "finance_payment_workflows",
                "description": (
                    "List registered payment execution capabilities (for debugging). "
                    "Runtime selects the capability from provider payment_method — "
                    "do not pass workflow names to finance_trigger_payment."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "function",
                "name": "finance_trigger_payment",
                "description": (
                    "Trigger an approval-gated bill payment. Runtime selects the execution "
                    "capability from provider payment_method in the trusted bill object. "
                    "Include trusted bill data from Knowledge and statement evidence. "
                    "Verification runs before approval."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "parameters": {
                            "type": "object",
                            "description": "Optional payment overrides (e.g. amount)",
                        },
                        "bill": {
                            "type": "object",
                            "description": (
                                "Trusted provider/bill data from Knowledge Provider "
                                "(includes payment_method, bank_account, portal URL, etc.)"
                            ),
                            "properties": {
                                "provider": {"type": "string"},
                                "payee": {"type": "string"},
                                "amount": {"type": "string"},
                                "due_date": {"type": "string"},
                                "payment_method": {
                                    "type": "string",
                                    "description": (
                                        "How this provider is paid: bank_transfer, "
                                        "card_portal, api, direct_debit"
                                    ),
                                },
                                "bank_account": {"type": "string"},
                                "payment_portal_url": {"type": "string"},
                                "payment_reference_type": {"type": "string"},
                                "account_reference": {"type": "string"},
                                "amount_source": {"type": "string"},
                                "destination": {"type": "string"},
                                "asset_id": {"type": "string"},
                            },
                        },
                        "statement": {
                            "type": "object",
                            "description": (
                                "External statement/email evidence (LLM-extracted fields). "
                                "Not authoritative — compared against bill by VerificationEngine."
                            ),
                            "properties": {
                                "provider": {"type": "string"},
                                "payee": {"type": "string"},
                                "amount": {"type": "string"},
                                "due_date": {"type": "string"},
                                "bank_account": {"type": "string"},
                                "payment_portal_url": {"type": "string"},
                                "account_reference": {"type": "string"},
                                "source": {"type": "string"},
                            },
                        },
                    },
                    "required": ["bill"],
                },
            },
            {
                "type": "function",
                "name": "finance_browser_resume",
                "description": (
                    "Resume a card-portal payment after manual login. "
                    "Use the session_id returned when finance_trigger_payment "
                    "was waiting_for_user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Browser session ID from the waiting payment",
                        },
                    },
                    "required": ["session_id"],
                },
            },
            {
                "type": "function",
                "name": "finance_browser_cancel",
                "description": (
                    "Cancel a pending card-portal browser session without completing payment."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Browser session ID to close",
                        },
                    },
                    "required": ["session_id"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "finance_bills_search":
            query = str(arguments.get("query", "")).strip()
            if not query:
                return json.dumps({"error": "query is required"})
            limit = int(arguments.get("limit", 10))
            bills = self.search_bills(query, limit=limit)
            return json.dumps(
                {
                    "query": query,
                    "bills": [bill.to_dict() for bill in bills],
                    "source": "knowledge",
                    "note": (
                        "Bill records from personal knowledge. "
                        "Verify amounts before triggering payment."
                    ),
                }
            )

        if tool_name == "finance_payment_workflows":
            workflows = self.list_payment_workflows()
            return json.dumps(
                {
                    "workflows": [workflow.to_dict() for workflow in workflows],
                    "approval_required": True,
                }
            )

        if tool_name == "finance_trigger_payment":
            prepared, error = self.prepare_payment(arguments)
            if error:
                return json.dumps({"error": error})
            workflow = str(prepared.get("workflow", "")).strip()
            bill = prepared.get("bill") if isinstance(prepared.get("bill"), dict) else {}
            resolution = prepared.get("_payment_resolution") or {}
            try:
                result = self.trigger_payment(
                    workflow,
                    parameters=prepared.get("parameters"),
                    bill=bill,
                    payment_method=str(resolution.get("payment_method", "")) or None,
                    authorized=True,
                )
            except ProviderUnavailableError as exc:
                return json.dumps({"error": exc.reason})
            result["bill_paid_not_updated"] = True
            resolution = prepared.get("_payment_resolution") or {}
            if resolution:
                result["payment_method"] = resolution.get("payment_method")
                result["execution_capability"] = prepared.get("workflow")
            result["note"] = (
                "Payment workflow triggered. Do not mark the bill paid in knowledge "
                "unless payment_evidence is available (workflow_success, "
                "user_confirmation, or verification_provider)."
            )
            return json.dumps(result)

        if tool_name == "finance_browser_resume":
            session_id = str(arguments.get("session_id", "")).strip()
            if not session_id:
                return json.dumps({"error": "session_id is required"})
            try:
                result = self.resume_browser_payment(session_id)
            except ProviderUnavailableError as exc:
                return json.dumps({"error": exc.reason})
            return json.dumps(result)

        if tool_name == "finance_browser_cancel":
            session_id = str(arguments.get("session_id", "")).strip()
            if not session_id:
                return json.dumps({"error": "session_id is required"})
            try:
                result = self.cancel_browser_payment(session_id)
            except ProviderUnavailableError as exc:
                return json.dumps({"error": exc.reason})
            return json.dumps(result)

        return json.dumps({"error": f"Unknown tool: {tool_name}"})


def create_finance_provider(
    settings: Settings,
    *,
    knowledge: KnowledgeProvider | None,
    workflow: WorkflowProvider | None,
    browser_executor: GovernedBrowserExecutor | None = None,
    secrets: GovernedSecretsResolver | None = None,
) -> LocalFinanceAdapter | None:
    if not settings.finance_enabled:
        return None
    if settings.finance_adapter != "local":
        raise ProviderUnavailableError(
            "finance", f"Unsupported adapter: {settings.finance_adapter}"
        )
    if knowledge is None:
        raise ProviderUnavailableError(
            "finance",
            "Finance requires the knowledge provider. Enable knowledge and NOTION_API_KEY.",
        )
    return LocalFinanceAdapter(
        settings=settings,
        knowledge=knowledge,
        workflow=workflow,
        browser_executor=browser_executor,
        secrets=secrets,
    )
