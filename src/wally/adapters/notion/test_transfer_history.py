"""Record a verified owner-account test without inventing a bill relationship."""

from wally.adapters.notion.native_receipts import NativeNotionPaymentHistory
from wally.finance.models import FinanceError, digest
from wally.finance.test_transfers import LocalTestHistory


class OwnerAccountNotionHistory(NativeNotionPaymentHistory):
    def __init__(self, *args, store, source, **kwargs):
        super().__init__(*args, **kwargs)
        self.local = LocalTestHistory(store)
        self.source = source

    @property
    def fingerprint(self):
        return digest(["owner_test_notion_v1", super().fingerprint, self.source.policy])

    def _validate_test(self, bill):
        if bill != self.source.read(bill.instance_id):
            raise FinanceError("Only the exact owner-account test may use this receipt target.")

    @staticmethod
    def _title(bill):
        return "RM1 owner-account test transfer · " + bill.instance_id

    def _rows(self, bill):
        self._validate_test(bill)
        if bill.currency != self.target.fixed_currency:
            raise FinanceError("Test receipt currency differs from the reviewed target.")
        self._gate()
        response = self.reader.post(
            f"/data_sources/{self.target.data_source_id}/query",
            json={
                "filter": {"property": self.target.title,
                           "title": {"equals": self._title(bill)}},
                "page_size": 100,
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("has_more"):
            raise FinanceError("Test receipt inventory is incomplete.")
        return data["results"]

    def _properties(self, bill, settlement):
        self._validate_test(bill)
        props = super()._properties(bill, settlement)
        props[self.target.title] = {
            "title": [{"type": "text", "text": {"content": self._title(bill)}}]
        }
        props[self.target.occurrence] = {"rich_text": []}  # A test has no billing period.
        props[self.target.account] = {"relation": []}
        if self.target.recurring_bill:
            props[self.target.recurring_bill] = {"relation": []}
        return props

    def check(self, bill):
        self.local.check(bill)  # A different test cannot evade an uncertain attempt.
        super().check(bill)

    def record(self, bill, settlement, *, guard):
        # Require durable independently checked settlement before the Notion create.
        self.local.verify_record(bill, settlement)
        super().record(bill, settlement, guard=guard)

    def record_confirmation(self, bill, settlement, details, *, guard):
        if details.get("definition_page_id"):
            raise FinanceError("A test receipt cannot settle or link a bill obligation.")
        super().record_confirmation(bill, settlement, details, guard=guard)
