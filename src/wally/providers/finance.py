"""Finance provider protocol — bills and payment workflows."""

from __future__ import annotations

from typing import Protocol

from wally.models.finance import BillSummary, PaymentWorkflowSummary
from wally.models.principal import RequestContext
from wally.providers.capability import CapabilityProvider
from wally.runtime.principals import PrincipalAuthority


class FinanceProvider(CapabilityProvider, Protocol):
    """Read financial knowledge and trigger approval-gated payment workflows."""

    def search_bills(self, query: str, *, limit: int = 10) -> list[BillSummary]:
        """Search knowledge for bills and payment records."""
        ...

    def list_payment_workflows(self) -> list[PaymentWorkflowSummary]:
        """List workflows classified as financial."""
        ...

    def prepare_payment(
        self, arguments: dict[str, object]
    ) -> tuple[dict[str, object], str | None]:
        """Fetch canonical inputs and resolve the exact execution capability."""
        ...

    def execute_payment(
        self,
        arguments: dict[str, object],
        *,
        context: RequestContext,
        authority: PrincipalAuthority,
        reviewed_fingerprint: str,
    ) -> dict[str, object]:
        """Revalidate a runtime-reviewed dispatch before execution."""
        ...
