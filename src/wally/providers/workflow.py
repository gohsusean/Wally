"""Workflow provider protocol."""

from __future__ import annotations

from typing import Protocol

from wally.models.workflow import WorkflowDefinition, WorkflowTriggerResult
from wally.providers.capability import CapabilityProvider


class WorkflowProvider(CapabilityProvider, Protocol):
    """Trigger and inspect version-controlled workflows (n8n)."""

    def list_workflows(self) -> list[WorkflowDefinition]:
        """Return workflows available for triggering."""
        ...

    def trigger(
        self, workflow: str, parameters: dict[str, object] | None = None
    ) -> WorkflowTriggerResult:
        """Trigger a workflow by stable name."""
        ...
