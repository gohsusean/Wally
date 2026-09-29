"""Deterministic runtime policy — enforced before provider calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wally.models.knowledge import KnowledgeClass
from wally.providers.knowledge import KnowledgeProvider

KNOWLEDGE_WRITE_TOOLS = frozenset(
    {"knowledge_create", "knowledge_update", "knowledge_archive"}
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


def evaluate_knowledge_policy(
    provider: KnowledgeProvider,
    tool_name: str,
    arguments: dict[str, Any],
) -> PolicyDecision:
    """Layer 1 policy: reject non-operational knowledge writes before provider execution."""
    if tool_name not in KNOWLEDGE_WRITE_TOOLS:
        return PolicyDecision(allowed=True)

    target = provider.resolve_write_target(tool_name, arguments)
    if target is None:
        return PolicyDecision(
            allowed=False,
            reason="Could not resolve the knowledge database for this write.",
        )

    if target.knowledge_class == KnowledgeClass.PENDING:
        return PolicyDecision(
            allowed=False,
            reason=(
                "Database classification is pending. "
                "Approve it with /knowledge approve before writing."
            ),
        )

    if target.knowledge_class == KnowledgeClass.GOVERNANCE:
        return PolicyDecision(
            allowed=False,
            reason="Governance knowledge cannot be modified by Wally.",
        )

    if not target.writable:
        return PolicyDecision(
            allowed=False,
            reason=f"Database '{target.name}' is not writable.",
        )

    return PolicyDecision(allowed=True)


FINANCE_PAYMENT_TOOL = "finance_trigger_payment"


def evaluate_finance_policy(
    finance_provider: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> PolicyDecision:
    """Layer 1 policy: financial writes only via registered financial workflows."""
    if tool_name != FINANCE_PAYMENT_TOOL:
        return PolicyDecision(allowed=True)

    workflow_name = str(arguments.get("workflow", "")).strip()
    if not workflow_name:
        return PolicyDecision(allowed=False, reason="Payment workflow name is required.")

    allowed = {item.name for item in finance_provider.list_payment_workflows()}
    if workflow_name not in allowed:
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Workflow '{workflow_name}' is not a registered financial workflow. "
                "Use finance_payment_workflows to list allowed payment workflows."
            ),
        )

    return PolicyDecision(allowed=True)
