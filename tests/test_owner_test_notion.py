"""Native Notion test receipts retain bill protections without fake relations."""

from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import yaml

from tests.test_native_payment_history import native as native
from tests.test_payment_receipt_email import world as world
from wally.adapters.notion.test_transfer_history import OwnerAccountNotionHistory
from wally.finance.models import FinanceError
from wally.finance.payments import Settlement
from wally.finance.test_transfers import APPROVED_TEST, OwnerAccountTestSource


def test_test_history_records_and_reads_native_receipt_without_a_bill(native, tmp_path):
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump(APPROVED_TEST))
    source = OwnerAccountTestSource(path)
    old = native.payments.history
    history = OwnerAccountNotionHistory(
        old.target, old.reader, old.registry, write_client=old.write_client,
        evidence_directory=old.evidence_directory, download=old.download,
        store=native.payments.store, source=source,
    )
    bill = source.read("test_" + uuid4().hex)
    settlement = Settlement(
        "WT-TEST", "a" * 64, "HLB ending 4394", "BANK-TEST", "settled", "b" * 64
    )
    with pytest.raises(FinanceError, match="readback is ambiguous"):
        history.record(bill, settlement, guard=lambda: None)
    assert native.notion.creates == 0
    history.local.verify_record = Mock()  # Simulate the existing durable bank settlement row.
    history.record(bill, settlement, guard=lambda: None)
    history.verify_record(bill, settlement)
    props = next(iter(native.notion.pages.values()))["properties"]
    assert props["account"]["relation"] == props["bill"]["relation"] == []
    assert props["occurrence"]["rich_text"] == []
    assert props["amount"]["number"] == 1.0
    with pytest.raises(FinanceError, match="already contains"):
        history.record(bill, settlement, guard=lambda: None)
    assert native.notion.creates == 1
    with pytest.raises(FinanceError, match="cannot settle"):
        history.record_confirmation(
            bill, settlement, {"definition_page_id": str(uuid4())}, guard=lambda: None
        )
    with pytest.raises(FinanceError, match="exact owner-account"):
        history.check(replace(bill, amount="2.00"))
