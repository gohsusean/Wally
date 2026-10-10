"""External modifications remain source information, never new financial authority."""

from dataclasses import replace

import pytest

from tests.finance_fixture import ATTEST, PROOF, catalog_fixture
from tests.test_notion_edits import approved, harness, http_harness, uid
from tests.test_telegram_notion import callback, propose, world
from wally.finance.models import Candidate, FinanceError, Kind, Locator, Scope
from wally.finance.notion import FieldMapping, SourceMapping
from wally.models.ops import ExecutionStatus
from wally.ops.notion_edits import EditError


def financial_world(tmp_path):
    service, human, ctx, api, registry = http_harness(tmp_path)
    catalog, ids, _ = catalog_fixture(tmp_path, service.authority)
    facts = replace(
        catalog.store.get(ids["definition"]).candidate.facts,
        amount_policy="fixed_contract",
        fixed_amount="1.00",
    )
    record = catalog.store.register(
        Candidate(Kind.DEFINITION, Locator("notion", uid(1), uid(2), uid(10)), facts)
    )
    catalog.store.certify(record, Scope.IDENTITY, {}, PROOF, list(ATTEST), {"channel": "fixture"})
    source = SourceMapping(
        uid(1), uid(2), Kind.DEFINITION, {"amount_policy": FieldMapping("policy", "select")}
    )
    catalog.sources = lambda: (source,)
    catalog._source_gate = lambda source: None
    service.backend.finance = catalog
    service.targets["tnb"] = replace(service.targets["tnb"], finance_id=record.id)
    registry.role = "finance"
    return service, ctx, api, catalog, record, ids


