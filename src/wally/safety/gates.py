"""Approval gate logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from wally.models.actions import ActionClass, PlannedAction


class GateResult(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY_DRY_RUN = "deny_dry_run"


@dataclass
class ApprovalGate:
    """Determine whether an action may proceed."""

    require_approval: tuple[str, ...]
    dry_run: bool

    def evaluate(self, action: PlannedAction, action_class: ActionClass) -> GateResult:
        if self.dry_run and action_class != ActionClass.READ:
            return GateResult.DENY_DRY_RUN
        if action_class.value in self.require_approval:
            return GateResult.REQUIRE_APPROVAL
        return GateResult.ALLOW
