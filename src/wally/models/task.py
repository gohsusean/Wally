"""Task abstraction for runtime routing and orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TaskIntent(StrEnum):
    """High-level task category — used by ReasoningRouter, not raw NL."""

    CONVERSATION = "conversation"
    TOOL_FOLLOWUP = "tool_followup"
    CONSOLIDATION = "consolidation"


@dataclass(frozen=True)
class Task:
    """A unit of work presented to the reasoning runtime."""

    intent: TaskIntent
    tool_names: tuple[str, ...] = ()
    workflow: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
