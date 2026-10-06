"""Tool registration and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from wally.audit.logger import AuditLogger
from wally.models.actions import ActionClass, PlannedAction, ToolCall
from wally.models.principal import Capability, RequestContext
from wally.providers.approval import ApprovalProvider
from wally.providers.capability import CapabilityProvider
from wally.providers.finance import FinanceProvider
from wally.providers.knowledge import KnowledgeProvider
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.execution_router import ExecutionCapabilityRouter, payment_fingerprint
from wally.runtime.finance_safety import (
    evaluate_bill_paid_write_policy,
    evaluate_finance_verification_policy,
    format_finance_approval_summary,
    verify_finance_payment,
)
from wally.runtime.policy import evaluate_finance_policy, evaluate_knowledge_policy
from wally.runtime.principals import PrincipalAuthority
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
        authority: PrincipalAuthority | None = None,
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
        self._authority = authority
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

    def execute(
        self,
        call: ToolCall,
        *,
        context: RequestContext | None = None,
    ) -> ToolExecutionResult:
        # Never let mutable model arguments alias the action reviewed at the prompt.
        call = ToolCall(call.call_id, call.name, deepcopy(call.arguments))
        original_arguments = deepcopy(call.arguments)
        if call.name not in self._handlers:
            return ToolExecutionResult(
                call_id=call.call_id,
                output=json.dumps({"error": f"Unknown tool: {call.name}"}),
            )

        provider_name = self._tool_providers[call.name]
        provider = self._providers[provider_name]

        if call.name == "workflow_trigger":
            router = ExecutionCapabilityRouter(provider.list_workflows())
            definition = router.get_workflow(str(call.arguments.get("workflow", "")))
            if definition is None or (
                definition.action_class == ActionClass.FINANCIAL
                or definition.capability_domain == "payment"
            ):
                return self._deny(
                    call,
                    "Financial/unknown workflows require canonical finance review.",
                    context=context,
                )

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
                return self._verify_finance_write(call, context=context)

        if provider_name == "finance" and self._finance is not None:
            prepared_arguments = call.arguments
            if call.name == "finance_trigger_payment":
                reason = (
                    self._authority.check(context, Capability.EXECUTE_PROPOSAL)
                    if self._authority
                    else "Authenticated runtime authority is required."
                )
                if reason:
                    return self._deny(call, reason, context=context)
                prepared_arguments, routing_error = self._finance.prepare_payment(call.arguments)
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
                        output=json.dumps({"status": "denied", "message": routing_error}),
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

        reviewed_fingerprint = (
            payment_fingerprint(call.arguments) if call.name == "finance_trigger_payment" else ""
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

        if gate_result == GateResult.REQUIRE_APPROVAL or call.name == "finance_trigger_payment":
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
            if call.name == "finance_trigger_payment":
                assert self._finance is not None and self._authority is not None
                fingerprint = reviewed_fingerprint
                output = json.dumps(
                    self._finance.execute_payment(
                        original_arguments,
                        context=context,
                        authority=self._authority,
                        reviewed_fingerprint=fingerprint,
                    )
                )
                self._audit.log_simple(
                    event_type="finance_dispatch",
                    session_id=self._session_id,
                    outcome="executed_unverified",
                    provider="finance",
                    action_type=call.name,
                    parameters={
                        "action_fingerprint": fingerprint,
                        **context.provenance().as_dict(),
                    },
                )
            else:
                output = self._handlers[call.name](call.arguments)
            return ToolExecutionResult(
                call_id=call.call_id,
                output=output,
                action_taken=f"{call.name}({json.dumps(call.arguments)})",
            )
        except Exception:
            if call.name == "finance_trigger_payment":
                self._audit.log_simple(
                    event_type="finance_dispatch",
                    session_id=self._session_id,
                    outcome="failed_or_uncertain",
                    provider="finance",
                    action_type=call.name,
                    parameters={
                        "action_fingerprint": reviewed_fingerprint,
                        **context.provenance().as_dict(),
                    },
                )
                return self._deny(
                    call,
                    "Payment failed or changed after review; do not auto-retry.",
                    context=context,
                )
            return ToolExecutionResult(
                call_id=call.call_id,
                output=json.dumps({"error": "Tool execution failed."}),
            )

    def _deny(self, call: ToolCall, reason: str, *, context=None) -> ToolExecutionResult:
        provenance = {}
        if self._authority and self._authority.check(context, Capability.READ_CONTEXT) is None:
            provenance = context.provenance().as_dict()
        self._audit.log_simple(
            event_type="finance_policy_denied",
            session_id=self._session_id,
            outcome="denied",
            provider=self.provider_for(call.name),
            action_type=call.name,
            parameters={"reason": reason, **provenance},
        )
        return ToolExecutionResult(
            call.call_id,
            json.dumps({"status": "denied", "message": reason}),
            action_taken=f"policy_denied:{call.name}",
            denied=True,
        )

    def _finance_write_snapshot(self, call: ToolCall) -> str:
        target = self._knowledge.resolve_write_target(call.name, call.arguments)
        if (
            target is None
            or not evaluate_knowledge_policy(self._knowledge, call.name, call.arguments).allowed
        ):
            raise ValueError("Knowledge target changed.")
        asset = (
            self._knowledge.get(call.arguments["asset_id"])
            if call.name == "knowledge_update"
            else None
        )
        data = {
            "tool": call.name,
            "arguments": call.arguments,
            "target": asdict(target),
            "asset": asdict(asset) if asset else None,
        }
        return json.dumps(data, sort_keys=True, default=str)

    def _verify_finance_write(self, call: ToolCall, *, context) -> ToolExecutionResult:
        reason = (
            self._authority.check(context, Capability.VERIFY_EXECUTION)
            if self._authority
            else "Authenticated human verification is required."
        )
        if reason:
            return self._deny(call, reason, context=context)
        planned = PlannedAction("knowledge", call.name, call.arguments, ActionClass.FINANCIAL)
        if self._gate.evaluate(planned, ActionClass.FINANCIAL) == GateResult.DENY_DRY_RUN:
            return self._deny(call, "Dry-run: financial state unchanged.", context=context)
        # Claims in payment_evidence are ignored and never forwarded as authority.
        call.arguments.pop("payment_evidence", None)
        try:
            snapshot = self._finance_write_snapshot(call)
            summary = (
                "Independently verify this financial record before confirming the write.\n"
                "If it claims payment occurred, confirm you personally checked that payment.\n"
                "Workflow acceptance, portal login and model claims are not evidence.\n"
                f"Reviewed target and exact write: {snapshot}"
            )
            if not self._approval.request_approval(
                summary, action_class=ActionClass.FINANCIAL.value
            ):
                return self._deny(call, "Human verification declined.", context=context)
            self._authority.authorize(context, Capability.VERIFY_EXECUTION)
            if self._finance_write_snapshot(call) != snapshot:
                return self._deny(
                    call, "Financial record changed after human review.", context=context
                )
            self._audit.log_simple(
                event_type="finance_state_user_verified",
                session_id=self._session_id,
                outcome="confirmed",
                provider="knowledge",
                action_type=call.name,
                parameters={
                    "write_fingerprint": hashlib.sha256(snapshot.encode()).hexdigest(),
                    "asset_id": call.arguments.get("asset_id", ""),
                    **context.provenance().as_dict(),
                },
            )
            output = self._handlers[call.name](call.arguments)
            return ToolExecutionResult(call.call_id, output, action_taken=f"verified:{call.name}")
        except Exception:
            return self._deny(
                call, "Financial write failed; review the outcome before retrying.", context=context
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
