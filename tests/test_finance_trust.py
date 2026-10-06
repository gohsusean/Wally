"""Financial trust-boundary regressions. All providers and credentials are synthetic."""

import json
from dataclasses import replace

import pytest

from tests.mock_knowledge import MockKnowledgeProvider
from tests.mock_workflow import MockWorkflowProvider
from tests.test_finance import _settings
from wally.adapters.finance.local import LocalFinanceAdapter
from wally.adapters.n8n.adapter import N8nWorkflowAdapter
from wally.audit.logger import AuditLogger
from wally.config.loader import NotionDatabaseConfig
from wally.models.actions import ActionClass, ToolCall
from wally.models.knowledge import KnowledgeClass
from wally.models.principal import Capability, Principal, RequestContext
from wally.orchestrator.tools import ToolRegistry
from wally.runtime.principals import ChannelPolicy, PrincipalAuthority
from wally.safety.gates import ApprovalGate

BILL = {
    "provider": "Electricity Co",
    "payee": "Electricity Co",
    "destination": "Electricity Co",
    "amount": "142.50",
    "currency": "MYR",
    "bank_account": "123456789",
    "account_reference": "ACC-1",
    "payment_method": "bank_transfer",
    "bill_id": "invoice-42",
}


class FinanceKnowledge(MockKnowledgeProvider):
    def __init__(self):
        super().__init__()
        self._databases["finance"] = NotionDatabaseConfig(
            name="finance",
            id="db-finance",
            role="finance",
            readable=True,
            writable=True,
            knowledge_class=KnowledgeClass.OPERATIONAL,
        )
        self.bill = self.seed("Electricity", "Due", role="finance", database="finance")
        self.bill.metadata = dict(BILL)

    def tool_definitions(self):
        return [*super().tool_definitions(), {"name": "knowledge_update"}]

    def execute_tool(self, name, arguments):
        if name == "knowledge_update":
            self.update(
                arguments["asset_id"],
                title=arguments.get("title"),
                content=arguments.get("content"),
            )
            return json.dumps({"status": "updated"})
        return super().execute_tool(name, arguments)


class Approval:
    def __init__(self, allow=True, callback=None):
        self.allow = allow
        self.callback = callback
        self.summaries = []

    def request_approval(self, summary, *, action_class):
        self.summaries.append(summary)
        if self.callback:
            self.callback()
        return self.allow


def runtime(tmp_path, *, approval=None, dry_run=False, require_approval=()):
    knowledge = FinanceKnowledge()
    workflow = MockWorkflowProvider()
    finance = LocalFinanceAdapter(settings=_settings(), knowledge=knowledge, workflow=workflow)
    authority = PrincipalAuthority()
    approval = approval or Approval()
    registry = ToolRegistry(
        providers={"finance": finance, "knowledge": knowledge, "workflow": workflow},
        knowledge=knowledge,
        finance=finance,
        gate=ApprovalGate(require_approval=require_approval, dry_run=dry_run),
        approval=approval,
        audit=AuditLogger(tmp_path / "audit"),
        dry_run=dry_run,
        authority=authority,
    )
    return registry, knowledge, workflow, finance, authority, approval


def payment(knowledge, **extra):
    return ToolCall(
        "c1",
        "finance_trigger_payment",
        {
            "bill": {"asset_id": knowledge.bill.id},
            "statement": dict(BILL),
            **extra,
        },
    )


