"""Workflow provider and tool tests."""

import json

import pytest

from tests.mock_knowledge import MockKnowledgeProvider
from tests.mock_workflow import MockWorkflowProvider
from wally.adapters.cli.approval import CLIApprovalProvider
from wally.adapters.n8n.adapter import _webhook_url
from wally.audit.logger import AuditLogger
from wally.config.loader import load_workflows_config
from wally.models.actions import ToolCall
from wally.orchestrator.tools import ToolRegistry
from wally.safety.gates import ApprovalGate


def test_load_workflows_config(project_root) -> None:
    workflows = load_workflows_config(project_root)
    names = {wf.name for wf in workflows}
    assert "weekly-backup" in names
    assert "pay-bill-bank-transfer" in names
    bank_transfer = next(wf for wf in workflows if wf.name == "pay-bill-bank-transfer")
    assert bank_transfer.capability == "bank_transfer"


def test_webhook_url_joins_base_and_path() -> None:
    assert (
        _webhook_url("https://example.app.n8n.cloud/webhook-test/", "weekly-backup")
        == "https://example.app.n8n.cloud/webhook-test/weekly-backup"
    )


def test_webhook_url_avoids_double_path_when_base_includes_workflow() -> None:
    assert (
        _webhook_url(
            "https://example.app.n8n.cloud/webhook-test/weekly-backup",
            "weekly-backup",
        )
        == "https://example.app.n8n.cloud/webhook-test/weekly-backup"
    )


def test_workflow_list_no_approval(tmp_path) -> None:
    workflow = MockWorkflowProvider()
    registry = ToolRegistry(
        providers={"workflow": workflow},
        knowledge=None,
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    result = registry.execute(ToolCall(call_id="c1", name="workflow_list", arguments={}))
    payload = json.loads(result.output)
    assert len(payload["workflows"]) == 2
    assert not result.denied


def test_financial_workflow_requires_approval(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = MockWorkflowProvider()
    registry = ToolRegistry(
        providers={"workflow": workflow},
        knowledge=None,
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
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
            name="workflow_trigger",
            arguments={"workflow": "pay-bill-bank-transfer", "parameters": {"amount": 100}},
        )
    )
    assert result.denied
    assert not workflow.triggered


def test_financial_workflow_triggers_after_approval(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = MockWorkflowProvider()
    registry = ToolRegistry(
        providers={"workflow": workflow},
        knowledge=None,
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
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
            name="workflow_trigger",
            arguments={"workflow": "pay-bill-bank-transfer", "parameters": {"amount": 100}},
        )
    )
    assert not result.denied
    assert workflow.triggered == [("pay-bill-bank-transfer", {"amount": 100})]


def test_knowledge_and_workflow_tools_coexist(tmp_path) -> None:
    knowledge = MockKnowledgeProvider()
    workflow = MockWorkflowProvider()
    registry = ToolRegistry(
        providers={"knowledge": knowledge, "workflow": workflow},
        knowledge=knowledge,
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path),
        dry_run=False,
    )
    names = {tool["name"] for tool in registry.definitions}
    assert "knowledge_retrieve" in names
    assert "workflow_trigger" in names
