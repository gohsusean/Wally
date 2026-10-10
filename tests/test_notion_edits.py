"""Isolated exact-review -> decision -> execution -> semantic verification coverage."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from wally.adapters.notion.edits import NotionEditBackend
from wally.exceptions import AuthorizationError, ProposalDecisionError, ProposalTransitionError
from wally.finance.notion import NotionFinanceReader
from wally.gateway.service import AdapterRegistration, GatewayRuntime
from wally.models.knowledge import KnowledgeClass
from wally.models.ops import ExecutionStatus, MatterStatus, ProposalStatus
from wally.models.principal import Capability, Principal, RequestContext
from wally.ops.decisions import UserDecision, apply_user_decision
from wally.ops.notion_edits import (
    DecisionSelection,
    EditError,
    EditTarget,
    NotionEditService,
    PropertyRule,
    RecordSnapshot,
)
from wally.ops.proposal_reconcile import ProposalReconciler
from wally.ops.store import OperationsStore
from wally.runtime.confirmation import digest
from wally.runtime.principals import LOCAL_OPERATOR_CHANNELS, ChannelPolicy, PrincipalAuthority


def uid(n):
    return str(UUID(int=n))


class Human:
    method = "synthetic_authenticated_owner_ui"

    def __init__(self):
        self.reviews = []
        self.callback = lambda: None
        self.answer = True

    def confirm(self, review):
        self.reviews.append(review)
        self.callback()
        return self.answer


class Backend:
    def __init__(self):
        self.data = {}
        self.writes = []
        self.invalidated = []
        self.after_write = lambda: None
        self.failure = False
        self.read_failure = False

    def read(self, target):
        if self.read_failure:
            raise EditError("read unavailable")
        value = self.data.setdefault(target.key, "fixed_contract")
        return RecordSnapshot(
            {"policy": value},
            {
                "policy": digest(["select", value]),
                "protected": digest("original"),
            },
            "schema",
        )

    def invalidate_certification(self, target):
        self.invalidated.append(target.key)

    def write(self, target, replacements, expected, revalidate):
        revalidate()
        self.writes.append((target.key, replacements))
        if self.failure:
            raise RuntimeError("secret error must never be persisted")
        self.data[target.key] = replacements["policy"]
        self.after_write()


def harness(tmp_path, *, enabled=True, verifier=True, backend=None):
    human = Human()
    restricted = frozenset(
        {Capability.SUBMIT_REQUEST, Capability.READ_CONTEXT, Capability.DECIDE_PROPOSAL}
    )
    authority = PrincipalAuthority(
        {
            **LOCAL_OPERATOR_CHANNELS,
            "codex": ChannelPolicy(
                "local_adapter",
                frozenset(
                    {
                        Capability.READ_CONTEXT,
                        Capability.SUBMIT_REQUEST,
                        Capability.DECIDE_PROPOSAL,
                        Capability.EXECUTE_NOTION_EDIT,
                        Capability.VERIFY_NOTION_EDIT,
                    }
                ),
            ),
            "chatgpt": ChannelPolicy("session_only", restricted),
            "telegram": ChannelPolicy("owner_callback", restricted),
        },
        human_confirmers={"codex": human, "cli": human} if verifier else {},
    )
    targets = tuple(
        EditTarget(
            key,
            uid(1),
            uid(2),
            uid(n),
            (PropertyRule("amount_policy", "policy", ("fixed_contract", "source_defined")),),
        )
        for n, key in enumerate(("tnb", "maybank"), 10)
    )
    store = OperationsStore(tmp_path / "ops.db")
    service = NotionEditService(
        store, authority, backend or Backend(), targets, writes_enabled=enabled
    )
    return service, human, authority.issue("codex")


def propose(service, context, key="tnb", **kwargs):
    return service.propose(key, {"policy": "source_defined", **kwargs}, context=context)


def select(proposal, decision=UserDecision.APPROVE, **kwargs):
    return DecisionSelection(proposal.id, proposal.fingerprint, decision, **kwargs)


def approved(service, context):
    proposal = propose(service, context)
    service.decide((select(proposal),), context=context)
    return proposal


def test_interface_neutral_full_flow_and_durable_audit(tmp_path):
    service, human, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    assert service.backend.writes == []
    assert service.store.get_proposal(proposal.id).status == ProposalStatus.APPROVED
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.VERIFIED_SUCCESS
    assert len(human.reviews) == 2
    assert human.reviews[0].nonce != human.reviews[1].nonce
    assert json.loads(human.reviews[0].presentation)["operation"] == "decide"
    assert json.loads(human.reviews[1].presentation)["operation"] == "execute_and_verify"
    assert service.backend.writes == [("tnb", {"policy": "source_defined"})]
    assert service.backend.invalidated == ["tnb"]
    assert service.store.get_matter(result.matter_id).status == MatterStatus.OPEN
    with service.store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM human_confirmations").fetchone()[0] == 2
        events = [r[0] for r in conn.execute("SELECT event FROM human_review_events")]
        assert events == [
            "edit_proposed",
            "decision_presented",
            "edit_decisions_recorded",
            "execution_presented",
            "edit_execution_started",
            "edit_verified",
        ]
        stored = str([tuple(r) for r in conn.execute("SELECT * FROM human_confirmations")])
        assert ctx.principal.grant not in stored
    with pytest.raises(EditError, match="already exists"):
        service.execute(proposal.id, context=ctx)
    assert len(service.backend.writes) == 1


def test_partial_batch_approve_reject_and_leave_unselected(tmp_path):
    service, human, ctx = harness(tmp_path)
    one, two = propose(service, ctx), propose(service, ctx, "maybank")
    service.decide((select(one),), context=ctx)
    assert service.store.get_proposal(two.id).status == ProposalStatus.PROPOSED
    service.decide((select(two, UserDecision.REJECT),), context=ctx)
    assert service.store.get_proposal(two.id).status == ProposalStatus.REJECTED
    with pytest.raises(EditError):
        service.execute(two.id, context=ctx)
    assert propose(service, ctx, "maybank").id == two.id
    assert len(json.loads(human.reviews[0].presentation)["selections"]) == 1


def test_explicit_batch_is_atomic_when_one_version_drifts_during_confirmation(tmp_path):
    service, human, ctx = harness(tmp_path)
    one, two = propose(service, ctx), propose(service, ctx, "maybank")
    human.callback = lambda: service.backend.data.update(maybank="source_defined")
    with pytest.raises(EditError, match="changed"):
        service.decide((select(one), select(two)), context=ctx)
    assert service.store.get_proposal(one.id).status == ProposalStatus.PROPOSED
    assert service.store.get_proposal(two.id).status == ProposalStatus.INVALIDATED
    assert service.backend.writes == []


def test_batch_compare_and_set_rolls_back_all_decisions(tmp_path):
    service, human, ctx = harness(tmp_path)
    one, two = propose(service, ctx), propose(service, ctx, "maybank")
    selections = (select(one), select(two))
    original = service.store.record_edit_decisions

    def race(*args):
        service.store.close_proposal(
            two.id,
            status=ProposalStatus.SUPERSEDED,
            updated_at=datetime.now(UTC).isoformat(),
            status_reason="race",
        )
        original(*args)

    service.store.record_edit_decisions = race
    with pytest.raises(ProposalTransitionError):
        service.decide(selections, context=ctx)
    assert service.store.get_proposal(one.id).status == ProposalStatus.PROPOSED
    with service.store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM human_confirmations").fetchone()[0] == 0


@pytest.mark.parametrize("channel", ["chatgpt", "telegram"])
def test_session_and_model_claims_cannot_confirm_or_execute(tmp_path, channel):
    service, _, ctx = harness(tmp_path)
    proposal = propose(service, ctx)
    caller = service.authority.issue(channel)
    with pytest.raises(AuthorizationError, match="No trustworthy"):
        service.decide((select(proposal),), context=caller)
    service.decide((select(proposal),), context=ctx)
    with pytest.raises(AuthorizationError):
        service.execute(proposal.id, context=caller)
    assert service.backend.writes == []


def test_forged_foreign_and_unverifiable_owner_fail_closed(tmp_path):
    service, _, ctx = harness(tmp_path, verifier=False)
    proposal = propose(service, ctx)
    for caller in (
        ctx,
        PrincipalAuthority().issue("cli"),
        RequestContext(Principal("owner", "codex", "local_adapter"), "forged"),
    ):
        with pytest.raises(AuthorizationError):
            service.decide((select(proposal),), context=caller)
    with pytest.raises(ProposalDecisionError, match="exact scoped review"):
        apply_user_decision(
            service.store,
            proposal.id,
            decision=UserDecision.APPROVE,
            context=service.authority.issue("cli"),
            authority=service.authority,
            now=datetime.now(UTC),
        )


@pytest.mark.parametrize("decision", [UserDecision.REJECT, UserDecision.DEFER])
def test_rejection_deferral_and_defer_release(tmp_path, decision):
    service, _, ctx = harness(tmp_path)
    proposal = propose(service, ctx)
    later = datetime.now(UTC) + timedelta(hours=1)
    selection = select(
        proposal, decision, defer_until=later.isoformat() if decision == UserDecision.DEFER else ""
    )
    service.decide((selection,), context=ctx)
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    ProposalReconciler(service.store).reconcile(now=later + timedelta(seconds=1))
    stored = service.store.get_proposal(proposal.id)
    assert stored.status == (
        ProposalStatus.PROPOSED if decision == UserDecision.DEFER else ProposalStatus.REJECTED
    )
    if decision == UserDecision.DEFER:
        assert not stored.decision


def test_superseded_outdated_and_substituted_versions(tmp_path):
    service, _, ctx = harness(tmp_path)
    old = propose(service, ctx)
    service.backend.data["tnb"] = "source_defined"
    new = service.propose("tnb", {"policy": "fixed_contract"}, context=ctx)
    assert service.store.get_proposal(old.id).status == ProposalStatus.SUPERSEDED
    for selection in (
        select(old),
        DecisionSelection(new.id, old.fingerprint, UserDecision.APPROVE),
    ):
        with pytest.raises(EditError):
            service.decide((selection,), context=ctx)
    service.backend.data["tnb"] = "fixed_contract"
    with pytest.raises(EditError, match="changed"):
        service.decide((select(new),), context=ctx)


def test_write_gate_and_scope_are_closed(tmp_path):
    service, _, ctx = harness(tmp_path, enabled=False)
    for replacements in ({"paid": "yes"}, {"policy": "secret"}, {"policy": True}):
        with pytest.raises(EditError):
            service.propose("tnb", replacements, context=ctx)
    proposal = approved(service, ctx)
    with pytest.raises(EditError, match="disabled"):
        service.execute(proposal.id, context=ctx)
    assert service.backend.writes == []


@pytest.mark.parametrize("drift", ["record", "matter", "target", "gate", "approval"])
def test_execution_revalidates_after_human_confirmation(tmp_path, drift):
    service, human, ctx = harness(tmp_path)
    proposal = approved(service, ctx)

    def change():
        if drift == "record":
            service.backend.data["tnb"] = "source_defined"
        elif drift == "matter":
            matter = service.store.get_matter(proposal.matter_id)
            service.store.save_matter(replace(matter, status=MatterStatus.RESOLVED))
        elif drift == "target":
            service.targets["tnb"] = replace(service.targets["tnb"], page_id=uid(90))
        elif drift == "gate":
            service.writes_enabled = False
        else:
            service.store.close_proposal(
                proposal.id,
                status=ProposalStatus.INVALIDATED,
                updated_at=datetime.now(UTC).isoformat(),
                status_reason="drift",
            )

    human.callback = change
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    assert service.backend.writes == []
    assert service.backend.invalidated == []


def test_uncertain_attempt_blocks_retry_and_other_versions_after_restart(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    service.backend.failure = True
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.EXECUTED_UNVERIFIED
    service.store = OperationsStore(tmp_path / "ops.db")
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    service.backend.data["tnb"] = "source_defined"
    other = service.propose("tnb", {"policy": "fixed_contract"}, context=ctx)
    service.decide((select(other),), context=ctx)
    with pytest.raises((EditError, sqlite3.IntegrityError)):
        service.execute(other.id, context=ctx)
    assert len(service.backend.writes) == 1
    assert "secret error" not in str(service.store.list_executions())


def test_interrupted_running_attempt_is_never_retried_or_verified_as_idle(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)

    def interrupt(target, replacements, expected, revalidate):
        raise KeyboardInterrupt

    service.backend.write = interrupt
    with pytest.raises(KeyboardInterrupt):
        service.execute(proposal.id, context=ctx)
    attempt = service.store.list_executions(proposal_id=proposal.id)[0]
    assert attempt.status == ExecutionStatus.RUNNING
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    with pytest.raises(EditError, match="running"):
        service.verify(attempt.id, context=ctx)


def test_verification_read_failure_never_reports_success(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    service.backend.after_write = lambda: setattr(service.backend, "read_failure", True)
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.EXECUTED_UNVERIFIED
    service.backend.read_failure = False
    result = service.verify(result.id, context=ctx)
    assert result.status == ExecutionStatus.VERIFIED_SUCCESS
    assert len(service.backend.writes) == 1


class NotionHTTP:
    def __init__(self):
        self.page = {
            "id": uid(10),
            "object": "page",
            "parent": {"data_source_id": uid(2)},
            "last_edited_time": "old",
            "properties": {
                "Policy": {"id": "policy", "type": "select", "select": {"name": "fixed_contract"}},
                "Business": {"id": "business", "type": "number", "number": 42},
                "Audit History": {"id": "audit", "type": "rich_text", "rich_text": []},
            },
        }
        self.schema = {
            "id": uid(2),
            "parent": {"database_id": uid(1)},
            "properties": {
                "Policy": {
                    "id": "policy",
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": "fixed_contract"},
                            {"name": "source_defined"},
                        ]
                    },
                },
                "Business": {"id": "business", "type": "number", "number": {}},
                "Audit History": {"id": "audit", "type": "rich_text", "rich_text": {}},
            },
        }
        self.calls = []
        self.side_effect = lambda: None
        self.patch_body = None

    def handle(self, request):
        self.calls.append(request.method)
        if request.method == "PATCH":
            self.patch_body = json.loads(request.content)
            assert set(self.patch_body) == {"properties"}
            assert set(self.patch_body["properties"]) == {"policy"}
            self.page["properties"]["Policy"]["select"] = self.patch_body["properties"]["policy"][
                "select"
            ]
            self.page["last_edited_time"] = "changed"
            self.page["properties"]["Audit History"]["rich_text"] = [
                {"plain_text": "Normal audit event"}
            ]
            self.side_effect()
            return httpx.Response(200, json=self.page)
        return httpx.Response(
            200, json=self.schema if "data_sources" in request.url.path else self.page
        )


def http_harness(tmp_path):
    api = NotionHTTP()
    registry_record = SimpleNamespace(
        classification=KnowledgeClass.OPERATIONAL, approved_by="owner", role="general"
    )
    registry = SimpleNamespace(find_by_key_or_id=lambda key: registry_record)

    def client():
        return httpx.Client(
            transport=httpx.MockTransport(api.handle), base_url="https://api.notion.com/v1"
        )

    backend = NotionEditBackend(
        NotionFinanceReader(client()),
        registry,
        finance=SimpleNamespace(sources=lambda: ()),
        write_client=client,
    )
    service, human, ctx = harness(tmp_path, backend=backend)
    service.targets["tnb"] = replace(service.targets["tnb"], audit_property_ids=("audit",))
    return service, human, ctx, api, registry_record


def test_gateway_to_mock_notion_end_to_end_ignores_only_benign_audit(tmp_path):
    service, human, ctx, api, _ = http_harness(tmp_path)
    runtime = GatewayRuntime(
        service.store,
        service.authority,
        (AdapterRegistration("local-codex", "codex", "fixture-session", approval_adapter=True),),
        notion_edits=service,
    )

    def call(op, **body):
        response = runtime.dispatch(
            adapter_id="local-codex", credential="fixture-session", op=op, body=body
        )
        assert response.ok, response.error
        return response.data

    proposal = call(
        "propose_notion_edit", target_key="tnb", replacements={"policy": "source_defined"}
    )
    review = call("get_notion_edit", proposal_id=proposal["id"])
    assert review["changes"] == [
        {
            "property_id": "policy",
            "field": "amount_policy",
            "before": "fixed_contract",
            "after": "source_defined",
        }
    ]
    call(
        "decide_notion_edits",
        selections=[
            {
                "proposal_id": proposal["id"],
                "fingerprint": proposal["fingerprint"],
                "decision": "approve",
            }
        ],
    )
    assert "PATCH" not in api.calls

    result = call("execute_notion_edit", proposal_id=proposal["id"])
    assert result["status"] == "verified_success"
    assert api.calls[-2:] == ["GET", "GET"]
    assert api.calls.count("PATCH") == 1
    denied = runtime.dispatch(
        adapter_id="local-codex",
        credential="fixture-session",
        op="execute_notion_edit",
        body={"proposal_id": proposal["id"], "replacements": {"business": 0}},
    )
    assert not denied.ok


@pytest.mark.parametrize("observed", ["approved", "original", "unexpected", "unavailable"])
def test_running_reconciliation_reads_but_never_unlocks_or_retries(tmp_path, observed):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)

    def interrupt(*args):
        raise KeyboardInterrupt

    service.backend.write = interrupt
    with pytest.raises(KeyboardInterrupt):
        service.execute(proposal.id, context=ctx)
    execution = service.store.list_executions(proposal_id=proposal.id)[0]
    if observed == "approved":
        service.backend.data["tnb"] = "source_defined"
    elif observed == "unexpected":
        service.backend.data["tnb"] = "unexpected"
    elif observed == "unavailable":
        service.backend.read_failure = True
    expected = {
        "approved": "approved_state_observed",
        "original": "original_state_observed",
        "unexpected": "unexpected_state",
        "unavailable": "read_unavailable",
    }
    result = service.inspect_execution(execution.id, context=ctx)
    assert result["observation"] == expected[observed]
    assert not result["completion_proven"]
    assert not result["claim_released"]
    assert not result["write_repeated"]
    assert service.store.get_execution(execution.id).status == ExecutionStatus.RUNNING
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)


def test_timeout_after_provider_applies_write_can_verify_without_retry(tmp_path):
    service, _, ctx, api, _ = http_harness(tmp_path)
    original = api.handle

    def timeout_after_patch(request):
        result = original(request)
        if request.method == "PATCH":
            raise httpx.ReadTimeout("synthetic uncertainty")
        return result

    service.backend.write_client = lambda: httpx.Client(
        transport=httpx.MockTransport(timeout_after_patch), base_url="https://api.notion.com/v1"
    )
    proposal = approved(service, ctx)
    execution = service.execute(proposal.id, context=ctx)
    assert execution.failure_category == "patch_outcome_uncertain"
    assert execution.status == ExecutionStatus.EXECUTED_UNVERIFIED
    inspection = service.inspect_execution(execution.id, context=ctx)
    assert inspection["observation"] == "approved_state_observed"
    assert not inspection["completion_proven"]
    assert service.verify(execution.id, context=ctx).status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.calls.count("PATCH") == 1
    api.page["properties"]["Business"]["number"] = 987
    inspection = service.inspect_execution(execution.id, context=ctx)
    assert inspection["observation"] == "unexpected_state"
    assert inspection["stored_verification_success"]
    assert not inspection["completion_proven"]


@pytest.mark.parametrize("mutation", ["business", "scope", "schema"])
def test_unexpected_business_or_schema_change_fails_verification(tmp_path, mutation):
    service, _, ctx, api, _ = http_harness(tmp_path)
    proposal = approved(service, ctx)

    def modify():
        if mutation == "business":
            api.page["properties"]["Business"]["number"] = 999
        elif mutation == "scope":
            api.page["properties"]["Policy"]["select"]["name"] = "fixed_contract"
        else:
            api.schema["properties"]["Business"]["type"] = "checkbox"

    api.side_effect = modify
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.EXECUTED_UNVERIFIED
    assert result.failure_category in {"verification_mismatch", "verification_unavailable"}
    assert len(service.store.list_executions()) == 1


def test_classification_change_after_review_prevents_write(tmp_path):
    service, human, ctx, api, registry = http_harness(tmp_path)
    proposal = approved(service, ctx)
    human.callback = lambda: setattr(registry, "classification", KnowledgeClass.GOVERNANCE)
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    assert "PATCH" not in api.calls


@pytest.mark.parametrize("outcome", ["verified", "denied", "unattempted", "uncertain"])
def test_financial_edit_invalidates_certificate_without_recertifying(tmp_path, outcome):
    from tests.finance_fixture import ATTEST, PROOF, catalog_fixture
    from wally.finance.models import Candidate, Kind, Locator, Scope
    from wally.finance.notion import FieldMapping, SourceMapping

    service, human, ctx, api, registry = http_harness(tmp_path)
    catalog, ids, _ = catalog_fixture(tmp_path, service.authority)
    original = catalog.store.get(ids["definition"])
    candidate = Candidate(
        Kind.DEFINITION, Locator("notion", uid(1), uid(2), uid(10)), original.candidate.facts
    )
    candidate = replace(
        candidate,
        facts=replace(candidate.facts, amount_policy="fixed_contract", fixed_amount="1.00"),
    )
    record = catalog.store.register(candidate)
    catalog.store.certify(record, Scope.IDENTITY, {}, PROOF, list(ATTEST), {"channel": "fixture"})
    source = SourceMapping(
        uid(1), uid(2), Kind.DEFINITION, {"amount_policy": FieldMapping("policy", "select")}
    )
    catalog.sources = lambda: (source,)
    catalog._source_gate = lambda source: None  # Fixture designation, no live source.
    service.backend.finance = catalog
    service.targets["tnb"] = replace(service.targets["tnb"], finance_id=record.id)
    registry.role = "finance"
    before_count = len(catalog.store.records())
    proposal = approved(service, ctx)
    assert catalog.store.certificate(record.id, Scope.IDENTITY)
    if outcome == "denied":
        human.answer = False
        with pytest.raises(AuthorizationError):
            service.execute(proposal.id, context=ctx)
        assert catalog.store.certificate(record.id, Scope.IDENTITY)
        assert "PATCH" not in api.calls
        return
    if outcome == "unattempted":

        def unavailable():
            raise RuntimeError("synthetic credential acquisition failure")

        service.backend.write_client = unavailable
    elif outcome == "uncertain":
        original_handle = api.handle

        def uncertain(request):
            response = original_handle(request)
            if request.method == "PATCH":
                raise httpx.ReadTimeout("synthetic timeout")
            return response

        service.backend.write_client = lambda: httpx.Client(
            transport=httpx.MockTransport(uncertain), base_url="https://api.notion.com/v1"
        )
    result = service.execute(proposal.id, context=ctx)
    if outcome == "verified":
        assert result.status == ExecutionStatus.VERIFIED_SUCCESS
    else:
        assert result.status == ExecutionStatus.EXECUTED_UNVERIFIED
        assert result.failure_category == (
            "patch_not_dispatched" if outcome == "unattempted" else "patch_outcome_uncertain"
        )
        assert api.calls.count("PATCH") == (0 if outcome == "unattempted" else 1)
    assert catalog.store.certificate(record.id, Scope.IDENTITY) is None
    assert catalog.store.get(record.id).invalid
    assert len(catalog.store.records()) == before_count
    assert catalog.store.certificate(ids["property"], Scope.IDENTITY) is not None


def test_finance_cannot_be_mislabelled_general_or_business_ignored_as_audit(tmp_path):
    service, _, ctx, _, registry = http_harness(tmp_path)
    registry.role = "finance"
    with pytest.raises(EditError, match="canonical"):
        propose(service, ctx)
    registry.role = "general"
    service.targets["tnb"] = replace(service.targets["tnb"], audit_property_ids=("policy",))
    with pytest.raises(EditError):
        propose(service, ctx)


def test_protected_property_truncation_and_unknown_types_fail_closed(tmp_path):
    service, _, ctx, api, _ = http_harness(tmp_path)
    api.page["properties"]["Business"] = {
        "id": "business",
        "type": "relation",
        "relation": [],
        "has_more": True,
    }
    api.schema["properties"]["Business"]["type"] = "relation"
    with pytest.raises(EditError, match="hydration"):
        propose(service, ctx)


def test_confirmation_denial_and_expiry(tmp_path, monkeypatch):
    service, human, ctx = harness(tmp_path)
    proposal = propose(service, ctx)
    human.answer = False
    with pytest.raises(AuthorizationError):
        service.decide((select(proposal),), context=ctx)
    human.answer = True
    human.callback = lambda: monkeypatch.setattr("wally.runtime.principals.time.time", lambda: 1e20)
    with pytest.raises(AuthorizationError, match="expired"):
        service.decide((select(proposal),), context=ctx)
    assert service.store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED


def test_source_change_while_resolving_action_credential_blocks_patch(tmp_path):
    service, _, ctx, api, _ = http_harness(tmp_path)
    proposal = approved(service, ctx)
    factory = service.backend.write_client

    def change_during_credential_resolution():
        api.page["properties"]["Business"]["number"] = 99
        return factory()

    service.backend.write_client = change_during_credential_resolution
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.EXECUTED_UNVERIFIED
    assert "PATCH" not in api.calls


def test_runtime_write_gate_and_target_policy_are_reloaded_after_confirmation(tmp_path):
    service, human, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    gate = [True]
    service._write_gate = lambda: gate[0]
    human.callback = lambda: gate.__setitem__(0, False)
    with pytest.raises(EditError, match="gate"):
        service.execute(proposal.id, context=ctx)
    assert service.backend.writes == []


def test_uuid_aliases_share_the_same_record_lock_identity(tmp_path):
    service, _, _ = harness(tmp_path)
    target = service.targets["tnb"]
    assert (
        replace(target, page_id=target.page_id.replace("-", "").upper()).page_id == target.page_id
    )


def test_mixed_batch_approval_is_explicitly_scoped(tmp_path):
    service, human, ctx = harness(tmp_path)
    one, two = propose(service, ctx), propose(service, ctx, "maybank")
    result = service.decide((select(one), select(two, UserDecision.REJECT)), context=ctx)
    assert [p.status for p in result] == [ProposalStatus.APPROVED, ProposalStatus.REJECTED]
    presented = json.loads(human.reviews[0].presentation)["selections"]
    assert [s["decision"] for s in presented] == ["approve", "reject"]
    assert service.backend.writes == []


def test_confirmation_nonce_cannot_be_replayed_for_a_second_decision(tmp_path, monkeypatch):
    service, _, ctx = harness(tmp_path)
    one, two = propose(service, ctx), propose(service, ctx, "maybank")
    confirm = service.authority.confirm_human
    receipt = []

    def save_receipt(*args, **kwargs):
        receipt.append(confirm(*args, **kwargs))
        return receipt[-1]

    monkeypatch.setattr(service.authority, "confirm_human", save_receipt)
    service.decide((select(one),), context=ctx)
    monkeypatch.setattr(service.authority, "confirm_human", lambda *a, **k: receipt[0])
    with pytest.raises(AuthorizationError, match="replayed"):
        service.decide((select(two),), context=ctx)
    assert service.store.get_proposal(two.id).status == ProposalStatus.PROPOSED


def test_expired_and_invalidated_proposals_require_a_new_review(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = propose(service, ctx)
    expired = replace(proposal, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    service.store.save_proposal(expired)
    with pytest.raises(EditError, match="expired"):
        service.decide((select(proposal),), context=ctx)
    ProposalReconciler(service.store).reconcile(now=datetime.now(UTC))
    assert service.store.get_proposal(proposal.id).status == ProposalStatus.EXPIRED
    successor = propose(service, ctx)
    assert successor.id != proposal.id
    assert successor.fingerprint != proposal.fingerprint
    assert successor.status == ProposalStatus.PROPOSED and not successor.decision
    assert propose(service, ctx).id == successor.id


def test_business_property_cannot_be_excluded_as_benign_audit(tmp_path):
    service, _, ctx, _, _ = http_harness(tmp_path)
    service.targets["tnb"] = replace(service.targets["tnb"], audit_property_ids=("business",))
    with pytest.raises(EditError, match="Audit History"):
        propose(service, ctx)


@pytest.mark.parametrize("drift", ["gate", "approval", "target"])
def test_authorization_change_during_credential_acquisition_blocks_patch(tmp_path, drift):
    service, _, ctx, api, _ = http_harness(tmp_path)
    proposal = approved(service, ctx)
    factory = service.backend.write_client

    def credential_factory():
        if drift == "gate":
            service.writes_enabled = False
        elif drift == "approval":
            service.store.close_proposal(
                proposal.id,
                status=ProposalStatus.INVALIDATED,
                updated_at=datetime.now(UTC).isoformat(),
                status_reason="revoked",
            )
        else:
            service.targets["tnb"] = replace(service.targets["tnb"], page_id=uid(99))
        return factory()

    service.backend.write_client = credential_factory
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.EXECUTED_UNVERIFIED
    assert "PATCH" not in api.calls