def test_external_change_marks_verification_stale_without_overwriting_or_changing_history(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    attempt = service.execute(proposal.id, context=ctx)
    assert service.reconcile("tnb", context=ctx)["verification_current"]
    service.backend.data["tnb"] = "fixed_contract"  # Authorized owner edits Notion directly.
    result = service.reconcile("tnb", context=ctx)
    assert result["state"] == "changed" and not result["verification_current"]
    assert result["origin"] == "unknown"
    assert result["changes"] == [
        {"field": "amount_policy", "before": "source_defined", "after": "fixed_contract"}
    ]
    assert service.store.get_execution(attempt.id).status == ExecutionStatus.VERIFIED_SUCCESS
    assert len(service.backend.writes) == 1
    with pytest.raises(EditError, match="stale"):
        service.verify(attempt.id, context=ctx)
    events = service.reconciliation.events()
    service.reconcile("tnb", context=ctx)
    assert service.reconciliation.events() == events
    service.backend.data["tnb"] = "source_defined"
    result = service.reconcile("tnb", context=ctx)
    assert result["state"] == "needs_review" and not result["verification_current"]
    assert not service.inspect_execution(attempt.id, context=ctx)["completion_proven"]


def test_prior_verified_attempt_backfills_baseline_on_additive_rollout(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    attempt = service.execute(proposal.id, context=ctx)
    with service.store._connect() as conn:
        conn.execute("DELETE FROM notion_trusted_snapshots")  # Isolated rollout fixture only.
    assert service.reconcile("tnb", context=ctx)["verification_current"]
    assert service.verify(attempt.id, context=ctx) == attempt


@pytest.mark.parametrize("business", [False, True])
def test_metadata_and_audit_updates_preserve_certification_but_mapped_changes_invalidate(
    tmp_path, business
):
    service, ctx, api, catalog, record, ids = financial_world(tmp_path)
    service.reconcile("tnb", context=ctx)
    api.page["last_edited_time"] = "2026-10-11T17:22:44.123Z"
    api.page["last_edited_by"] = {"id": "synthetic-actor"}
    api.page["properties"]["Audit History"]["rich_text"] = [{"text": {"content": "new audit"}}]
    if business:
        api.page["properties"]["Policy"]["select"]["name"] = "source_defined"
    result = service.reconcile("tnb", context=ctx)
    assert result["certification_affected"] == business
    assert bool(catalog.store.certificate(record.id, Scope.IDENTITY)) != business
    assert catalog.store.certificate(ids["property"], Scope.IDENTITY)
    assert catalog.store.get(record.id).candidate.facts.amount_policy == "fixed_contract"
    assert "PATCH" not in api.calls
    if business:
        with catalog.store.connect() as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM finance_certificates WHERE object_id=?", (record.id,)
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT COUNT(*) FROM finance_certificate_events").fetchone()[0] > 0
        with pytest.raises(FinanceError):
            catalog.binding(record.id)


def test_unrelated_business_property_stales_full_verification_not_finance_certificate(tmp_path):
    service, ctx, api, catalog, record, _ = financial_world(tmp_path)
    service.reconcile("tnb", context=ctx)
    api.page["properties"]["Business"]["number"] = 8
    result = service.reconcile("tnb", context=ctx)
    assert result["state"] == "changed" and result["other_business_changes"] == 1
    assert not result["certification_affected"]
    assert catalog.store.certificate(record.id, Scope.IDENTITY)
    assert "PATCH" not in api.calls


def test_first_read_detects_certified_metadata_drift(tmp_path):
    service, ctx, api, catalog, record, _ = financial_world(tmp_path)
    api.page["properties"]["Policy"]["select"]["name"] = "source_defined"
    result = service.reconcile("tnb", context=ctx)
    assert result["state"] == "changed" and result["certification_affected"]
    assert not catalog.store.certificate(record.id, Scope.IDENTITY)
    assert "PATCH" not in api.calls


def test_shared_operations_and_finance_database_reconciliation_has_no_nested_write_lock(tmp_path):
    from wally.finance.store import FinanceStore

    service, ctx, api, catalog, record, _ = financial_world(tmp_path)
    # Exercise actual runtime topology: both services share the operational SQLite file.
    old_candidate = record.candidate
    catalog.store = FinanceStore(service.store._path)
    record = catalog.store.register(old_candidate)
    catalog.store.certify(record, Scope.IDENTITY, {}, PROOF, list(ATTEST), {"channel": "fixture"})
    service.targets["tnb"] = replace(service.targets["tnb"], finance_id=record.id)
    service.reconcile("tnb", context=ctx)
    api.page["properties"]["Policy"]["select"]["name"] = "source_defined"
    assert service.reconcile("tnb", context=ctx)["certification_affected"]
    assert "PATCH" not in api.calls


def test_readonly_change_notice_has_review_link_later_and_no_execution_button(tmp_path):
    state = world(tmp_path)
    proposal = propose(state)
    state[1].handle_update(callback(state))
    state[0].backend.data["Sandbox 0"] = "fixed_contract"
    state[0].review(proposal.id, context=state[0].authority.issue("telegram"))
    state[1].sync()
    state[1].deliver_pending()
    alert = next(
        item for item in state[2].sent if item["text"].startswith("📝 Notion change detected")
    )
    assert "From source → Fixed contract" in alert["text"]
    assert "last verified record" in alert["text"]
    assert proposal.id not in alert["text"] and proposal.fingerprint not in alert["text"]
    assert alert["buttons"][0][0]["text"] == "Review in Notion"
    assert alert["buttons"][0][1]["callback_data"].endswith(".n")
    assert len(state[0].backend.writes) == 1
    attempt = state[0].store.list_executions()[0]
    assert "stale" in state[3].completion(proposal.id, attempt)
    before = len(state[2].sent)
    state[1].sync()
    state[1].deliver_pending()
    assert len(state[2].sent) == before


def test_reversion_needs_explicit_verification_and_read_failure_stays_untrusted(tmp_path):
    service, _, ctx = harness(tmp_path)
    proposal = approved(service, ctx)
    attempt = service.execute(proposal.id, context=ctx)
    service.backend.read_failure = True
    with pytest.raises(EditError, match="could not be checked"):
        service.reconcile("tnb", context=ctx)
    service.backend.read_failure = False
    assert not service.reconcile("tnb", context=ctx)["verification_current"]
    assert service.verify(attempt.id, context=ctx) == attempt
    assert service.reconcile("tnb", context=ctx)["verification_current"]
    assert len(service.backend.writes) == 1


def test_reconciliation_tool_authentication_and_read_scope(tmp_path):
    state = world(tmp_path)
    adapter = state[5]

    def call(arguments):
        return adapter.handle(
            {
                "id": 1,
                "method": "tools/call",
                "params": {"name": "reconcile_notion_record", "arguments": arguments},
            }
        )["result"]

    assert not call({"target_key": "Sandbox 0"})["isError"]
    assert call({"target_key": "Sandbox 0", "human_approved": True})["isError"]
    assert call({"target_key": "unregistered"})["isError"]
    assert state[0].backend.writes == []
    assert not state[0].store.list_executions()


def test_explicit_catalog_recertification_is_not_revoked_again_for_old_enum_drift(tmp_path):
    service, ctx, api, catalog, record, _ = financial_world(tmp_path)
    service.reconcile("tnb", context=ctx)
    api.page["properties"]["Policy"]["select"]["name"] = "source_defined"
    assert service.reconcile("tnb", context=ctx)["certification_affected"]
    # Simulate the existing catalog refresh and separately authenticated certification.
    candidate = replace(
        record.candidate,
        facts=replace(record.candidate.facts, amount_policy="source_defined", fixed_amount=""),
    )
    current = catalog.store.register(candidate)
    catalog.store.certify(current, Scope.IDENTITY, {}, PROOF, list(ATTEST), {"channel": "fixture"})
    assert service.reconcile("tnb", context=ctx)["certification_affected"]
    assert catalog.store.certificate(record.id, Scope.IDENTITY)
    assert not service.reconcile("tnb", context=ctx)["verification_current"]
