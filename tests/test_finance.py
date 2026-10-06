"""Finance provider tests."""

import json
from dataclasses import dataclass, field

from wally.adapters.finance.local import LocalFinanceAdapter
from wally.config.loader import Settings
from wally.models.actions import ActionClass
from wally.models.knowledge import KnowledgeAsset, KnowledgeRetrievalResult
from wally.models.workflow import WorkflowDefinition, WorkflowParameter, WorkflowTriggerResult


@dataclass
class _MockKnowledge:
    assets: list[KnowledgeAsset] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "knowledge"

    def is_healthy(self) -> bool:
        return True

    def retrieve(self, query: str, *, role: str | None = None, limit: int = 10):
        return KnowledgeRetrievalResult(assets=self.assets[:limit], query=query)

    def get(self, asset_id: str):
        return next(asset for asset in self.assets if asset.id == asset_id)


def _bill_knowledge(bill: dict) -> _MockKnowledge:
    return _MockKnowledge(
        assets=[
            KnowledgeAsset(
                id=bill.get("asset_id", "bill-1"),
                title="Canonical bill",
                content="Due",
                database="bills",
                role="finance",
                metadata={"currency": "MYR", **bill},
            )
        ]
    )


@dataclass
class _MockWorkflow:
    workflows: list[WorkflowDefinition] = field(default_factory=list)
    triggered: list[tuple[str, dict | None]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "workflow"

    def is_healthy(self) -> bool:
        return True

    def list_workflows(self) -> list[WorkflowDefinition]:
        return self.workflows

    def trigger(self, workflow: str, parameters: dict | None = None) -> WorkflowTriggerResult:
        self.triggered.append((workflow, parameters))
        return WorkflowTriggerResult(workflow=workflow, status="triggered", response={})


def _settings(**overrides) -> Settings:
    from pathlib import Path

    from wally.config.loader import load_settings

    base = load_settings(
        project_root=Path(__file__).resolve().parents[1], config_name="macbook"
    )
    from dataclasses import replace

    return replace(base, finance_bills_role="finance", **overrides)


def test_search_bills_delegates_to_knowledge() -> None:
    knowledge = _MockKnowledge(
        assets=[
            KnowledgeAsset(
                id="bill-1",
                title="Electricity",
                content="Due 15th, $142.50",
                database="bills",
                role="finance",
            )
        ]
    )
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=knowledge,
        workflow=None,
    )
    bills = adapter.search_bills("electricity")
    assert len(bills) == 1
    assert bills[0].title == "Electricity"


def test_list_payment_workflows_filters_financial() -> None:
    workflow = _MockWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay bill",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
                parameters=(
                    WorkflowParameter(
                        name="amount", param_type="number", description="", required=True
                    ),
                ),
            ),
            WorkflowDefinition(
                name="weekly-backup",
                description="Backup",
                webhook_path="weekly-backup",
                action_class=ActionClass.REVERSIBLE,
                parameters=(),
            ),
        ]
    )
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=workflow,
    )
    names = [item.name for item in adapter.list_payment_workflows()]
    assert names == ["pay-bill-bank-transfer"]


def test_trigger_payment_delegates_to_workflow() -> None:
    workflow = _MockWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                parameters=(),
            )
        ]
    )
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=workflow,
    )
    adapter._trigger_payment(
        "pay-bill-bank-transfer", parameters={"amount": 142.5}, authorized=True
    )
    assert workflow.triggered == [("pay-bill-bank-transfer", {"amount": 142.5})]


def test_finance_bills_search_tool_output() -> None:
    knowledge = _MockKnowledge(
        assets=[
            KnowledgeAsset(
                id="bill-1",
                title="Water",
                content="$80 due",
                database="bills",
                role="finance",
            )
        ]
    )
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=knowledge,
        workflow=None,
    )
    output = json.loads(adapter.execute_tool("finance_bills_search", {"query": "water"}))
    assert output["bills"][0]["title"] == "Water"
    assert output["source"] == "knowledge"
