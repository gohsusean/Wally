"""Safety layer tests."""

from wally.models.actions import ActionClass, PlannedAction
from wally.safety.classifier import classify_action
from wally.safety.gates import ApprovalGate, GateResult


def test_classify_knowledge_retrieve() -> None:
    action = PlannedAction("knowledge", "retrieve", {}, ActionClass.READ)
    assert classify_action(action) == ActionClass.READ


def test_classify_knowledge_archive() -> None:
    action = PlannedAction("knowledge", "archive", {}, ActionClass.READ)
    assert classify_action(action) == ActionClass.DESTRUCTIVE


def test_gate_allows_read() -> None:
    gate = ApprovalGate(
        require_approval=("financial", "destructive", "irreversible"),
        dry_run=False,
    )
    action = PlannedAction("knowledge", "retrieve", {}, ActionClass.READ)
    assert gate.evaluate(action, ActionClass.READ) == GateResult.ALLOW


def test_gate_requires_approval_for_financial() -> None:
    gate = ApprovalGate(require_approval=("financial",), dry_run=False)
    action = PlannedAction("workflow", "trigger_financial", {}, ActionClass.FINANCIAL)
    assert gate.evaluate(action, ActionClass.FINANCIAL) == GateResult.REQUIRE_APPROVAL


def test_gate_dry_run_blocks_writes() -> None:
    gate = ApprovalGate(require_approval=(), dry_run=True)
    action = PlannedAction("home_automation", "call_service", {}, ActionClass.REVERSIBLE)
    assert gate.evaluate(action, ActionClass.REVERSIBLE) == GateResult.DENY_DRY_RUN
