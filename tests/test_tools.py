"""Tool registry and policy tests."""

import json

import pytest

from tests.mock_knowledge import MockKnowledgeProvider
from wally.adapters.cli.approval import CLIApprovalProvider
from wally.audit.logger import AuditLogger
from wally.models.actions import ToolCall
from wally.orchestrator.tools import ToolRegistry
from wally.runtime.policy import evaluate_knowledge_policy
from wally.safety.gates import ApprovalGate


def test_delete_requires_approval(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    knowledge = MockKnowledgeProvider()
    asset = knowledge.seed("Old tenant", "Notes about tenant")
    registry = ToolRegistry(
        providers={"knowledge": knowledge},
        knowledge=knowledge,
        gate=ApprovalGate(require_approval=("destructive",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
        session_id="test",
    )
    monkeypatch.setattr(
        "wally.adapters.cli.approval.CLIApprovalProvider.request_approval",
        lambda self, summary, action_class: False,
    )
    result = registry.execute(
        ToolCall(call_id="c1", name="knowledge_archive", arguments={"asset_id": asset.id})
    )
    assert result.denied is True
    assert asset.id in knowledge._assets


def test_retrieve_executes_without_approval(tmp_path) -> None:
    knowledge = MockKnowledgeProvider()
    knowledge.seed("Bali trip", "Fun holiday")
    registry = ToolRegistry(
        providers={"knowledge": knowledge},
        knowledge=knowledge,
        gate=ApprovalGate(require_approval=("destructive",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    result = registry.execute(
        ToolCall(call_id="c1", name="knowledge_retrieve", arguments={"query": "Bali"})
    )
    payload = json.loads(result.output)
    assert len(payload["assets"]) == 1


def test_governance_write_rejected_by_policy() -> None:
    knowledge = MockKnowledgeProvider()
    policy = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "New SOP", "content": "...", "database": "governance"},
    )
    assert policy.allowed is False
    assert "Governance" in (policy.reason or "")


def test_governance_write_rejected_in_registry(tmp_path) -> None:
    knowledge = MockKnowledgeProvider()
    registry = ToolRegistry(
        providers={"knowledge": knowledge},
        knowledge=knowledge,
        gate=ApprovalGate(require_approval=("destructive",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
        session_id="sess-1",
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="knowledge_create",
            arguments={"title": "Hack", "content": "...", "database": "governance"},
        )
    )
    assert result.denied is True
    assert "policy_denied" in (result.action_taken or "")


def test_pending_write_rejected_in_registry(tmp_path) -> None:
    knowledge = MockKnowledgeProvider()
    registry = ToolRegistry(
        providers={"knowledge": knowledge},
        knowledge=knowledge,
        gate=ApprovalGate(require_approval=("destructive",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
        session_id="sess-1",
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="knowledge_create",
            arguments={"title": "Note", "content": "...", "database": "pending"},
        )
    )
    assert result.denied is True


def test_operational_write_allowed_by_policy() -> None:
    knowledge = MockKnowledgeProvider()
    policy = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "Bill note", "content": "...", "database": "operations"},
    )
    assert policy.allowed is True
