"""Existing native Payment History layout; no database or property creation."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from wally.adapters.notion.payments import NotionPaymentHistory, PaymentHistoryTarget
from wally.finance.models import FinanceError, digest
from wally.finance.notion import notion_id


def production_payment_history_target():
    """Pinned to the observed 23 October schema, including both relation targets."""
    return PaymentHistoryTarget(
        database_id="3884d585-1c89-80c9-8660-d7a098133860",
        data_source_id="3884d585-1c89-8020-95b3-000ba9a8c680",
        title="title",
        account="NyxU",
        occurrence="jVNQ",
        amount="Q%60Cb",
        currency="",
        fixed_currency="MYR",
        receipt="ccje",
        evidence="%3B%5EuL",
        recurring_bill="_dRH",
        paid_date="VSxr",
        confirmation_sent="IO%5CJ",
        confirmation_date="MQcI",
        receipt_file="TTcL",
        account_source_id="3884d585-1c89-806f-8820-000babad80b8",
        recurring_bill_source_id="3884d585-1c89-8029-9974-000bb244b0e0",
    )


def download_receipt(url):
    """Separate unauthenticated client: never forward the Notion bearer to storage."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
        or parsed.hostname not in {"prod-files-secure.s3.us-west-2.amazonaws.com"}
    ):
        raise FinanceError("Unreviewed Notion receipt download host; retain follow-up.")
    with (
        httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > 10_000_000:
                raise FinanceError("Notion receipt exceeds the expected size.")
    return bytes(content)


class NativeNotionPaymentHistory(NotionPaymentHistory):
    """Attach the retained original only to the independently verified payment row.

    File upload dispatch has a private, exclusive claim. Lost responses block new
    uploads; recovery only reads a known upload ID or an already attached file.
    """

    def __init__(self, *args, evidence_directory: Path, download=download_receipt, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence_directory = evidence_directory.resolve()
        self.download = download

    @property
    def fingerprint(self):
        return digest(["native-receipt-v1", super().fingerprint, str(self.evidence_directory)])

    def record_confirmation(self, bill, settlement, details, *, guard):
        super().record_confirmation(bill, settlement, details, guard=guard)
        if not self.target.receipt_file:
            raise FinanceError("Native receipt property is not mapped.")
        path = Path(details.get("receipt_path", "")).resolve()
        if not path.is_relative_to(self.evidence_directory) or not path.is_file():
            raise FinanceError("Actual retained bank receipt is unavailable; retain follow-up.")
        content = path.read_bytes()
        expected_hash = details.get("receipt_sha256")
        if (
            not content.startswith(b"%PDF-")
            or not 20 <= len(content) <= 10_000_000
            or hashlib.sha256(content).hexdigest() != expected_hash
        ):
            raise FinanceError("Retained bank receipt changed; retain follow-up.")
        self.verify_record(bill, settlement)
        page_id = notion_id(self._rows(bill)[0]["id"])

        def attached():
            self.verify_record(bill, settlement, page_id=page_id)
            response = self.reader.get(f"/pages/{page_id}")
            response.raise_for_status()
            props = {p["id"]: p for p in response.json()["properties"].values()}
            files = props.get(self.target.receipt_file, {}).get("files", [])
            if not files:
                return False
            if len(files) != 1 or files[0].get("type") != "file":
                raise FinanceError("Existing receipt attachment is ambiguous; never overwrite it.")
            if hashlib.sha256(self.download(files[0]["file"]["url"])).hexdigest() != expected_hash:
                raise FinanceError("Existing receipt attachment differs; never overwrite it.")
            return True

        if attached():
            return
        key = digest([self.fingerprint, page_id, settlement.reference, expected_hash])
        claim = path.parent / f"notion-upload-{key}.json"
        try:
            with claim.open("x", encoding="utf-8") as output:
                claim.chmod(0o600)
                json.dump({"state": "creating"}, output)
            fresh = True
        except FileExistsError:
            fresh = False
        state = json.loads(claim.read_text())
        if not fresh and not state.get("id"):
            raise FinanceError("Notion upload outcome uncertain; do not repeat POST.")
        with self.write_client() as client:
            if fresh:
                self.verify_record(bill, settlement, page_id=page_id)
                guard()
                response = client.post(
                    "/file_uploads",
                    json={
                        "mode": "single_part",
                        "filename": "bank-receipt.pdf",
                        "content_type": "application/pdf",
                    },
                )
                response.raise_for_status()
                state = {"id": notion_id(response.json()["id"]), "state": "sending"}
                claim.write_text(json.dumps(state))
                guard()
                # No explicit Content-Type: httpx generates the multipart boundary.
                client.headers.pop("Content-Type", None)
                response = client.post(
                    f"/file_uploads/{state['id']}/send",
                    files={"file": ("bank-receipt.pdf", content, "application/pdf")},
                )
                response.raise_for_status()
            response = self.reader.get(f"/file_uploads/{state['id']}")
            response.raise_for_status()
            upload = response.json()
            if (
                notion_id(upload.get("id", "")) != state["id"]
                or upload.get("status") != "uploaded"
                or upload.get("content_length") != len(content)
                or upload.get("content_type") != "application/pdf"
            ):
                raise FinanceError("Notion receipt upload is unconfirmed; never repeat send.")
            self.verify_record(bill, settlement, page_id=page_id)
            if attached():
                return
            guard()
            response = client.patch(
                f"/pages/{page_id}",
                json={
                    "properties": {
                        self.target.receipt_file: {
                            "files": [
                                {
                                    "type": "file_upload",
                                    "name": "bank-receipt.pdf",
                                    "file_upload": {"id": state["id"]},
                                }
                            ]
                        },
                    }
                },
            )
            response.raise_for_status()
        if not attached():
            raise FinanceError("Native receipt attachment readback is unconfirmed.")
