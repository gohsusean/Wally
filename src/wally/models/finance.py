"""Finance domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BillSummary:
    """A bill or payment record surfaced from knowledge."""

    asset_id: str
    title: str
    content: str
    database: str
    domain: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "asset_id": self.asset_id,
            "title": self.title,
            "content": self.content,
            "database": self.database,
            "domain": self.domain,
        }


@dataclass(frozen=True)
class PaymentWorkflowSummary:
    """A payment execution capability available for approval-gated execution."""

    name: str
    description: str
    parameters: tuple[str, ...]
    capability_domain: str | None = None
    capability: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "description": self.description,
            "parameters": list(self.parameters),
        }
        if self.capability_domain:
            payload["capability_domain"] = self.capability_domain
        if self.capability:
            payload["capability"] = self.capability
        return payload


@dataclass(frozen=True)
class PaymentResolution:
    """Runtime-selected payment execution path."""

    workflow: str
    payment_method: str
    capability_domain: str
    capability: str
    parameters: dict[str, object]
