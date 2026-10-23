"""Narrow receipt recording after exact independent provider settlement.

Uses existing Notion REST client/registry contracts. No arbitrary metadata update,
credential fields, catalog certification or Matter resolution is exposed here.
"""

import json
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
    recurring_bill: str = ""
    paid_date: str = ""
    confirmation_sent: str = ""
    confirmation_date: str = ""
    fixed_currency: str = ""
    receipt_file: str = ""
    account_source_id: str = ""
    recurring_bill_source_id: str = ""

    @property
    def fingerprint(self):
        notion_id(self.database_id)
        notion_id(self.data_source_id)
        optional = {
            "recurring_bill",
            "paid_date",
            "confirmation_sent",
            "confirmation_date",
            "receipt_file",
            "account_source_id",
            "recurring_bill_source_id",
        }
        if self.fixed_currency:
            if self.currency or self.fixed_currency != "MYR":
                raise FinanceError("A fixed MYR receipt target requires no currency column.")
            optional.add("currency")
        fields = [
            v
            for k, v in asdict(self).items()
            if k
            not in {
                "database_id",
                "data_source_id",
                "fixed_currency",
                "account_source_id",
                "recurring_bill_source_id",
            }
            and (v or k not in optional)
        ]
        if any(not v for v in fields) or len(set(fields)) != len(fields):
            raise FinanceError("Payment History needs distinct reviewed property IDs.")
        for source in (self.account_source_id, self.recurring_bill_source_id):
            if source:
                notion_id(source)
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
        _ = self.target.fingerprint
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
        expected.update(
            {
                name: kind
                for name, kind in {
                    "recurring_bill": "relation",
                    "paid_date": "date",
                    "confirmation_sent": "checkbox",
                    "confirmation_date": "date",
                    "receipt_file": "files",
                }.items()
                if getattr(self.target, name)
            }
        )
        if self.target.fixed_currency:
            expected.pop("currency")
        props = {p["id"]: p["type"] for p in source["properties"].values()}
        if any(props.get(getattr(self.target, k)) != v for k, v in expected.items()):
            raise FinanceError("Payment History schema changed.")
        properties = {p["id"]: p for p in source["properties"].values()}
        for field, source_id in (
            (self.target.account, self.target.account_source_id),
            (self.target.recurring_bill, self.target.recurring_bill_source_id),
        ):
            if source_id and notion_id(
                properties.get(field, {}).get("relation", {}).get("data_source_id", "")
            ) != notion_id(source_id):
                raise FinanceError("Payment History relation target changed.")

    def _rows(self, bill):
        if self.target.fixed_currency and bill.currency != self.target.fixed_currency:
            raise FinanceError("Payment currency differs from the reviewed receipt target.")
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
        if t.fixed_currency and bill.currency != t.fixed_currency:
            raise FinanceError("Payment currency differs from the reviewed receipt target.")
        props = {
            t.title: {"title": [{"type": "text", "text": {"content": "Payment receipt"}}]},
            t.account: {"relation": [{"id": bill.account_page_id}]},
            t.occurrence: rich(bill.occurrence),
            t.amount: {"number": float(Decimal(bill.amount))},
            t.receipt: rich(settlement.reference),
            t.evidence: rich(settlement.evidence_hash),
        }
        if t.currency:
            props[t.currency] = rich(bill.currency)
        return props

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
                if key == self.target.evidence and found.startswith("{"):
                    found = json.loads(found).get("settlement_hash")
            if found != value:
                raise FinanceError("Payment History receipt readback did not match.")

    def record_confirmation(self, bill, settlement, details, *, guard):
        """Update only this verified receipt's email evidence; never recreate a payment.

        Repeated recovery reads detect an already applied PATCH. Receipt settlement
        stays independently checkable when email is outstanding or its response was lost.
        """
        self.verify_record(bill, settlement)
        rows = self._rows(bill)
        if len(rows) != 1:
            raise FinanceError("Confirmation receipt target is ambiguous.")
        page_id = notion_id(rows[0]["id"])
        allowed = {
            "state",
            "recipient",
            "message_id",
            "thread_id",
            "rfc822_id",
            "sent_rfc822_id",
            "sent_at",
            "receipt_sha256",
            "paid_date",
            "definition_page_id",
        }
        evidence = {
            "settlement_hash": settlement.evidence_hash,
            "confirmation": {k: v for k, v in details.items() if k in allowed},
        }
        props = {self.target.evidence: rich(json.dumps(evidence, sort_keys=True))}
        if self.target.recurring_bill and details.get("definition_page_id"):
            props[self.target.recurring_bill] = {
                "relation": [{"id": notion_id(details["definition_page_id"])}]
            }
        if self.target.paid_date and details.get("paid_date"):
            props[self.target.paid_date] = {"date": {"start": details["paid_date"]}}
        sent = details.get("state") == "sent"
        if self.target.confirmation_sent:
            props[self.target.confirmation_sent] = {"checkbox": sent}
        if self.target.confirmation_date:
            props[self.target.confirmation_date] = {
                "date": {"start": details["sent_at"]} if sent else None
            }

        def matches():
            response = self.reader.get(f"/pages/{page_id}")
            response.raise_for_status()
            page = response.json()
            if (
                notion_id(page.get("id")) != page_id
                or page.get("archived")
                or page.get("in_trash")
                or notion_id(page.get("parent", {}).get("data_source_id"))
                != notion_id(self.target.data_source_id)
            ):
                raise FinanceError("Confirmation receipt readback target changed.")
            actual = {p["id"]: p for p in page["properties"].values()}
            for key, expected in props.items():
                kind, value = next(iter(expected.items()))
                found = actual.get(key, {}).get(kind)
                if kind == "rich_text":
                    found = "".join(
                        p.get("plain_text", p.get("text", {}).get("content", ""))
                        for p in found or []
                    )
                    value = "".join(p["text"]["content"] for p in value)
                elif kind == "relation":
                    found = [notion_id(p["id"]) for p in found or []]
                    value = [notion_id(p["id"]) for p in value]
                elif kind == "date":
                    found = found.get("start") if found else None
                    value = value.get("start") if value else None
                if found != value:
                    return False
            return True

        if matches():
            return
        with self.write_client() as client:
            self.verify_record(bill, settlement)
            guard()
            response = client.patch(f"/pages/{page_id}", json={"properties": props})
            response.raise_for_status()
        if not matches():
            raise FinanceError("Confirmation receipt metadata is unconfirmed.")
