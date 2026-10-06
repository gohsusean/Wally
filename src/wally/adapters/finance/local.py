"""Local finance adapter — composes Knowledge and Workflow providers."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import Any

from wally.config.loader import Settings
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ActionClass, PlannedAction
from wally.models.finance import BillSummary, PaymentWorkflowSummary
from wally.models.knowledge import KnowledgeAsset, KnowledgeClass
from wally.models.principal import Capability, RequestContext
from wally.providers.knowledge import KnowledgeProvider
from wally.providers.workflow import WorkflowProvider
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.execution_router import (
    ExecutionCapabilityRouter,
    canonical_payment_parameters,
    payment_fingerprint,
    prepare_finance_payment_arguments,
)
from wally.runtime.finance_safety import verify_finance_payment
from wally.runtime.principals import PrincipalAuthority
from wally.runtime.secret_resolver import GovernedSecretsResolver
from wally.runtime.secrets_safety import evaluate_secret_reference, workflow_secret_refs
from wally.runtime.verification_engine import normalize_bank_account


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
        """Re-fetch approved finance knowledge. Model fields are assertions, never authority."""
        if set(arguments) - {"bill", "statement", "workflow", "parameters"}:
            return {}, "Unsupported payment arguments."
        supplied = arguments.get("bill")
        if not isinstance(supplied, dict) or not isinstance(supplied.get("asset_id"), str):
            return {}, "A canonical bill asset_id is required."
        if supplied["asset_id"].startswith("fin_"):
            return {}, "D03 financial identities do not authorize legacy payments."
        try:
            asset = self._knowledge.get(supplied["asset_id"])
        except Exception:
            return {}, "Canonical bill knowledge is unavailable."
        if (
            asset.id != supplied["asset_id"]
            or asset.role != self._bills_role
            or asset.knowledge_class != KnowledgeClass.OPERATIONAL
        ):
            return {}, "Payment requires approved operational finance knowledge."
        bill = deepcopy(asset.metadata)
        bill["asset_id"] = asset.id
        if any(key not in bill or value != bill[key] for key, value in supplied.items()):
            return {}, "Supplied bill fields conflict with canonical knowledge."
        if any(
            not isinstance(value, (str, int, float)) or isinstance(value, bool)
            for value in canonical_payment_parameters(bill).values()
        ):
            return {}, "Canonical financial fields must be scalar values."
        if not bill.get("currency") or not (bill.get("payee") or bill.get("provider")):
            return {}, "Canonical payee/provider and currency are required."
        currency = str(bill["currency"]).strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            return {}, "Canonical currency must be a three-letter code."
        bill["currency"] = currency
        try:
            amount_text = str(bill.get("amount", "")).strip()
            prefix = re.match(r"^([A-Za-z]{2,3})\s*", amount_text)
            if prefix:
                if prefix[1].upper() not in {currency, "RM" if currency == "MYR" else currency}:
                    return {}, "Canonical amount currency conflicts with currency field."
                amount_text = amount_text[prefix.end() :]
            amount = Decimal(amount_text.replace(",", ""))
            if not amount.is_finite() or amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            return {}, "Canonical payment amount must be positive."
        refs = workflow_secret_refs(bill)
        if bill.get("workflow_secret_refs") and refs != bill["workflow_secret_refs"]:
            return {}, "Invalid canonical workflow secret references."
        # Credentials must not replace any verified financial field at secret injection.
        if any(
            name not in {"otp", "username", "password", "token", "api_key"}
            or not evaluate_secret_reference(ref).allowed
            for name, ref in refs.items()
        ):
            return {}, "Workflow secrets may only populate credential slots."
        prepared, error = prepare_finance_payment_arguments(
            {**deepcopy(arguments), "bill": bill}, self.payment_router()
        )
        if error:
            return {}, error
        if prepared["_payment_resolution"]["payment_method"] == "bank_transfer" and not (
            normalize_bank_account(bill.get("bank_account") or bill.get("account_number"))
        ):
            return {}, "Canonical bank transfer account is missing or malformed."
        definition = self.payment_router().get_workflow(prepared["workflow"])
        if definition is None:
            return {}, "Canonical execution capability is unavailable."
        prepared["_execution_target"] = asdict(definition)
        return prepared, None

    def execute_payment(
        self,
        arguments: dict[str, Any],
        *,
        context: RequestContext,
        authority: PrincipalAuthority,
        reviewed_fingerprint: str,
    ) -> dict[str, object]:
        authority.authorize(context, Capability.EXECUTE_PROPOSAL)
        prepared, error = self.prepare_payment(arguments)
        if error or payment_fingerprint(prepared) != reviewed_fingerprint:
            raise ProviderUnavailableError("finance", "Payment changed after review; verify again.")
        report = verify_finance_payment(prepared)
        if report.blocked:
            raise ProviderUnavailableError("finance", report.block_reason or "Verification failed.")
        result = self._trigger_payment(
            prepared["workflow"],
            parameters=prepared["parameters"],
            bill=prepared["bill"],
            payment_method=prepared["_payment_resolution"]["payment_method"],
            authorized=True,
        )
        result["bill_paid_not_updated"] = True
        result["action_fingerprint"] = reviewed_fingerprint
        result["note"] = (
            "Dispatch is not payment completion evidence. Knowledge and Matters unchanged."
        )
        return result

    def _trigger_payment(
        self,
        workflow: str,
        parameters: dict[str, object] | None = None,
        *,
        bill: dict[str, Any] | None = None,
        payment_method: str | None = None,
        authorized: bool = False,
    ) -> dict[str, object]:
        if not authorized:
            raise ProviderUnavailableError(
                "finance", "Runtime execution authorization is required."
            )
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
        if workflow_secret_refs(bill or {}) and self._secrets is None:
            raise ProviderUnavailableError(
                "finance", "Configured workflow credentials unavailable."
            )
        if self._secrets is not None:
            from wally.runtime.secrets_safety import scrub_data

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
            return PlannedAction("finance", "payment_workflows", arguments, ActionClass.READ)
        if tool_name == "finance_trigger_payment":
            return PlannedAction("finance", "trigger_payment", arguments, ActionClass.FINANCIAL)
        if tool_name == "finance_browser_resume":
            return PlannedAction("finance", "browser_resume", arguments, ActionClass.READ)
        if tool_name == "finance_browser_cancel":
            return PlannedAction("finance", "browser_cancel", arguments, ActionClass.READ)
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
                    "Request a bill payment using the approved finance asset_id. Runtime "
                    "fetches canonical metadata, selects the capability, verifies the exact "
                    "payload and requires authenticated authority plus fresh human approval. "
                    "Missing metadata or conflicting caller fields fail closed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "parameters": {
                            "type": "object",
                            "description": (
                                "Exact assertions only; cannot override canonical values"
                            ),
                        },
                        "bill": {
                            "type": "object",
                            "description": (
                                "asset_id of the approved finance asset. Runtime re-fetches "
                                "metadata; optional repeated fields must match it exactly."
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
                            "required": ["asset_id"],
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
            return json.dumps({"status": "denied", "error": "Use authenticated runtime review."})

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
