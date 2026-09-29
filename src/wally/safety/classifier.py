"""Rule-based action risk classification."""

from __future__ import annotations

from wally.models.actions import ActionClass, PlannedAction

_ACTION_RULES: dict[tuple[str, str], ActionClass] = {
    ("knowledge", "retrieve"): ActionClass.READ,
    ("knowledge", "get"): ActionClass.READ,
    ("knowledge", "create"): ActionClass.REVERSIBLE,
    ("knowledge", "update"): ActionClass.REVERSIBLE,
    ("knowledge", "archive"): ActionClass.DESTRUCTIVE,
    ("home_automation", "get_state"): ActionClass.READ,
    ("home_automation", "list_entities"): ActionClass.READ,
    ("home_automation", "call_service"): ActionClass.REVERSIBLE,
    ("workflow", "trigger"): ActionClass.IRREVERSIBLE,
    ("workflow", "trigger_financial"): ActionClass.FINANCIAL,
    ("communications", "email_search"): ActionClass.READ,
    ("communications", "email_get"): ActionClass.READ,
    ("communications", "email_draft"): ActionClass.REVERSIBLE,
    ("communications", "email_send"): ActionClass.IRREVERSIBLE,
    ("communications", "calendar_list"): ActionClass.READ,
    ("communications", "calendar_availability"): ActionClass.READ,
    ("communications", "calendar_create"): ActionClass.IRREVERSIBLE,
    ("conversation", "search"): ActionClass.READ,
    ("conversation", "recent"): ActionClass.READ,
    ("web", "search"): ActionClass.READ,
    ("web", "fetch"): ActionClass.READ,
    ("finance", "bills_search"): ActionClass.READ,
    ("finance", "payment_workflows"): ActionClass.READ,
    ("finance", "trigger_payment"): ActionClass.FINANCIAL,
}


def classify_action(action: PlannedAction) -> ActionClass:
    """Classify a planned action using deterministic rules."""
    key = (action.provider, action.action)
    if key in _ACTION_RULES:
        return _ACTION_RULES[key]

    if action.action_class != ActionClass.READ:
        return action.action_class

    if action.action in {"delete", "remove", "destroy", "archive"}:
        return ActionClass.DESTRUCTIVE
    if action.action in {"pay", "transfer", "purchase"}:
        return ActionClass.FINANCIAL
    if action.action in {"send", "publish", "post"}:
        return ActionClass.IRREVERSIBLE

    return ActionClass.READ
