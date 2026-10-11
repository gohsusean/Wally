"""Isolated missing-state and incomplete-financial-locator authorization tests."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import httpx
import pytest

from tests.test_notion_edits import approved, http_harness, propose, select, uid
from tests.test_telegram_notion import callback, cards, world
from wally.exceptions import AuthorizationError
from wally.finance.models import Kind, Locator, ReadIssue, digest
from wally.finance.notion import FieldMapping, SourceMapping
from wally.models.ops import ExecutionStatus
from wally.ops.notion_edits import EditError, PropertyRule


def missing_policy(tmp_path):
    service, human, ctx, api, registry = http_harness(tmp_path)
    target = service.targets["tnb"]
    service.targets["tnb"] = replace(
        target, properties=(replace(target.properties[0], allow_missing=True),)
    )
    api.page["properties"]["Policy"]["select"] = None
    return service, human, ctx, api, registry


def test_missing_allowed_value_is_explicit_and_independently_verified(tmp_path):
    service, _, ctx, api, _ = missing_policy(tmp_path)
    proposal = approved(service, ctx)
    spec = service.store.get_notion_edit(proposal.id)
    assert spec["changes"][0]["before"] is None
    assert spec["before"]["values"]["policy"] is None
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.calls.count("PATCH") == 1
    assert api.calls[-2:] == ["GET", "GET"]


@pytest.mark.parametrize("replacement", ["arbitrary", "", None, False, 0])
def test_missing_disallowed_value_never_proposes_or_dispatches(tmp_path, replacement):
    service, _, ctx, api, _ = missing_policy(tmp_path)
    with pytest.raises(EditError):
        service.propose("tnb", {"policy": replacement}, context=ctx)
    assert "PATCH" not in api.calls


def test_missing_requires_explicit_policy_and_unexpected_value_is_refused(tmp_path):
    service, _, ctx, api, _ = http_harness(tmp_path)
    api.page["properties"]["Policy"]["select"] = None
    with pytest.raises(EditError, match="initialization"):
        propose(service, ctx)
    service.targets["tnb"] = replace(
        service.targets["tnb"],
        properties=(replace(service.targets["tnb"].properties[0], allow_missing=True),),
    )
    api.page["properties"]["Policy"]["select"] = {"name": "unexpected"}
    with pytest.raises(EditError, match="separate review"):
        propose(service, ctx)
    assert "PATCH" not in api.calls


@pytest.mark.parametrize("when", ["after_approval", "during_prompt", "during_credentials"])
def test_missing_to_existing_drift_refuses_dispatch(tmp_path, when):
    service, human, ctx, api, _ = missing_policy(tmp_path)
    proposal = approved(service, ctx)

    def drift():
        api.page["properties"]["Policy"]["select"] = {"name": "fixed_contract"}

    if when == "after_approval":
        drift()
    elif when == "during_prompt":
        human.callback = drift
    else:
        factory = service.backend.write_client

        def client():
            drift()
            return factory()

        service.backend.write_client = client
    if when == "during_credentials":
        result = service.execute(proposal.id, context=ctx)
        assert result.failure_category == "patch_not_dispatched"
    else:
        with pytest.raises(EditError):
            service.execute(proposal.id, context=ctx)
    assert "PATCH" not in api.calls


def test_uncertain_initialization_inspects_and_verifies_without_retry(tmp_path):
    service, _, ctx, api, _ = missing_policy(tmp_path)
    original = api.handle

    def uncertain(request):
        response = original(request)
        if request.method == "PATCH":
            raise httpx.ReadTimeout("synthetic")
        return response

    service.backend.write_client = lambda: httpx.Client(
        transport=httpx.MockTransport(uncertain), base_url="https://api.notion.com/v1"
    )
    proposal = approved(service, ctx)
    attempt = service.execute(proposal.id, context=ctx)
    assert attempt.failure_category == "patch_outcome_uncertain"
    assert not service.inspect_execution(attempt.id, context=ctx)["completion_proven"]
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    assert service.verify(attempt.id, context=ctx).status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.calls.count("PATCH") == 1


def test_rejected_initialization_and_replayed_callback_never_execute(tmp_path):
    original, _, _, api, _ = missing_policy(tmp_path)
    target = replace(original.targets["tnb"], key="Assessment sandbox")
    state = world(tmp_path / "telegram", backend=original.backend, targets=(target,))
    from tests.test_telegram_notion import propose as telegram_propose

    proposal = telegram_propose(state)
    assert "Missing → <b>From source</b>" in cards(state)[0]["text"]
    update = callback(state, code="r")
    state[1].handle_update(update)
    state[1].handle_update(update)
    assert "PATCH" not in api.calls
    with pytest.raises(AuthorizationError):
        state[0].execute(proposal.id, context=state[0].authority.issue("telegram"))
    assert any("remains <b>Missing</b>" in item["text"] for item in state[2].sent)


@pytest.mark.parametrize(
    "field,value",
    [
        ("responsibility", "owner"),
        ("currency", "MYR"),
        ("frequency", "semiannual"),
        ("evidence_mode", "issued_bill"),
        ("due_mode", "from_document"),
    ],
)
def test_selected_stream_select_initializations(tmp_path, field, value):
    service, _, ctx, api, _ = missing_policy(tmp_path)
    service.targets["tnb"] = replace(
        service.targets["tnb"],
        properties=(PropertyRule(field, "policy", (value,), allow_missing=True),),
    )
    api.schema["properties"]["Policy"]["select"]["options"] = [{"name": value}]
    proposal = service.propose("tnb", {"policy": value}, context=ctx)
    service.decide((select(proposal),), context=ctx)
    assert service.execute(proposal.id, context=ctx).status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.patch_body == {"properties": {"policy": {"select": {"name": value}}}}


@pytest.mark.parametrize("expired", [True, False])
def test_initialization_expiry_and_successful_callback_replay(tmp_path, expired):
    original, _, _, api, _ = missing_policy(tmp_path)
    target = replace(original.targets["tnb"], key="Assessment sandbox")
    state = world(tmp_path / "telegram", backend=original.backend, targets=(target,))
    from tests.test_telegram_notion import propose as telegram_propose

    proposal = telegram_propose(state)
    update = callback(state)
    if expired:
        with state[0].store._connect() as conn:
            conn.execute("UPDATE telegram_edit_reviews SET expires_at=0")
    state[1].handle_update(update)
    state[1].handle_update(update)
    assert api.calls.count("PATCH") == (0 if expired else 1)
    attempts = state[0].store.list_executions(proposal_id=proposal.id)
    assert len(attempts) == (0 if expired else 1)
    if attempts:
        assert attempts[0].status == ExecutionStatus.VERIFIED_SUCCESS


def incomplete_finance(service, registry, *, field="amount_policy", kind=Kind.DEFINITION):
    registry.role = "finance"
    source = SourceMapping(uid(1), uid(2), kind, {field: FieldMapping("policy", "select")})
    issue = ReadIssue(kind, source.key, Locator("notion", uid(1), uid(2), uid(10)))
    issues, records = [issue], []
    service.backend.finance = SimpleNamespace(
        sources=lambda: (source,),
        _source_gate=lambda s: None,
        store=SimpleNamespace(read_issues=lambda: issues, records=lambda: records),
    )
    service.targets["tnb"] = replace(
        service.targets["tnb"],
        finance_issue_digest=digest(asdict(issue)),
        evidence_summary="Synthetic issued assessment bill",
        evidence_digest=digest("synthetic"),
    )
    return source, issue, issues, records


def test_incomplete_financial_row_edits_without_registering_or_certifying(tmp_path):
    service, _, ctx, api, registry = missing_policy(tmp_path)
    _, _, _, records = incomplete_finance(service, registry)
    proposal = approved(service, ctx)
    assert service.execute(proposal.id, context=ctx).status == ExecutionStatus.VERIFIED_SUCCESS
    assert records == []  # No canonical record/certification/enablement operation exists.
    assert api.calls.count("PATCH") == 1


@pytest.mark.parametrize("problem", ["issue", "source_failure", "canonical", "mapping", "both"])
def test_incomplete_binding_changes_block_before_dispatch(tmp_path, problem):
    service, _, ctx, api, registry = missing_policy(tmp_path)
    source, issue, issues, records = incomplete_finance(service, registry)
    proposal = approved(service, ctx)
    if problem == "issue":
        issues[:] = [replace(issue, constraints={"account": ("changed",)})]
    elif problem == "source_failure":
        issues.append(ReadIssue(Kind.DEFINITION, source.key, reason="source_unavailable"))
    elif problem == "canonical":
        records.append(SimpleNamespace(candidate=SimpleNamespace(locator=issue.locator)))
    elif problem == "mapping":
        source.fields["amount_policy"] = FieldMapping("other", "select")
    else:
        service.targets["tnb"] = replace(service.targets["tnb"], finance_id="canonical")
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    assert "PATCH" not in api.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "assessment_account"),
        ("charge_key", "assessment_tax"),
        ("obligation_type", "property_tax"),
    ],
)
def test_finite_text_initialization_and_protected_properties(tmp_path, field, value):
    service, _, ctx, api, _ = http_harness(tmp_path)
    service.targets["tnb"] = replace(
        service.targets["tnb"],
        properties=(PropertyRule(field, "policy", (value,), allow_missing=True),),
    )
    api.schema["properties"]["Policy"] = {"id": "policy", "type": "rich_text", "rich_text": {}}
    api.page["properties"]["Policy"] = {"id": "policy", "type": "rich_text", "rich_text": []}
    original = api.handle

    def handle(request):
        if request.method == "PATCH":
            import json

            api.calls.append("PATCH")
            api.patch_body = json.loads(request.content)
            api.page["properties"]["Policy"]["rich_text"] = api.patch_body["properties"]["policy"][
                "rich_text"
            ]
            return httpx.Response(200, json=api.page)
        return original(request)

    service.backend.write_client = lambda: httpx.Client(
        transport=httpx.MockTransport(handle), base_url="https://api.notion.com/v1"
    )
    proposal = service.propose("tnb", {"policy": value}, context=ctx)
    service.decide((select(proposal),), context=ctx)
    result = service.execute(proposal.id, context=ctx)
    assert result.status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.patch_body == {
        "properties": {"policy": {"rich_text": [{"type": "text", "text": {"content": value}}]}}
    }
    assert api.page["properties"]["Business"]["number"] == 42
    service.reconcile("tnb", context=ctx)


def test_code_owned_text_and_select_contracts_reject_generic_fields(tmp_path):
    service, _, ctx, api, _ = missing_policy(tmp_path)
    for field, value in [
        ("namespace", "arbitrary"),
        ("currency", "BTC"),
        ("payment_status", "paid"),
    ]:
        service.targets["tnb"] = replace(
            service.targets["tnb"],
            properties=(PropertyRule(field, "policy", (value,), allow_missing=True),),
        )
        with pytest.raises(EditError):
            service.propose("tnb", {"policy": value}, context=ctx)
    assert "PATCH" not in api.calls


def test_default_contract_extension_preserves_old_target_fingerprint(tmp_path):
    service, _, _, _, _ = http_harness(tmp_path)
    target = service.targets["tnb"]
    legacy = asdict(target)
    for key in ("finance_issue_digest", "evidence_summary", "evidence_digest"):
        del legacy[key]
    for rule in legacy["properties"]:
        del rule["allow_missing"]
    from wally.runtime.confirmation import digest as review_digest

    assert target.fingerprint == review_digest(legacy)
    assert (
        replace(target, properties=(replace(target.properties[0], allow_missing=True),)).fingerprint
        != target.fingerprint
    )


def test_initialization_evidence_changes_require_a_new_exact_review(tmp_path):
    service, _, ctx, api, registry = missing_policy(tmp_path)
    incomplete_finance(service, registry)
    proposal = approved(service, ctx)
    service.targets["tnb"] = replace(service.targets["tnb"], evidence_digest=digest("new"))
    with pytest.raises(EditError):
        service.execute(proposal.id, context=ctx)
    assert "PATCH" not in api.calls
