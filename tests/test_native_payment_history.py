"""Native schema/receipt acceptance; all HTTP responses are synthetic."""

from dataclasses import replace

import httpx
import pytest

from tests.test_payment_receipt_email import PDF, reconcile
from tests.test_payment_receipt_email import world as world
from wally.adapters.notion.native_receipts import (
    NativeNotionPaymentHistory,
    download_receipt,
    production_payment_history_target,
)
from wally.finance.models import FinanceError
from wally.finance.payment_demo import prepare_card, uid


@pytest.fixture
def native(world, tmp_path):
    old = world.payments.history
    target = replace(
        old.target,
        currency="",
        fixed_currency="MYR",
        receipt_file="file",
        recurring_bill="bill",
        paid_date="date",
        confirmation_sent="sent",
        confirmation_date="sentdate",
        account_source_id=uid(3),
        recurring_bill_source_id=uid(4),
    )
    original = world.notion.handle
    world.upload_creates = world.upload_sends = 0
    world.upload_lost = False
    world.upload_send_lost = False
    world.file_hash_matches = True
    world.relation_drift = False

    def handle(request):
        path = request.url.path.removeprefix("/v1")
        if path == "/file_uploads":
            world.upload_creates += 1
            if world.upload_lost:
                raise RuntimeError("synthetic lost upload create response")
            return httpx.Response(200, json={"id": uid(8)})
        if path == f"/file_uploads/{uid(8)}/send":
            world.upload_sends += 1
            assert request.headers["content-type"].startswith("multipart/form-data;")
            assert PDF in request.content
            if world.upload_send_lost:
                raise RuntimeError("synthetic lost upload send response")
            return httpx.Response(200, json={"id": uid(8)})
        if path == f"/file_uploads/{uid(8)}":
            return httpx.Response(
                200,
                json={
                    "id": uid(8),
                    "status": "uploaded",
                    "content_length": len(PDF),
                    "content_type": "application/pdf",
                },
            )
        response = original(request)
        data = response.json()
        if request.method == "GET" and path.startswith("/data_sources/"):
            data["properties"].pop("currency")
            for key, kind in {
                "file": "files",
                "bill": "relation",
                "date": "date",
                "sent": "checkbox",
                "sentdate": "date",
            }.items():
                data["properties"][key] = {"id": key, "type": kind}
            data["properties"]["account"]["relation"] = {"data_source_id": uid(3)}
            data["properties"]["bill"]["relation"] = {
                "data_source_id": uid(5) if world.relation_drift else uid(4)
            }
        if request.method == "PATCH" and "file" in data.get("properties", {}):
            prop = data["properties"]["file"]
            prop["files"] = [{"type": "file", "file": {"url": "https://storage.invalid/receipt"}}]
            world.notion.pages[data["id"]]["properties"]["file"] = prop
        return httpx.Response(200, json=data)

    world.notion.handle = handle
    world.notion.client.close()
    world.notion.client = httpx.Client(
        base_url="https://api.notion.com/v1", transport=httpx.MockTransport(handle)
    )
    world.payments.history = NativeNotionPaymentHistory(
        target,
        world.notion.client,
        old.registry,
        write_client=old.write_client,
        evidence_directory=tmp_path / "receipts",
        download=lambda _: PDF if world.file_hash_matches else PDF + b"changed",
    )
    return world


def test_native_receipt_and_relationships_use_existing_fields(native):
    proposal, callback = prepare_card(native)
    native.telegram.handle_update(callback)
    assert native.upload_creates == native.upload_sends == 1
    assert native.notion.creates == 1
    assert native.gmail.send_count == 1
    assert "Email status in Notion is unconfirmed" not in native.approvals.result(proposal.id)
    props = next(iter(native.notion.pages.values()))["properties"]
    assert "currency" not in props
    assert props["date"]["date"]["start"] == "2026-10-23"
    assert props["bill"]["relation"][0]["id"] == uid(11)
    assert props["sent"]["checkbox"] is True
    reconcile(native, proposal)
    assert native.upload_creates == native.upload_sends == native.notion.creates == 1


@pytest.mark.parametrize("phase", ["create", "send"])
def test_upload_uncertainty_recovers_without_repeated_posts(native, phase):
    native.upload_lost = phase == "create"
    native.upload_send_lost = phase == "send"
    proposal, callback = prepare_card(native)
    native.telegram.handle_update(callback)
    assert "Email status in Notion is unconfirmed" in native.approvals.result(proposal.id)
    reconcile(native, proposal)
    assert native.upload_creates == 1
    assert native.upload_sends == (1 if phase == "send" else 0)
    assert native.notion.creates == native.browser.submissions == native.gmail.send_count == 1


def test_wrong_download_does_not_claim_native_reconciliation(native):
    native.file_hash_matches = False
    proposal, callback = prepare_card(native)
    native.telegram.handle_update(callback)
    assert "Email status in Notion is unconfirmed" in native.approvals.result(proposal.id)
    reconcile(native, proposal)
    assert native.upload_creates == native.upload_sends == 1


def test_relation_drift_blocks_before_telegram_approval(native):
    native.relation_drift = True
    with pytest.raises(FinanceError, match="relation target"):
        prepare_card(native)
    assert native.upload_creates == native.notion.creates == native.gmail.send_count == 0


def test_production_mapping_has_native_files_and_no_invented_currency_column():
    target = production_payment_history_target()
    assert target.fixed_currency == "MYR" and not target.currency
    assert target.receipt_file == "TTcL"
    assert target.fingerprint


@pytest.mark.parametrize(
    "url",
    [
        "http://prod-files-secure.s3.us-west-2.amazonaws.com/a",
        "https://evil.invalid/a",
        "https://api.notion.com/a",
        "https://prod-files-secure.s3.us-west-2.amazonaws.com.evil.invalid/a",
    ],
)
def test_download_never_forwards_credentials_or_uses_unreviewed_host(url):
    with pytest.raises(FinanceError, match="host"):
        download_receipt(url)


def test_native_currency_mismatch_blocks_before_card(native):
    with pytest.raises(FinanceError, match="currency"):
        native.payments.history.check(replace(native.source.bill, currency="USD"))
    assert native.upload_creates == native.notion.creates == 0
