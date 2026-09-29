"""Workflow domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wally.models.actions import ActionClass


@dataclass(frozen=True)
class WorkflowParameter:
    name: str
    param_type: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class WorkflowDefinition:
    """A version-controlled workflow Wally may trigger via n8n."""

    name: str
    description: str
    webhook_path: str
    action_class: ActionClass = ActionClass.IRREVERSIBLE
    parameters: tuple[WorkflowParameter, ...] = field(default_factory=tuple)
    capability_domain: str | None = None
    capability: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    execution_backend: str = "n8n"


@dataclass(frozen=True)
class WorkflowTriggerResult:
    workflow: str
    status: str
    response: dict[str, Any] = field(default_factory=dict)