def paid_write(knowledge, evidence=None):
    return ToolCall(
        "c2",
        "knowledge_update",
        {
            "asset_id": knowledge.bill.id,
            "content": "Status: paid",
            "payment_evidence": evidence,
        },
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("payee", "Attacker"),
        ("recipient", "Attacker"),
        ("destination", "Elsewhere"),
        ("bank_account", "000000000"),
        ("account", "ACC-2"),
        ("account_number", "000"),
        ("account_reference", "ACC-2"),
        ("amount", "900.00"),
        ("currency", "USD"),
        ("bill_id", "invoice-99"),
        ("asset_id", "another-bill"),
        ("payment_portal_url", "https://evil.example"),
        ("workflow", "weekly-backup"),
        ("note", {"amount": "900.00", "destination": "Attacker"}),
    ],
)
def test_execution_overrides_fail_before_approval(tmp_path, field, value):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    # Reproduce verify(A)/execute(B) with matching bill and statement plus a separate override.
    call = payment(knowledge, parameters={field: value})
    call.arguments["bill"].update(BILL)
    result = registry.execute(call, context=authority.issue("repl"))
    assert result.denied
    assert not workflow.triggered
    assert not approval.summaries


def test_only_canonical_payload_dispatches_even_when_gate_allows(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    result = registry.execute(payment(knowledge), context=authority.issue("repl"))
    assert not result.denied
    assert len(approval.summaries) == 1
    name, payload = workflow.triggered[0]
    assert name == "pay-bill-bank-transfer"
    assert payload["bank_account"] == BILL["bank_account"]
    assert payload["amount"] == BILL["amount"]
    assert payload["currency"] == BILL["currency"]
    assert payload["account"] == BILL["account_reference"]
    assert payload["asset_id"] == knowledge.bill.id
    assert json.loads(result.output)["bill_paid_not_updated"] is True
    assert knowledge.bill.content == "Due"


def test_provider_failure_is_unconfirmed_and_never_auto_retried(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    original = workflow.trigger

    def accept_then_disconnect(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("synthetic ambiguous response")

    workflow.trigger = accept_then_disconnect
    result = registry.execute(payment(knowledge), context=authority.issue("repl"))
    assert result.denied and "do not auto-retry" in result.output
    assert len(workflow.triggered) == len(approval.summaries) == 1
    assert knowledge.bill.content == "Due"
    events = [
        json.loads(line)
        for p in (tmp_path / "audit").glob("*.jsonl")
        for line in p.read_text().splitlines()
    ]
    event = next(e for e in events if e["event_type"] == "finance_dispatch")
    assert event["outcome"] == "failed_or_uncertain"
    assert event["parameters"]["action_fingerprint"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "USD"),
        ("destination", "Another payee"),
        ("account_reference", "another-obligation"),
        ("bill_id", "another-invoice"),
    ],
)
def test_statement_identity_mismatch_blocks_before_approval(tmp_path, field, value):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    call = payment(knowledge)
    call.arguments["statement"][field] = value
    assert registry.execute(call, context=authority.issue("repl")).denied
    assert not workflow.triggered and not approval.summaries


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "143.00"),
        ("bank_account", "0000"),
        ("currency", "USD"),
        ("payee", "Changed"),
        ("account_reference", "ACC-2"),
        ("payment_method", "card_portal"),
        ("bill_id", "invoice-43"),
    ],
)
def test_canonical_drift_during_approval_requires_new_cycle(tmp_path, field, value):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    approval.callback = lambda: knowledge.bill.metadata.update({field: value})
    result = registry.execute(payment(knowledge), context=authority.issue("repl"))
    assert result.denied
    assert not workflow.triggered
    assert len(approval.summaries) == 1


