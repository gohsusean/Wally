"""Finance provider protocol — bills and payment workflows."""

from __future__ import annotations

from typing import Protocol

from wally.models.finance import BillSummary, PaymentWorkflowSummary
from wally.providers.capability import CapabilityProvider


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
        """Resolve execution capability and merge provider parameters."""
        ...

    def trigger_payment(
        self, workflow: str, parameters: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Trigger an approval-gated financial workflow via n8n."""
        ...
