"""Communications provider tests."""

import json

import pytest

from tests.mock_communications import MockCommunicationsProvider
from tests.mock_knowledge import MockKnowledgeProvider
from wally.adapters.cli.approval import CLIApprovalProvider
from wally.adapters.google.adapter import _decode_body, _encode_rfc822, _header_value
from wally.audit.logger import AuditLogger
from wally.models.actions import ToolCall
from wally.orchestrator.tools import ToolRegistry
from wally.safety.gates import ApprovalGate


def test_encode_rfc822_produces_base64() -> None:
    raw = _encode_rfc822(to="a@b.com", subject="Hi", body="Hello")
    assert isinstance(raw, str)
    assert len(raw) > 10


def test_header_value_lookup() -> None:
    headers = [{"name": "Subject", "value": "Test"}]
    assert _header_value(headers, "subject") == "Test"


def test_decode_body_plain_text() -> None:
    import base64

    body = base64.urlsafe_b64encode(b"hello").decode()
    payload = {"mimeType": "text/plain", "body": {"data": body}}
    assert _decode_body(payload) == "hello"


def test_email_search_no_approval(tmp_path) -> None:
    comms = MockCommunicationsProvider()
    registry = ToolRegistry(
        providers={"communications": comms},
        knowledge=None,
        gate=ApprovalGate(require_approval=("irreversible",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="communications_email_search",
            arguments={"unread_only": True},
        )
    )
    payload = json.loads(result.output)
    assert payload["emails"][0]["subject"] == "Property inspection"
    assert not result.denied


def test_email_send_requires_approval(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comms = MockCommunicationsProvider()
    registry = ToolRegistry(
        providers={"communications": comms},
        knowledge=None,
        gate=ApprovalGate(require_approval=("irreversible",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    monkeypatch.setattr(
        "wally.adapters.cli.approval.CLIApprovalProvider.request_approval",
        lambda self, summary, action_class: False,
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="communications_email_send",
            arguments={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )
    )
    assert result.denied
    assert not comms.sent


def test_email_send_after_approval(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comms = MockCommunicationsProvider()
    registry = ToolRegistry(
        providers={"communications": comms},
        knowledge=None,
        gate=ApprovalGate(require_approval=("irreversible",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    monkeypatch.setattr(
        "wally.adapters.cli.approval.CLIApprovalProvider.request_approval",
        lambda self, summary, action_class: True,
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="communications_email_send",
            arguments={"to": "a@b.com", "subject": "Hi", "body": "Hello"},
        )
    )
    assert not result.denied
    assert len(comms.sent) == 1


def test_knowledge_and_communications_coexist(tmp_path) -> None:
    knowledge = MockKnowledgeProvider()
    comms = MockCommunicationsProvider()
    registry = ToolRegistry(
        providers={"knowledge": knowledge, "communications": comms},
        knowledge=knowledge,
        gate=ApprovalGate(require_approval=("irreversible",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    names = {tool["name"] for tool in registry.definitions}
    assert "knowledge_retrieve" in names
    assert "communications_email_search" in names
