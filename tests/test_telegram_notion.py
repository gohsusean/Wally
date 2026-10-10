"""Synthetic Codex -> Telegram exact review -> existing scoped Notion runtime."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.test_notion_edits import Backend, http_harness, uid
from tests.test_telegram import FakeTelegram
from wally.codex.config import EditConfig, load_config
from wally.codex.service import (
    CODEX_CHANNEL,
    CODEX_POLICY,
    CODEX_TELEGRAM_POLICY,
    CodexEditAdapter,
)
from wally.exceptions import AuthorizationError
from wally.gateway.service import AdapterRegistration, GatewayRuntime
from wally.models.ops import ExecutionStatus, ProposalStatus
from wally.models.principal import Capability
from wally.ops.notion_edits import EditError, EditTarget, NotionEditService, PropertyRule
from wally.ops.store import OperationsStore
from wally.runtime.principals import PrincipalAuthority
from wally.telegram.ingress import LEASE
from wally.telegram.notion_approvals import (
    WORKER_CHANNEL,
    WORKER_POLICY,
    TelegramEditConfirmation,
    TelegramNotionApprovals,
)
from wally.telegram.poll import build_service, poll_once, telegram_registration
from wally.telegram.service import TelegramConfig, TelegramService, telegram_policy

OWNER = "42"


def world(tmp_path, *, backend=None, targets=None, enabled=True):
    confirmation = TelegramEditConfirmation()
    authority = PrincipalAuthority(
        {
            CODEX_CHANNEL: CODEX_TELEGRAM_POLICY,
            "telegram": telegram_policy(),
            WORKER_CHANNEL: WORKER_POLICY,
        },
        human_confirmers={"telegram": confirmation, WORKER_CHANNEL: confirmation},
    )
    store = OperationsStore(tmp_path / "ops.db")
    targets = targets or tuple(
        EditTarget(
            f"Sandbox {n}",
            uid(1),
            uid(2),
            uid(10 + n),
            (PropertyRule("amount_policy", "policy", ("fixed_contract", "source_defined")),),
        )
        for n in range(2)
    )
    policies = [
        EditConfig(
            targets=targets,
            telegram_targets=tuple(t.key for t in targets),
            telegram_writes_enabled=enabled,
        )
    ]
    edits = NotionEditService(
        store,
        authority,
        backend or Backend(),
        targets,
        writes_enabled=True,
        targets_provider=lambda: policies[0].targets,
        write_gate=lambda: policies[0].telegram_writes_enabled and confirmation.dispatch_allowed(),
    )
    approvals = TelegramNotionApprovals(edits, confirmation, lambda: policies[0], OWNER)
    config = TelegramConfig("synthetic-token", OWNER, "synthetic-gateway", OWNER)
    local = AdapterRegistration("codex", CODEX_CHANNEL, "synthetic-local", True)
    runtime = GatewayRuntime(
        store, authority, (telegram_registration(config), local), notion_edits=edits
    )
    transport = FakeTelegram()
    telegram = TelegramService(runtime, store, config, transport, notion_approvals=approvals)
    adapter = CodexEditAdapter(runtime, local, telegram_approval=True)
    return edits, telegram, transport, approvals, policies, adapter


def propose(world, index=0, after="source_defined"):
    edits, _, _, _, policies, adapter = world
    result = adapter.handle(
        {
            "id": index + 1,
            "method": "tools/call",
            "params": {
                "name": "propose_notion_edit",
                "arguments": {
                    "target_key": policies[0].targets[index].key,
                    "replacements": {"policy": after},
                },
            },
        }
    )["result"]
    assert not result["isError"], result
    return edits.store.get_proposal(result["structuredContent"]["id"])


def cards(world):
    _, telegram, transport, _, _, _ = world
    telegram.sync()
    telegram.deliver_pending()
    return [item for item in transport.sent if item["buttons"]]


def callback(world, card=0, code="e", update_id=1):
    _, telegram, transport, _, _, _ = world
    shown = cards(world)
    item = next(
        item
        for item in shown
        if (
            item["text"].startswith("📝 Notion updates")
            if card == 2
            else (
                "\n" + world[4][0].targets[card].key + "\n" in item["text"]
                and item["text"].startswith("📝 Notion update\n")
            )
        )
    )
    message_id = transport.sent.index(item) + 1
    nonce = item["buttons"][0][0]["callback_data"].split(".")[0]
    update = {
        "update_id": update_id,
        "callback_query": {
            "id": f"synthetic-callback-{update_id}",
            "from": {"id": 42, "is_bot": False},
            "data": nonce + "." + code,
            "message": {"message_id": message_id, "chat": {"id": 42, "type": "private"}},
        },
    }
    return update


def test_codex_to_telegram_independent_http_verification_and_benign_audit(tmp_path):
    original, _, _, api, _ = http_harness(tmp_path)
    target = replace(original.targets["tnb"], key="Synthetic sandbox")
    state = world(tmp_path, backend=original.backend, targets=(target,))
    edits, telegram, transport, _, _, adapter = state
    proposal = propose(state)
    card = cards(state)[0]
    assert "Fixed contract → From source" in card["text"]
    assert "Amount policy" in card["text"]
    assert proposal.fingerprint not in card["text"]
    assert "PATCH" not in api.calls
    telegram.handle_update(callback(state))
    attempt = edits.store.list_executions()[0]
    assert attempt.status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.calls.count("PATCH") == 1
    assert api.calls[-2:] == ["GET", "GET"]
    assert api.patch_body == {"properties": {"policy": {"select": {"name": "source_defined"}}}}
    assert edits.store.get_proposal(proposal.id).decision_origin == "telegram"
    assert attempt.origin == WORKER_CHANNEL
    assert any("Verified successfully" in item["text"] for item in transport.sent)
    with edits.store._connect() as conn:  # noqa: SLF001
        receipts = conn.execute("SELECT record,presentation FROM human_confirmations").fetchall()
        row = conn.execute("SELECT * FROM telegram_edit_reviews").fetchone()
    assert len(receipts) == 2
    assert {json.loads(row["record"])["purpose"] for row in receipts} == {
        "decide_proposal",
        "execute_notion_edit",
    }
    assert row["owner_user_id"] == OWNER and row["authorized_at"] < row["expires_at"]
    result = adapter.handle(
        {
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "inspect_notion_execution",
                "arguments": {"execution_id": attempt.id},
            },
        }
    )
    assert result["result"]["structuredContent"]["completion_proven"]
    assert not {"execute_notion_edit", "decide_notion_edits"} & {
        tool["name"]
        for tool in adapter.handle({"id": 8, "method": "tools/list"})["result"]["tools"]
    }
    assert Capability.EXECUTE_NOTION_EDIT in CODEX_POLICY.capabilities  # Dormant path preserved.


@pytest.mark.parametrize(
    "code,status", [("r", ProposalStatus.REJECTED), ("l", ProposalStatus.DEFERRED)]
)
def test_reject_and_defer_are_durable_and_never_execute(tmp_path, code, status):
    state = world(tmp_path)
    proposal = propose(state)
    edits, telegram, transport, _, _, _ = state
    telegram.handle_update(callback(state, code=code))
    assert edits.store.get_proposal(proposal.id).status == status
    assert edits.backend.writes == []
    assert any("No changes made" in item["text"] for item in transport.sent)
    telegram.handle_update(callback(state, code="e", update_id=2))
    assert edits.backend.writes == []
    if code == "l":
        with edits.store._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE proposals SET defer_until=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), proposal.id),
            )
            conn.execute("UPDATE telegram_edit_reviews SET expires_at=0")
        telegram.sync()
        assert edits.store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED
        assert edits.backend.writes == []


def test_individual_cards_support_partial_batch_and_full_card_lists_all(tmp_path):
    state = world(tmp_path)
    first, second = propose(state, 0), propose(state, 1)
    edits, telegram, _, _, _, _ = state
    full = cards(state)
    assert len(full) == 3
    batch = next(item for item in full if item["text"].startswith("📝 Notion updates"))
    assert "Sandbox 0" in batch["text"] and "Sandbox 1" in batch["text"]
    assert first.id not in batch["text"] and second.id not in batch["text"]
    telegram.handle_update(callback(state, card=0))
    telegram.handle_update(callback(state, card=1, code="r", update_id=2))
    assert edits.store.get_proposal(first.id).status == ProposalStatus.APPROVED
    assert edits.store.get_proposal(second.id).status == ProposalStatus.REJECTED
    assert len(edits.backend.writes) == 1
    telegram.handle_update(callback(state, card=2, update_id=3))
    assert len(edits.backend.writes) == 1


def test_approve_all_records_has_separate_execution_authorizations(tmp_path):
    state = world(tmp_path)
    propose(state, 0)
    propose(state, 1)
    state[1].handle_update(callback(state, card=2))
    assert len(state[0].backend.writes) == 2
    assert all(
        ex.status == ExecutionStatus.VERIFIED_SUCCESS for ex in state[0].store.list_executions()
    )
    with state[0].store._connect() as conn:  # noqa: SLF001
        assert conn.execute("SELECT COUNT(*) FROM human_confirmations").fetchone()[0] == 3


@pytest.mark.parametrize(
    "tamper", ["user", "group", "forward", "message", "nonce", "bot", "action"]
)
def test_forged_or_wrong_identity_callbacks_cannot_authorize(tmp_path, tamper):
    state = world(tmp_path)
    proposal = propose(state)
    update = callback(state)
    cb = update["callback_query"]
    if tamper == "user":
        cb["from"]["id"] = 99
    elif tamper == "group":
        cb["message"]["chat"]["type"] = "group"
    elif tamper == "forward":
        cb["message"]["forward_origin"] = {"type": "user"}
    elif tamper == "message":
        cb["message"]["message_id"] = 999
    elif tamper == "nonce":
        cb["data"] = "forged-nonce.e"
    elif tamper == "bot":
        cb["from"]["is_bot"] = True
    else:
        cb["data"] = cb["data"][:-1] + "a"
    state[1].handle_update(update)
    assert state[0].store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED
    assert state[0].backend.writes == []


@pytest.mark.parametrize(
    "drift",
    ["version", "expired", "superseded", "source", "scope", "policy", "gate", "review_expired"],
)
def test_stale_scope_policy_or_state_stops_execution(tmp_path, drift):
    state = world(tmp_path)
    proposal = propose(state)
    update = callback(state)
    edits, telegram, _, _, policies, _ = state
    if drift in {"version", "expired", "superseded"}:
        columns = {
            "version": ("fingerprint", "changed"),
            "expired": ("expires_at", "2000-01-01T00:00:00+00:00"),
            "superseded": ("status", "superseded"),
        }
        column, value = columns[drift]
        with edits.store._connect() as conn:  # noqa: SLF001
            conn.execute(f"UPDATE proposals SET {column}=? WHERE id=?", (value, proposal.id))
    elif drift == "source":
        edits.backend.data[policies[0].targets[0].key] = "source_defined"
    elif drift == "scope":
        with edits.store._connect() as conn:  # noqa: SLF001
            conn.execute("UPDATE telegram_edit_reviews SET scope='[]'")
    elif drift == "policy":
        policies[0] = replace(policies[0], telegram_targets=())
    elif drift == "gate":
        policies[0] = replace(policies[0], telegram_writes_enabled=False)
    else:
        with edits.store._connect() as conn:  # noqa: SLF001
            conn.execute("UPDATE telegram_edit_reviews SET expires_at=0")
    telegram.handle_update(update)
    assert edits.backend.writes == []


def test_duplicate_replayed_concurrent_and_restart_callbacks_never_repeat(tmp_path):
    state = world(tmp_path)
    propose(state)
    update = callback(state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(state[1].handle_update, [update, update]))
    assert len(state[0].backend.writes) == 1
    state[1].handle_update(callback(state, update_id=2))
    restarted = world(tmp_path, backend=state[0].backend)
    restarted[1].handle_update(update)
    assert len(state[0].backend.writes) == 1
    assert restarted[1].ingress.offset() >= 3


@pytest.mark.parametrize("failure", ["timeout", "protected", "interrupted", "prepatch"])
def test_uncertain_failure_and_interruption_never_retry_and_reconcile_readonly(tmp_path, failure):
    original, _, _, api, _ = http_harness(tmp_path)
    target = replace(original.targets["tnb"], key="Synthetic sandbox")
    state = world(tmp_path, backend=original.backend, targets=(target,))
    proposal = propose(state)
    update = callback(state)
    if failure == "timeout":
        api.side_effect = lambda: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic timeout"))
    elif failure == "protected":
        api.side_effect = lambda: api.page["properties"]["Business"].update(number=7)
    elif failure == "interrupted":
        api.side_effect = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    else:
        original.backend.write_client = lambda: (_ for _ in ()).throw(
            RuntimeError("private canary")
        )
    if failure == "interrupted":
        with pytest.raises(KeyboardInterrupt):
            state[1].handle_update(update)
    else:
        state[1].handle_update(update)
    attempt = state[0].store.list_executions()[0]
    assert attempt.status != ExecutionStatus.VERIFIED_SUCCESS
    assert api.calls.count("PATCH") == (0 if failure == "prepatch" else 1)
    observed = state[0].inspect_execution(
        attempt.id, context=state[0].authority.issue(WORKER_CHANNEL)
    )
    assert not observed["write_repeated"] and not observed["claim_released"]
    state[1].handle_update(callback(state, update_id=2))
    assert api.calls.count("PATCH") == (0 if failure == "prepatch" else 1)
    assert state[0].store.get_proposal(proposal.id).status == ProposalStatus.APPROVED
    if failure == "timeout":
        assert observed["observation"] == "approved_state_observed"
        verified = state[0].verify(attempt.id, context=state[0].authority.issue(WORKER_CHANNEL))
        assert verified.status == ExecutionStatus.VERIFIED_SUCCESS
        assert api.calls.count("PATCH") == 1


def test_no_callback_scope_cannot_authorize_and_telegram_has_no_execute(tmp_path):
    state = world(tmp_path)
    edits, telegram, _, _, _, _ = state
    proposal = propose(state)
    for channel, cap in [
        ("telegram", Capability.DECIDE_PROPOSAL),
        (WORKER_CHANNEL, Capability.EXECUTE_NOTION_EDIT),
    ]:
        with pytest.raises(AuthorizationError):
            edits.authority.confirm_human(
                edits.authority.issue(channel), cap, presentation={"model_says": "owner approved"}
            )
    assert (
        not {
            Capability.EXECUTE_PROPOSAL,
            Capability.EXECUTE_NOTION_EDIT,
            Capability.CERTIFY_FINANCIAL_DATA,
        }
        & telegram_policy().capabilities
    )
    assert not {Capability.EXECUTE_PROPOSAL, Capability.CERTIFY_FINANCIAL_DATA} & (
        WORKER_POLICY.capabilities
    )
    denied = telegram.runtime.dispatch(
        adapter_id="telegram",
        credential="synthetic-gateway",
        op="execute_notion_edit",
        body={"proposal_id": proposal.id},
    )
    assert not denied.ok
    with pytest.raises(EditError):
        edits.propose(
            "unregistered",
            {"policy": "source_defined"},
            context=edits.authority.issue(CODEX_CHANNEL),
        )
    with pytest.raises(EditError):
        edits.propose("Sandbox 0", {"amount": "999"}, context=edits.authority.issue(CODEX_CHANNEL))


def test_revocation_while_resolving_credentials_and_lost_lease_block_patch(tmp_path):
    original, _, _, api, _ = http_harness(tmp_path)
    target = replace(original.targets["tnb"], key="Synthetic sandbox")
    state = world(tmp_path, backend=original.backend, targets=(target,))
    propose(state)
    update = callback(state)
    factory = original.backend.write_client

    def revoked():
        state[4][0] = replace(state[4][0], telegram_targets=())
        return factory()

    original.backend.write_client = revoked
    state[1].handle_update(update)
    assert "PATCH" not in api.calls
    assert state[0].store.list_executions()[0].failure_category == "patch_not_dispatched"


def test_full_review_size_never_hides_changes(tmp_path):
    state = world(tmp_path)
    # Force one individually valid card to exceed the safe complete-review budget.
    target = replace(state[4][0].targets[0], key="x" * 4000)
    state[4][0] = replace(state[4][0], targets=(target,), telegram_targets=(target.key,))
    propose(state)
    assert cards(state) == []
    assert state[0].backend.writes == []


def test_policy_rejects_high_risk_telegram_targets_and_keeps_defaults_off(tmp_path):
    import yaml

    target = EditTarget(
        "sandbox", uid(1), uid(2), uid(10), (PropertyRule("bank_account", "bank", ("x",)),)
    )
    from dataclasses import asdict

    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "targets": [asdict(target)], "telegram_targets": [target.key]}
        )
    )
    with pytest.raises(EditError):
        load_config(path)
    config = load_config(tmp_path / "missing")
    assert not config.telegram_writes_enabled and not config.telegram_targets


def test_replay_cursor_seam_late_chat_status_and_lease_fencing(tmp_path):
    state = world(tmp_path)
    ingress = state[1].ingress
    now = datetime.now(UTC)
    ingress.record(17, "ignored", now)
    state[1].handle_update({"update_id": 17})
    assert ingress.offset() == 18
    assert ingress.try_acquire("a", now)
    assert ingress.renew("a", now + timedelta(seconds=30))
    assert not ingress.try_acquire("b", now + LEASE)
    assert not ingress.renew("a", now + LEASE * 2)
    assert ingress.try_acquire("b", now + LEASE * 2)
    assert not ingress.owns("a", now + LEASE * 2)
    args = dict(
        kind="verification_completed",
        execution_id="ex",
        proposal_id="p",
        fingerprint="fp",
        owner_user_id=OWNER,
        now=now,
    )
    state[1].outbox.enqueue_status(**args, chat_id="")
    row = state[1].outbox.enqueue_status(**args, chat_id=OWNER)
    assert row.chat_id == OWNER


def test_poll_lease_loss_before_callback_never_executes(tmp_path):
    state = world(tmp_path)
    propose(state)
    update = callback(state)

    def updates(offset):
        with state[0].store._connect() as conn:  # noqa: SLF001
            conn.execute("UPDATE telegram_lease SET holder='other'")
        return [update]

    state[2].get_updates = updates
    assert not poll_once(state[1], state[2], "poll-holder")
    assert state[0].backend.writes == []


def test_empty_poller_build_does_not_contact_notion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "wally.codex.__main__.httpx.Client",
        lambda *a, **kw: pytest.fail("must not contact live provider"),
    )
    settings = SimpleNamespace(
        project_root=tmp_path,
        ops_database=tmp_path / "ops.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit",
        dry_run=False,
    )
    service = build_service(settings, TelegramConfig("test", OWNER, "test"), FakeTelegram())
    assert service is not None
    assert not service._notion_approvals.policy().telegram_writes_enabled  # noqa: SLF001


def test_frequency_uses_same_exact_telegram_authorization(tmp_path):
    original, _, _, api, _ = http_harness(tmp_path)
    target = replace(
        original.targets["tnb"],
        key="Synthetic recurring task",
        properties=(PropertyRule("frequency", "policy", ("monthly", "quarterly")),),
    )
    api.page["properties"]["Policy"]["select"]["name"] = "monthly"
    api.schema["properties"]["Policy"]["select"]["options"] = [
        {"name": "monthly"},
        {"name": "quarterly"},
    ]
    state = world(tmp_path, backend=original.backend, targets=(target,))
    propose(state, after="quarterly")
    assert "Frequency" in cards(state)[0]["text"]
    state[1].handle_update(callback(state))
    assert state[0].store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    assert api.page["properties"]["Policy"]["select"]["name"] == "quarterly"


@pytest.mark.parametrize("outcome", ["verified", "unattempted", "uncertain", "denied"])
def test_telegram_preserves_certification_invalidation_and_no_recertification(tmp_path, outcome):
    from tests.finance_fixture import ATTEST, PROOF, catalog_fixture
    from wally.finance.models import Candidate, Kind, Locator, Scope
    from wally.finance.notion import FieldMapping, SourceMapping

    original, _, _, api, registry = http_harness(tmp_path)
    catalog, ids, _ = catalog_fixture(tmp_path, original.authority)
    candidate = Candidate(
        Kind.DEFINITION,
        Locator("notion", uid(1), uid(2), uid(10)),
        catalog.store.get(ids["definition"]).candidate.facts,
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
    catalog._source_gate = lambda source: None
    original.backend.finance = catalog
    registry.role = "finance"
    target = replace(
        original.targets["tnb"], key="Synthetic financial fixture", finance_id=record.id
    )
    state = world(tmp_path, backend=original.backend, targets=(target,))
    propose(state)
    update = callback(state, code="r" if outcome == "denied" else "e")
    if outcome == "unattempted":
        original.backend.write_client = lambda: (_ for _ in ()).throw(RuntimeError("fixture"))
    elif outcome == "uncertain":
        api.side_effect = lambda: (_ for _ in ()).throw(httpx.ReadTimeout("fixture"))
    state[1].handle_update(update)
    if outcome == "denied":
        assert catalog.store.certificate(record.id, Scope.IDENTITY)
        assert "PATCH" not in api.calls
    else:
        assert catalog.store.certificate(record.id, Scope.IDENTITY) is None
        assert catalog.store.get(record.id).invalid
    assert catalog.store.certificate(ids["property"], Scope.IDENTITY) is not None


def test_loss_of_polling_authority_during_credential_resolution_blocks_patch(tmp_path):
    original, _, _, api, _ = http_harness(tmp_path)
    target = replace(original.targets["tnb"], key="Synthetic sandbox")
    state = world(tmp_path, backend=original.backend, targets=(target,))
    propose(state)
    update = callback(state)
    factory = original.backend.write_client

    def lost():
        state[1].set_dispatch_guard(lambda: False)
        return factory()

    original.backend.write_client = lost
    state[1].handle_update(update)
    assert "PATCH" not in api.calls
    assert state[0].store.list_executions()[0].failure_category == "patch_not_dispatched"


def test_disallowed_target_does_not_get_a_generic_approval_card(tmp_path):
    state = world(tmp_path)
    propose(state)
    state[4][0] = replace(state[4][0], telegram_targets=())
    assert cards(state) == []
    assert state[0].backend.writes == []


def test_expired_cards_wait_for_owner_and_fresh_button_has_new_nonce(tmp_path):
    state = world(tmp_path)
    propose(state)
    stale = callback(state)
    with state[0].store._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE telegram_edit_reviews SET expires_at=0")
    before = len(state[2].sent)
    state[1].sync()
    state[1].deliver_pending()
    assert len(state[2].sent) == before  # No fifteen-minute notification loop.
    state[1].handle_update(stale)
    assert state[0].backend.writes == []
    fresh = [item for item in state[2].sent if item["buttons"]][-1]
    assert fresh["buttons"][0][0]["callback_data"] != stale["callback_query"]["data"]
    assert len(state[2].sent) == before + 1


def test_codex_default_telegram_and_explicit_dormant_local_modes(tmp_path):
    from types import SimpleNamespace

    from wally.codex.__main__ import build_adapter

    settings = SimpleNamespace(
        project_root=tmp_path,
        ops_database=tmp_path / "ops.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit",
        dry_run=False,
    )
    default = build_adapter(settings)
    local = build_adapter(settings, approval_channel="local")
    assert len(default.handle({"id": 1, "method": "tools/list"})["result"]["tools"]) == 6
    assert len(local.handle({"id": 1, "method": "tools/list"})["result"]["tools"]) == 8
    status = default.handle(
        {
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_notion_edit_status", "arguments": {}},
        }
    )["result"]["structuredContent"]
    assert status["approval_channel"] == "telegram" and not status["telegram_writes_enabled"]
    assert status["telegram_targets"] == []
