"""Narrow receipt recording after exact independent provider settlement.

Uses existing Notion REST client/registry contracts. No arbitrary metadata update,
credential fields, catalog certification or Matter resolution is exposed here.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal

from wally.finance.models import FinanceError, digest
from wally.finance.notion import notion_id
from wally.models.knowledge import KnowledgeClass


@dataclass(frozen=True)
class PaymentHistoryTarget:
    database_id: str
    data_source_id: str
    title: str
    account: str
    occurrence: str
    amount: str
    currency: str
    receipt: str
    evidence: str

    @property
    def fingerprint(self):
        notion_id(self.database_id)
        notion_id(self.data_source_id)
        fields = [v for k, v in asdict(self).items() if k not in {"database_id", "data_source_id"}]
        if any(not v for v in fields) or len(set(fields)) != len(fields):
            raise FinanceError("Payment History needs distinct reviewed property IDs.")
        return digest(asdict(self))


def rich(value):
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


class NotionPaymentHistory:
    def __init__(self, target, reader, registry, *, write_client):
        self.target, self.reader, self.registry = target, reader, registry
        self.write_client = write_client

    @property
    def fingerprint(self):
        return self.target.fingerprint

    def _gate(self):
        record = self.registry.find_by_key_or_id(self.target.database_id)
        if (
            record is None
            or record.classification != KnowledgeClass.OPERATIONAL
            or not record.approved_by
        ):
            raise FinanceError("Payment History is not approved operational knowledge.")
        # Verify the pinned schema and parent before querying or writing.
        response = self.reader.get(f"/data_sources/{self.target.data_source_id}")
        response.raise_for_status()
        source = response.json()
        if notion_id(source.get("parent", {}).get("database_id", "")) != notion_id(
            self.target.database_id
        ):
            raise FinanceError("Payment History parent changed.")
        expected = {
            "title": "title",
            "account": "relation",
            "occurrence": "rich_text",
            "amount": "number",
            "currency": "rich_text",
            "receipt": "rich_text",
            "evidence": "rich_text",
        }
        props = {p["id"]: p["type"] for p in source["properties"].values()}
        if any(props.get(getattr(self.target, k)) != v for k, v in expected.items()):
            raise FinanceError("Payment History schema changed.")

    def _rows(self, bill):
        self._gate()
        response = self.reader.post(
            f"/data_sources/{self.target.data_source_id}/query",
            json={
                "filter": {
                    "and": [
                        {
                            "property": self.target.account,
                            "relation": {"contains": bill.account_page_id},
                        },
                        {
                            "property": self.target.occurrence,
                            "rich_text": {"equals": bill.occurrence},
                        },
                    ]
                },
                "page_size": 100,
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("has_more"):
            raise FinanceError("Ambiguous Payment History inventory.")
        return data["results"]

    def check(self, bill):
        if self._rows(bill):
            raise FinanceError("Payment History already contains this obligation; reconcile first.")

    def _properties(self, bill, settlement):
        t = self.target
        return {
            t.title: {"title": [{"type": "text", "text": {"content": "Payment receipt"}}]},
            t.account: {"relation": [{"id": bill.account_page_id}]},
            t.occurrence: rich(bill.occurrence),
            t.amount: {"number": float(Decimal(bill.amount))},
            t.currency: rich(bill.currency),
            t.receipt: rich(settlement.reference),
            t.evidence: rich(settlement.evidence_hash),
        }

    def record(self, bill, settlement, *, guard):
        self.check(bill)
        props = self._properties(bill, settlement)
        # Factory resolves Notion action credentials after runtime settlement checks.
        with self.write_client() as client:
            self.check(bill)  # Classification/schema/duplicate checks after credentials.
            guard()
            result = client.post(
                "/pages",
                json={
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": self.target.data_source_id,
                    },
                    "properties": props,
                },
            )
            result.raise_for_status()
            page_id = notion_id(result.json()["id"])
        self.verify_record(bill, settlement, page_id=page_id)

    def verify_record(self, bill, settlement, *, page_id=""):
        rows = self._rows(bill)
        if len(rows) != 1 or (page_id and notion_id(rows[0]["id"]) != page_id):
            raise FinanceError("Payment History duplicate/readback is ambiguous.")
        page_id = notion_id(rows[0]["id"])
        read = self.reader.get(f"/pages/{page_id}")
        read.raise_for_status()
        page = read.json()
        if (
            page.get("archived")
            or page.get("in_trash")
            or notion_id(page.get("parent", {}).get("data_source_id", ""))
            != notion_id(self.target.data_source_id)
        ):
            raise FinanceError("Payment History readback target changed.")
        actual = {p["id"]: p for p in page["properties"].values()}
        for key, expected in self._properties(bill, settlement).items():
            kind, value = next(iter(expected.items()))
            found = actual.get(key, {}).get(kind)
            if kind in {"title", "rich_text"}:
                found = "".join(
                    p.get("plain_text", p.get("text", {}).get("content", "")) for p in found or []
                )
                value = "".join(p["text"]["content"] for p in value)
            if found != value:
                raise FinanceError("Payment History receipt readback did not match.")
