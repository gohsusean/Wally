"""n8n webhook adapter for WorkflowProvider."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx

from wally.config.loader import Settings
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ActionClass, PlannedAction
from wally.models.workflow import WorkflowDefinition, WorkflowTriggerResult
from wally.runtime.execution_router import ExecutionCapabilityRouter


def _webhook_url(base_url: str, webhook_path: str) -> str:
    """Join base URL and path; avoid double-appending if base already includes the path."""
    path = webhook_path.lstrip("/")
    base = base_url.rstrip("/") + "/"
    if base.rstrip("/").endswith(f"/{path}"):
        return base.rstrip("/")
    return urljoin(base, path)


class N8nWorkflowAdapter:
    """Trigger n8n workflows via webhook URLs defined in config/workflows.yaml."""

    def __init__(
        self,
        *,
        base_url: str,
        workflows: list[WorkflowDefinition],
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._workflows = {wf.name: wf for wf in workflows}
        self._router = ExecutionCapabilityRouter(workflows)
        self._client = httpx.Client(timeout=60.0)

    @property
    def name(self) -> str:
        return "workflow"

    def is_healthy(self) -> bool:
        return bool(self._base_url and self._workflows)

    def list_workflows(self) -> list[WorkflowDefinition]:
        return sorted(self._workflows.values(), key=lambda wf: wf.name)

    def trigger(
        self, workflow: str, parameters: dict[str, object] | None = None
    ) -> WorkflowTriggerResult:
        canonical = self._router.resolve_workflow_name(workflow) or workflow
        definition = self._workflows.get(canonical)
        if definition is None:
            raise ProviderUnavailableError(self.name, f"Unknown workflow: {workflow}")

        if definition.execution_backend == "browser":
            raise ProviderUnavailableError(
                self.name,
                f"Workflow '{workflow}' is executed via browser automation. "
                "Use finance_trigger_payment for card portal payments.",
            )

        missing = [
            param.name
            for param in definition.parameters
            if param.required and (parameters or {}).get(param.name) is None
        ]
        if missing:
            raise ProviderUnavailableError(
                self.name, f"Missing required parameters: {', '.join(missing)}"
            )

        url = _webhook_url(self._base_url, definition.webhook_path)
        payload = {
            "workflow": canonical,
            "parameters": parameters or {},
            "source": "wally",
        }
        try:
            response = self._client.post(url, json=payload)
        except httpx.HTTPError:
            raise ProviderUnavailableError(
                self.name, "Could not reach n8n webhook."
            ) from None

        if response.status_code >= 400:
            raise ProviderUnavailableError(
                self.name,
                f"n8n webhook error {response.status_code}.",
            )

        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"status": "non_json"}

        return WorkflowTriggerResult(
            workflow=canonical,
            status="triggered",
            response=body if isinstance(body, dict) else {"result": body},
        )

    def tool_definitions(self) -> list[dict[str, Any]]:
        workflow_names = sorted(self._workflows)
        return [
            {
                "type": "function",
                "name": "workflow_list",
                "description": "List workflows Wally can trigger.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "function",
                "name": "workflow_trigger",
                "description": "Trigger a version-controlled n8n workflow.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow": {
                            "type": "string",
                            "enum": workflow_names,
                            "description": "Stable workflow name from config/workflows.yaml",
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Workflow-specific parameters",
                        },
                    },
                    "required": ["workflow"],
                },
            },
        ]

    def planned_action_for_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> PlannedAction | None:
        if tool_name == "workflow_list":
            return PlannedAction("workflow", "list", arguments, ActionClass.READ)
        if tool_name == "workflow_trigger":
            workflow = str(arguments.get("workflow", ""))
            canonical = self._router.resolve_workflow_name(workflow) or workflow
            definition = self._workflows.get(canonical)
            if definition is None:
                return PlannedAction(
                    "workflow",
                    "trigger",
                    arguments,
                    ActionClass.IRREVERSIBLE,
                )
            action_name = (
                "trigger_financial"
                if definition.action_class == ActionClass.FINANCIAL
                else "trigger"
            )
            return PlannedAction(
                "workflow",
                action_name,
                arguments,
                definition.action_class,
            )
        return None

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "workflow_list":
            workflows = [
                {
                    "name": wf.name,
                    "description": wf.description,
                    "action_class": wf.action_class.value,
                    "parameters": [
                        {
                            "name": p.name,
                            "type": p.param_type,
                            "required": p.required,
                            "description": p.description,
                        }
                        for p in wf.parameters
                    ],
                }
                for wf in self.list_workflows()
            ]
            return json.dumps({"workflows": workflows})
        if tool_name == "workflow_trigger":
            result = self.trigger(
                arguments["workflow"],
                parameters=arguments.get("parameters"),
            )
            return json.dumps(
                {
                    "workflow": result.workflow,
                    "status": result.status,
                    "response": result.response,
                }
            )
        raise ValueError(f"Unknown tool: {tool_name}")

    def close(self) -> None:
        self._client.close()


def create_workflow_provider(settings: Settings) -> N8nWorkflowAdapter | None:
    if not settings.workflow_enabled:
        return None
    if settings.workflow_adapter != "n8n":
        raise ProviderUnavailableError(
            "workflow", f"Unsupported adapter: {settings.workflow_adapter}"
        )
    if not settings.n8n_webhook_base_url:
        raise ProviderUnavailableError(
            "workflow", "N8N_WEBHOOK_BASE_URL is not set."
        )
    if not settings.workflows:
        raise ProviderUnavailableError(
            "workflow",
            "No workflows configured in config/workflows.yaml.",
        )
    return N8nWorkflowAdapter(
        base_url=settings.n8n_webhook_base_url,
        workflows=list(settings.workflows),
    )
