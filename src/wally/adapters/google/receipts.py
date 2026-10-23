"""Gmail receipt transport, called only by the supervised payment worker."""

import base64
import hashlib
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr

from wally.adapters.google.adapter import GMAIL_API, _decode_body, _header_value
from wally.finance.models import FinanceError


class GmailReceiptMailer:
    def __init__(self, communications):
        self.communications = communications

    def send_receipt_email(self, details, receipt):
        if hashlib.sha256(receipt).hexdigest() != details["receipt_sha256"]:
            raise FinanceError("Receipt attachment changed before Gmail dispatch.")
        message = EmailMessage()
        message["To"] = details["recipient"]
        message["Subject"] = details["subject"]
        message["Message-ID"] = details["rfc822_id"]
        message.set_content(details["body"])
        message.add_attachment(
            receipt, maintype="application", subtype="pdf", filename="receipt.pdf"
        )
        # Transport errors are ambiguous. The caller retains its claim and reads Gmail.
        return self.communications._request(
            "POST",
            GMAIL_API + "/users/me/messages/send",
            json_body={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
        )

    def verify_receipt_email(self, details):
        request = self.communications._request
        result = request(
            "GET",
            GMAIL_API + "/users/me/messages",
            params={
                "q": "in:sent rfc822msgid:" + details["rfc822_id"].strip("<>"),
                "maxResults": 2,
            },
        )
        if not isinstance(result, dict) or (
            "messages" not in result and result.get("resultSizeEstimate") != 0
        ):
            raise FinanceError("Gmail confirmation inventory is incomplete.")
        rows = result.get("messages", [])
        if result.get("nextPageToken") or not isinstance(rows, list) or len(rows) > 1:
            raise FinanceError("Gmail confirmation inventory is ambiguous.")
        if not rows:
            return None
        message_id = rows[0]["id"]
        sent = request(
            "GET", GMAIL_API + f"/users/me/messages/{message_id}", params={"format": "full"}
        )
        payload = sent.get("payload", {})
        headers = payload.get("headers", [])
        if (
            sent.get("id") != message_id
            or "SENT" not in sent.get("labelIds", [])
            or _header_value(headers, "Message-ID") != details["rfc822_id"]
            or parseaddr(_header_value(headers, "To"))[1].casefold()
            != details["recipient"].casefold()
            or str(make_header(decode_header(_header_value(headers, "Subject"))))
            != details["subject"]
            or _header_value(headers, "Cc")
            or _header_value(headers, "Bcc")
            or _decode_body(payload).replace("\r\n", "\n").rstrip() != details["body"].rstrip()
        ):
            raise FinanceError(
                "Gmail sent-message readback differs from the approved confirmation."
            )

        def attachments(part):
            if part.get("filename"):
                yield part
            for child in part.get("parts", []):
                yield from attachments(child)

        parts = list(attachments(payload))
        if len(parts) != 1 or parts[0].get("mimeType") != "application/pdf":
            raise FinanceError("Gmail did not retain exactly the bank PDF attachment.")
        attachment = parts[0].get("body", {})
        if attachment.get("attachmentId"):
            attachment = request(
                "GET",
                GMAIL_API
                + f"/users/me/messages/{message_id}/attachments/"
                + attachment["attachmentId"],
            )
        content = base64.urlsafe_b64decode(attachment.get("data", "") + "==")
        if hashlib.sha256(content).hexdigest() != details["receipt_sha256"]:
            raise FinanceError(
                "Gmail bank receipt attachment does not match the retained original."
            )
        return {
            "message_id": message_id,
            "thread_id": sent.get("threadId", ""),
            "sent_at": datetime.fromtimestamp(int(sent["internalDate"]) / 1000, UTC).isoformat(),
        }
