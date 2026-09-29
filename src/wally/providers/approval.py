"""Human approval for consequential actions."""

from __future__ import annotations

from typing import Protocol


class ApprovalProvider(Protocol):
    """Request explicit human confirmation."""

    def request_approval(self, summary: str, *, action_class: str) -> bool:
        """Return True if the user approves the action."""
        ...