def test_workflow_target_drift_during_approval_blocks_dispatch(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    approval.callback = lambda: workflow._definitions.__setitem__(
        1, replace(workflow._definitions[1], webhook_path="different-destination")
    )
    assert registry.execute(payment(knowledge), context=authority.issue("repl")).denied
    assert not workflow.triggered


def test_mutating_original_model_arguments_cannot_redirect_reviewed_dispatch(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    call = payment(knowledge)
    approval.callback = lambda: call.arguments.update({"parameters": {"amount": "900"}})
    assert not registry.execute(call, context=authority.issue("repl")).denied
    assert workflow.triggered[0][1]["amount"] == BILL["amount"]


@pytest.mark.parametrize("classification", [KnowledgeClass.PENDING, KnowledgeClass.GOVERNANCE])
def test_nonoperational_canonical_bill_fails_closed(tmp_path, classification):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    knowledge.bill.knowledge_class = classification
    assert registry.execute(payment(knowledge), context=authority.issue("repl")).denied
    assert not workflow.triggered and not approval.summaries


def test_missing_metadata_does_not_fall_back_to_model_bill(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    call = payment(knowledge)
    call.arguments["bill"].update(BILL)
    knowledge.bill.metadata = {}
    assert registry.execute(call, context=authority.issue("repl")).denied
    assert not workflow.triggered and not approval.summaries


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "900"),
        ("bank_account", "000"),
        ("currency", "USD"),
    ],
)
def test_model_cannot_replace_canonical_bill_fields(tmp_path, field, value):
    registry, knowledge, workflow, _, authority, _ = runtime(tmp_path)
    call = payment(knowledge)
    call.arguments["bill"][field] = value
    assert registry.execute(call, context=authority.issue("repl")).denied
    assert not workflow.triggered


@pytest.mark.parametrize(
    "evidence",
    [
        {"type": "user_confirmation", "user_confirmed": True},
        {
            "type": "workflow_success",
            "workflow": "pay-bill-bank-transfer",
            "workflow_status": "triggered",
        },
        {
            "type": "workflow_success",
            "workflow": "pay-bill-bank-transfer",
            "workflow_status": "completed",
        },
        {"type": "verification_provider", "provider": "bank"},
        {"principal": "owner", "channel": "repl", "authenticated": True},
    ],
)
def test_self_asserted_evidence_without_authenticated_human_cannot_write(tmp_path, evidence):
    registry, knowledge, _, _, _, approval = runtime(tmp_path)
    result = registry.execute(paid_write(knowledge, evidence))
    assert result.denied and knowledge.bill.content == "Due"
    assert not approval.summaries


def test_trusted_human_verification_records_bound_provenance_and_writes(tmp_path):
    registry, knowledge, _, _, authority, approval = runtime(tmp_path)
    context = authority.issue("repl", correlation_id="verified-bill-42")
    result = registry.execute(paid_write(knowledge), context=context)
    assert not result.denied and knowledge.bill.content == "Status: paid"
    assert "personally checked" in approval.summaries[0]
    events = [
        json.loads(line)
        for p in (tmp_path / "audit").glob("*.jsonl")
        for line in p.read_text().splitlines()
    ]
    event = next(e for e in events if e["event_type"] == "finance_state_user_verified")
    assert event["parameters"]["principal"] == "owner"
    assert event["parameters"]["asset_id"] == knowledge.bill.id
    assert event["parameters"]["correlation_id"] == "verified-bill-42"
    assert context.principal.grant not in str(events)


@pytest.mark.parametrize("mode", ["forged", "foreign", "restricted"])
def test_untrusted_or_restricted_context_cannot_execute_or_verify(tmp_path, mode):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    if mode == "forged":
        context = RequestContext(Principal("owner", "repl", "local_terminal"), "forged")
    elif mode == "foreign":
        context = PrincipalAuthority().issue("repl")
    else:
        restricted = PrincipalAuthority(
            {
                "telegram": ChannelPolicy(
                    "owner_private_chat", frozenset({Capability.DECIDE_PROPOSAL})
                )
            }
        )
        registry._authority = restricted
        context = restricted.issue("telegram")
    assert registry.execute(payment(knowledge), context=context).denied
    assert registry.execute(paid_write(knowledge), context=context).denied
    assert not workflow.triggered and knowledge.bill.content == "Due"
    assert not approval.summaries


def test_human_refusal_and_record_drift_cannot_write(tmp_path):
    registry, knowledge, _, _, authority, approval = runtime(tmp_path, approval=Approval(False))
    context = authority.issue("repl")
    assert registry.execute(paid_write(knowledge), context=context).denied
    assert knowledge.bill.content == "Due"
    approval.allow = True
    approval.callback = lambda: knowledge.bill.metadata.update({"bill_id": "another-obligation"})
    assert registry.execute(paid_write(knowledge), context=context).denied
    assert knowledge.bill.content == "Due"


