"""Tool registration and execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from wally.audit.logger import AuditLogger
from wally.models.actions import ActionClass, PlannedAction, ToolCall
from wally.providers.approval import ApprovalProvider
from wally.providers.capability import CapabilityProvider
from wally.providers.finance import FinanceProvider
from wally.providers.knowledge import KnowledgeProvider
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.finance_safety import (
    evaluate_bill_paid_write_policy,
    evaluate_finance_verification_policy,
    format_finance_approval_summary,
    verify_finance_payment,
)
from wally.runtime.policy import evaluate_finance_policy, evaluate_knowledge_policy
from wally.safety.classifier import classify_action
from wally.safety.gates import ApprovalGate, GateResult


@dataclass
class ToolExecutionResult:
    call_id: str
    output: str
    action_taken: str | None = None
    denied: bool = False


class ToolRegistry:
    """Registers capability provider tools and executes them through policy and safety gates."""

    def __init__(
        self,
        *,
        providers: dict[str, CapabilityProvider],
        knowledge: KnowledgeProvider | None,
        finance: FinanceProvider | None = None,
        finance_bills_role: str = "finance",
        browser_executor: GovernedBrowserExecutor | None = None,
        gate: ApprovalGate,
        approval: ApprovalProvider,
        audit: AuditLogger,
        dry_run: bool,
        session_id: str = "",
    ) -> None:
        self._providers = providers
        self._knowledge = knowledge
        self._finance = finance
        self._finance_bills_role = finance_bills_role
        self._browser_executor = browser_executor
        self._gate = gate
        self._approval = approval
        self._audit = audit
        self._dry_run = dry_run
        self._session_id = session_id
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {}
        self._tool_providers: dict[str, str] = {}

        for provider_name, provider in providers.items():
            for tool in provider.tool_definitions():
                tool_name = str(tool["name"])
                self._tool_providers[tool_name] = provider_name

                def _handler(
                    args: dict[str, Any],
                    *,
                    name: str = tool_name,
                    cap: CapabilityProvider = provider,
                ) -> str:
                    return cap.execute_tool(name, args)

                self._handlers[tool_name] = _handler

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    @property
    def definitions(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for provider in self._providers.values():
            tools.extend(provider.tool_definitions())
        return tools

    def provider_for(self, tool_name: str) -> str:
        return self._tool_providers.get(tool_name, "unknown")

    def execute(self, call: ToolCall) -> ToolExecutionResult:
        if call.name not in self._handlers:
            return ToolExecutionResult(
                call_id=call.call_id,
                output=json.dumps({"error": f"Unknown tool: {call.name}"}),
            )

        provider_name = self._tool_providers[call.name]
        provider = self._providers[provider_name]

        if provider_name == "knowledge" and self._knowledge is not None:
            policy = evaluate_knowledge_policy(self._knowledge, call.name, call.arguments)
            if not policy.allowed:
                self._audit.log_simple(
                    event_type="policy_denied",
                    session_id=self._session_id,
                    outcome="denied",
                    provider="knowledge",
                    action_type=call.name,
                    parameters={"reason": policy.reason, **call.arguments},
                )
                return ToolExecutionResult(
                    call_id=call.call_id,
                    output=json.dumps({"status": "denied", "message": policy.reason}),
                    action_taken=f"policy_denied:{call.name}",
                    denied=True,
                )
            paid_policy = evaluate_bill_paid_write_policy(
                self._knowledge,
                call.name,
                call.arguments,
                finance_bills_role=self._finance_bills_role,
            )
            if not paid_policy.allowed:
                self._audit.log_simple(
                    event_type="policy_denied",
                    session_id=self._session_id,
                    outcome="denied",
                    provider="finance",
                    action_type=call.name,
                    parameters={"reason": paid_policy.reason, **call.arguments},
                )
                return ToolExecutionResult(
                    call_id=call.call_id,
                    output=json.dumps({"status": "denied", "message": paid_policy.reason}),
                    action_taken=f"policy_denied:{call.name}",
                    denied=True,
                )

        if provider_name == "finance" and self._finance is not None:
            prepared_arguments = call.arguments
            if call.name == "finance_trigger_payment":
                prepared_arguments, routing_error = self._finance.prepare_payment(
                    call.arguments
                )
                if routing_error:
                    self._audit.log_simple(
                        event_type="routing_denied",
                        session_id=self._session_id,
                        outcome="denied",
                        provider="finance",
                        action_type=call.name,
                        parameters={"reason": routing_error, **call.arguments},
                    )
                    return ToolExecutionResult(
                        call_id=call.call_id,
                        output=json.dumps(
                            {"status": "denied", "message": routing_error}
                        ),
                        action_taken=f"routing_denied:{call.name}",
                        denied=True,
                    )
                call = ToolCall(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=prepared_arguments,
                )

            resolution = prepared_arguments.get("_payment_resolution") or {}
            if (
                call.name == "finance_trigger_payment"
                and resolution.get("payment_method") == "card_portal"
                and self._browser_executor is not None
            ):
                bill = prepared_arguments.get("bill")
                if isinstance(bill, dict):
                    portal_policy = self._browser_executor.evaluate_portal_policy(bill)
                    if not portal_policy.allowed:
                        self._audit.log_simple(
                            event_type="browser_policy_denied",
                            session_id=self._session_id,
                            outcome="denied",
                            provider="browser",
                            action_type=call.name,
                            parameters={"reason": portal_policy.reason},
                        )
                        return ToolExecutionResult(
                            call_id=call.call_id,
                            output=json.dumps(
                                {"status": "denied", "message": portal_policy.reason}
                            ),
                            action_taken=f"browser_policy_denied:{call.name}",
                            denied=True,
                        )

            policy = evaluate_finance_policy(self._finance, call.name, call.arguments)
            if not policy.allowed:
                self._audit.log_simple(
                    event_type="policy_denied",
                    session_id=self._session_id,
                    outcome="denied",
                    provider="finance",
                    action_type=call.name,
                    parameters={"reason": policy.reason, **call.arguments},
                )
                return ToolExecutionResult(
                    call_id=call.call_id,
                    output=json.dumps({"status": "denied", "message": policy.reason}),
                    action_taken=f"policy_denied:{call.name}",
                    denied=True,
                )

        verification_report = None
        if call.name == "finance_trigger_payment":
            verification_report = verify_finance_payment(call.arguments)
            verification_policy = evaluate_finance_verification_policy(verification_report)
            if not verification_policy.allowed:
                self._audit.log_simple(
                    event_type="verification_blocked",
                    session_id=self._session_id,
                    outcome="denied",
                    provider="finance",
                    action_type=call.name,
                    parameters={
                        "reason": verification_policy.reason,
                        "blocked": verification_report.blocked,
                    },
                )
                return ToolExecutionResult(
                    call_id=call.call_id,
                    output=json.dumps(
                        {
                            "status": "denied",
                            "message": verification_policy.reason,
                            "verification_blocked": True,
                        }
                    ),
                    action_taken=f"verification_blocked:{call.name}",
                    denied=True,
                )

        planned = provider.planned_action_for_tool(call.name, call.arguments)
        if planned is None:
            planned = PlannedAction(
                provider_name,
                call.name,
                call.arguments,
                ActionClass.READ,
            )
        action_class = classify_action(planned)
        gate_result = self._gate.evaluate(planned, action_class)

        if gate_result == GateResult.DENY_DRY_RUN:
            msg = f"[dry-run] Would execute {call.name} with {call.arguments}"
            return ToolExecutionResult(
                call_id=call.call_id, output=msg, action_taken=msg, denied=True
            )

        if gate_result == GateResult.REQUIRE_APPROVAL:
            summary = self._approval_summary(call, verification_report=verification_report)
            approved = self._approval.request_approval(summary, action_class=action_class.value)
            if not approved:
                msg = json.dumps({"status": "denied", "message": "User denied approval."})
                return ToolExecutionResult(
                    call_id=call.call_id,
                    output=msg,
                    action_taken=f"denied:{call.name}",
                    denied=True,
                )

        try:
            output = self._handlers[call.name](call.arguments)
            return ToolExecutionResult(
                call_id=call.call_id,
                output=output,
                action_taken=f"{call.name}({json.dumps(call.arguments)})",
            )
        except Exception:
            return ToolExecutionResult(
                call_id=call.call_id,
                output=json.dumps({"error": "Tool execution failed."}),
            )

    @staticmethod
    def _approval_summary(
        call: ToolCall,
        *,
        verification_report=None,
    ) -> str:
        if call.name == "knowledge_archive":
            asset_id = call.arguments.get("asset_id", "unknown")
            return f"Archive knowledge asset {asset_id}."
        if call.name == "workflow_trigger":
            workflow = call.arguments.get("workflow", "unknown")
            params = call.arguments.get("parameters", {})
            return f"Trigger workflow '{workflow}' with parameters {params}."
        if call.name == "communications_email_send":
            to = call.arguments.get("to", "unknown")
            subject = call.arguments.get("subject", "")
            return f"Send email to {to} with subject '{subject}'."
        if call.name == "communications_calendar_create":
            summary = call.arguments.get("summary", "event")
            start = call.arguments.get("start", "")
            return f"Create calendar event '{summary}' starting {start}."
        if call.name == "finance_trigger_payment":
            return format_finance_approval_summary(
                call.arguments,
                verification=verification_report,
            )
        return f"Execute {call.name} with {call.arguments}"
