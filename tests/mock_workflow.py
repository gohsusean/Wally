"""Mock workflow provider for tests."""

from __future__ import annotations

import json
from typing import Any

from wally.models.actions import ActionClass, PlannedAction
from wally.models.workflow import WorkflowDefinition, WorkflowParameter, WorkflowTriggerResult
from wally.runtime.execution_router import ExecutionCapabilityRouter


class MockWorkflowProvider:
    name = "workflow"

    def __init__(self) -> None:
        self._definitions = [
            WorkflowDefinition(
                name="weekly-backup",
                description="Backup",
                webhook_path="weekly-backup",
                action_class=ActionClass.REVERSIBLE,
            ),
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay bill via bank transfer",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
                parameters=(
                    WorkflowParameter(
                        name="amount", param_type="number", required=True
                    ),
                ),
            ),
        ]
        self._workflows = {wf.name: wf for wf in self._definitions}
        self._router = ExecutionCapabilityRouter(self._definitions)
        self.triggered: list[tuple[str, dict[str, object] | None]] = []

    def is_healthy(self) -> bool:
        return True

    def list_workflows(self) -> list[WorkflowDefinition]:
        return list(self._definitions)

    def trigger(
        self, workflow: str, parameters: dict[str, object] | None = None
    ) -> WorkflowTriggerResult:
        canonical = self._router.resolve_workflow_name(workflow) or workflow
        self.triggered.append((canonical, parameters))
        return WorkflowTriggerResult(workflow=canonical, status="triggered", response={})

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "workflow_list",
                "description": "List workflows",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "function",
                "name": "workflow_trigger",
                "description": "Trigger workflow",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow": {"type": "string"},
                        "parameters": {"type": "object"},
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
            action_class = (
                definition.action_class if definition else ActionClass.IRREVERSIBLE
            )
            action_name = (
                "trigger_financial"
                if action_class == ActionClass.FINANCIAL
                else "trigger"
            )
            return PlannedAction("workflow", action_name, arguments, action_class)
        return None

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "workflow_list":
            return json.dumps(
                {"workflows": [{"name": wf.name} for wf in self.list_workflows()]}
            )
        if tool_name == "workflow_trigger":
            result = self.trigger(
                arguments["workflow"], parameters=arguments.get("parameters")
            )
            return json.dumps({"workflow": result.workflow, "status": result.status})
        raise ValueError(tool_name)
