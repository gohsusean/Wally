"""Action planning types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActionClass(StrEnum):
    READ = "read"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"
    FINANCIAL = "financial"
    DESTRUCTIVE = "destructive"


@dataclass
class PlannedAction:
    """An action the orchestrator may execute via a provider."""

    provider: str
    action: str
    parameters: dict[str, object]
    action_class: ActionClass


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