def test_direct_model_tool_execution_does_not_authorize_finance(tmp_path):
    _, knowledge, workflow, finance, _, _ = runtime(tmp_path)
    result = json.loads(
        finance.execute_tool("finance_trigger_payment", payment(knowledge).arguments)
    )
    assert result["status"] == "denied" and not workflow.triggered


def test_secret_injection_cannot_replace_verified_money_fields(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    knowledge.bill.metadata["workflow_secret_refs"] = {"amount": "op://Test/Bad/amount"}
    assert registry.execute(payment(knowledge), context=authority.issue("repl")).denied
    assert not workflow.triggered and not approval.summaries


@pytest.mark.parametrize(
    "bad",
    [
        {"amount": "USD142.50"},
        {"amount": "NaN"},
        {"currency": ""},
        {"bank_account": {"recipient": "attacker"}},
        {"workflow_secret_refs": {"otp": "op://Test/OTP/value", "amount": 999}},
    ],
)
def test_malformed_canonical_material_fields_fail_closed(tmp_path, bad):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    knowledge.bill.metadata.update(bad)
    assert registry.execute(payment(knowledge), context=authority.issue("repl")).denied
    assert not workflow.triggered and not approval.summaries


def test_unusable_canonical_account_cannot_be_verified_by_absent_statement_account(tmp_path):
    registry, knowledge, workflow, _, authority, approval = runtime(tmp_path)
    knowledge.bill.metadata["bank_account"] = "pending"
    call = payment(knowledge)
    call.arguments["statement"].pop("bank_account")
    assert registry.execute(call, context=authority.issue("repl")).denied
    assert not workflow.triggered and not approval.summaries


def test_repeated_exact_assertions_and_statement_notes_cannot_supply_dispatch_fields(tmp_path):
    registry, knowledge, workflow, _, authority, _ = runtime(tmp_path)
    call = payment(knowledge, parameters={"amount": BILL["amount"], "account": "ACC-1"})
    call.arguments["statement"]["note"] = {"amount": "999", "recipient": "attacker"}
    assert not registry.execute(call, context=authority.issue("repl")).denied
    assert "note" not in workflow.triggered[0][1]
    assert workflow.triggered[0][1]["amount"] == BILL["amount"]


def test_financial_workflow_alias_and_misclassification_do_not_bypass_review(tmp_path):
    registry, _, workflow, _, _, approval = runtime(tmp_path)
    workflow._definitions[1] = replace(
        workflow._definitions[1], action_class=ActionClass.READ, aliases=("shortcut",)
    )
    for name in ("shortcut", "pay-bill-bank-transfer"):
        call = ToolCall("c", "workflow_trigger", {"workflow": name, "parameters": {"amount": 100}})
        assert registry.execute(call).denied
    assert not workflow.triggered and not approval.summaries


def test_n8n_model_tool_financial_alias_never_posts(tmp_path):
    workflow = MockWorkflowProvider()
    definition = replace(workflow._definitions[1], aliases=("shortcut",))
    adapter = N8nWorkflowAdapter(base_url="https://example.invalid", workflows=[definition])
    try:
        for name in ("shortcut", definition.name):
            payload = json.loads(adapter.execute_tool("workflow_trigger", {"workflow": name}))
            assert payload["status"] == "denied"
    finally:
        adapter.close()


@pytest.mark.parametrize("allow,dry_run", [(False, False), (True, True)])
def test_payment_and_paid_write_keep_approval_and_dry_run_boundaries(tmp_path, allow, dry_run):
    registry, knowledge, workflow, _, authority, _ = runtime(
        tmp_path, approval=Approval(allow), dry_run=dry_run
    )
    context = authority.issue("repl")
    assert registry.execute(payment(knowledge), context=context).denied
    assert registry.execute(paid_write(knowledge), context=context).denied
    assert not workflow.triggered and knowledge.bill.content == "Due"
