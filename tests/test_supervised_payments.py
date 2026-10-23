"""Offline adverse payment/Telegram/auth/Notion cases; no live provider startup."""

import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime

import httpx
import pytest

from tests.finance_fixture import catalog_fixture
from wally.exceptions import AuthorizationError, ProviderUnavailableError
from wally.finance.models import FinanceError, Scope
from wally.finance.payment_demo import (
    CANARIES,
    ORIGIN,
    TOTP_REF,
    DemoSource,
    build_demo,
    prepare_card,
)
from wally.finance.payments import PAYMENT_WORKER, CertifiedPaymentSource
from wally.models.browser import (
    BrowserActionType,
    BrowserSession,
    BrowserStepResult,
    BrowserStepStatus,
)
from wally.models.ops import ExecutionStatus, MatterStatus, ProposalStatus
from wally.models.principal import Capability, Principal, RequestContext
from wally.ops.decisions import UserDecision, apply_user_decision
from wally.ops.proposal_reconcile import ProposalReconciler
from wally.runtime.principals import PrincipalAuthority


class FastBrowser:
    """Selected-field browser contract fake; real Chromium runs in payment_demo."""

    def __init__(self):
        self.source = DemoSource()
        self.quote_bill = self.source.bill
        self.expiry = time.time() + 600
        self.submissions = 0
        self.ledger_reads = 0
        self.sessions = {}
        self.closed = 0
        self.challenge = False
        self.auth_fail = False
        self.submit_fail = False
        self.ledger_status = "settled"
        self.receipt = "SANDBOX-RECEIPT-001"
        self.on_fill = lambda: None
        self.on_pay = lambda: None
        self.on_ledger = lambda: None

    def open_session(self, *, url, allowed_origins):
        assert allowed_origins == (ORIGIN,)
        key = str(len(self.sessions) + self.closed + 1)
        self.sessions[key] = url
        return BrowserSession(session_id=key, url=url)

    def read_fields(self, session_id, selectors):
        fields = {
            key: getattr(self.quote_bill, key)
            for key in ("payee", "destination", "amount", "currency", "reference")
        }
        fields.update(
            transaction="sandbox-prepared-001",
            funding="Sandbox saved card ending 1234",
            expires_at=str(self.expiry),
            duplicate_check="clear",
            fee="0.00",
        )
        if self.sessions[session_id].endswith("/ledger"):
            self.ledger_reads += 1
            self.on_ledger()
            fields.update(
                receipt=self.receipt,
                status=self.ledger_status if self.submissions == 1 else "pending",
            )
        return {key: fields[key] for key in selectors}

    def has_selector(self, *_):
        return self.challenge

    def run_actions(self, session_id, actions):
        for action in actions:
            if action.action_type == BrowserActionType.FILL:
                assert action.parameters["value"] in CANARIES
                self.on_fill()
            if action.action_type == BrowserActionType.VERIFY_AUTH and self.auth_fail:
                raise RuntimeError(CANARIES[1])
            if action.parameters.get("selector") == "#pay-once":
                self.on_pay()
                self.submissions += 1
                if self.submit_fail:
                    raise RuntimeError(CANARIES[1])
        return BrowserStepResult(session_id=session_id, status=BrowserStepStatus.COMPLETED)

    def close_session(self, session_id):
        self.sessions.pop(session_id)
        self.closed += 1


@pytest.fixture
def world(tmp_path):
    state = build_demo(tmp_path, browser=FastBrowser())
    state.browser.source = state.source
    yield state
    state.notion.client.close()


def run(world, update=None):
    proposal, callback = prepare_card(world)
    if update:
        update(callback)
    world.telegram.handle_update(callback)
    return proposal, callback


def worker(world):
    return world.authority.issue(PAYMENT_WORKER)


def attempt(world):
    return world.store.list_executions()[0]


def test_full_telegram_flow_and_replays(world, tmp_path):
    proposal, callback = run(world)
    assert attempt(world).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.notion.creates == world.notion.reads == 1
    assert world.browser.ledger_reads == 1
    assert len(world.secrets.resolve_calls) == 3
    assert not world.browser.sessions
    assert "Notion updated" in world.approvals.result(proposal.id)
    assert world.store.get_proposal(proposal.id).decision_origin == "telegram"
    assert world.store.get_matter(proposal.matter_id).status == MatterStatus.OPEN
    world.telegram.handle_update(callback)
    callback["update_id"] = 2
    world.telegram.handle_update(callback)
    assert world.browser.submissions == 1
    retained = (tmp_path / "operations.db").read_bytes()
    public = json.dumps({"messages": world.transport.sent, "attempt": asdict(attempt(world))})
    for canary in CANARIES:
        assert canary.encode() not in retained
        assert canary not in public
    with pytest.raises(FinanceError):
        world.payments.prepare(world.source.bill.instance_id, context=world.authority.issue("cli"))


def test_reject_never_resolves_secrets(world):
    proposal, _ = run(
        world,
        lambda u: u["callback_query"].update(
            data=u["callback_query"]["data"].removesuffix(".e") + ".r"
        ),
    )
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.REJECTED
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["from"].update(id=43),
        lambda c: c["from"].update(is_bot=True),
        lambda c: c["message"].update(message_id=99),
        lambda c: c["message"].update(date=0),
        lambda c: c["message"]["chat"].update(type="group"),
        lambda c: c["message"]["chat"].update(id=43),
        lambda c: c["message"].update(forward_origin={"type": "user"}),
        lambda c: c.update(data="not-a-real-nonce.e"),
    ],
)
def test_foreign_copied_or_undelivered_callback_cannot_pay(world, change):
    _, callback = prepare_card(world)
    change(callback["callback_query"])
    world.telegram.handle_update(callback)
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0


def test_expired_review(world):
    _, callback = prepare_card(world)
    with world.store._connect() as c:
        c.execute("UPDATE telegram_payment_reviews SET expires_at=0")
    world.telegram.handle_update(callback)
    assert not world.store.list_executions()


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "101.00"),
        ("destination", "OTHER-ACCOUNT"),
        ("reference", "OTHER-INVOICE"),
        ("catalog_binding", "c" * 64),
        ("period_start", "2026-12-31"),
    ],
)
def test_changed_bill_requires_new_review(world, field, value):
    _, callback = prepare_card(world)
    world.source.bill = replace(world.source.bill, **{field: value})
    world.telegram.handle_update(callback)
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0


def test_policy_or_matter_drift_after_credentials(world):
    def drift():
        world.payments.enabled = lambda: False

    world.browser.on_fill = drift
    run(world)
    assert world.browser.submissions == 0
    assert attempt(world).outcome == "not_submitted"


def test_amount_drift_after_credentials(world):
    world.browser.on_fill = lambda: setattr(
        world.source, "bill", replace(world.source.bill, amount="999.00")
    )
    run(world)
    assert world.browser.submissions == 0
    assert attempt(world).outcome == "not_submitted"


def test_callback_cannot_resume_after_worker_lease_loss(world):
    world.browser.on_fill = lambda: world.telegram.set_dispatch_guard(lambda: False)
    run(world)
    assert world.browser.submissions == 0


@pytest.mark.parametrize("kind", ["auth_fail", "challenge"])
def test_auth_failure_or_mandatory_challenge_is_an_exception(world, kind):
    setattr(world.browser, kind, True)
    proposal, _ = run(world)
    assert world.browser.submissions == 0
    assert not world.browser.sessions
    assert attempt(world).outcome == (
        "challenge_required" if kind == "challenge" else "not_submitted"
    )
    assert "Notion updated" not in world.approvals.result(proposal.id)


def test_uncertain_submission_reconciles_without_resubmitting(world):
    world.browser.submit_fail = True
    proposal, callback = run(world)
    assert attempt(world).status == ExecutionStatus.EXECUTED_UNVERIFIED
    assert world.notion.creates == 0
    world.telegram.handle_update({**callback, "update_id": 2})
    assert world.browser.submissions == 1
    world.payments.reconcile(proposal.id, context=worker(world))
    assert attempt(world).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.browser.submissions == world.notion.creates == 1


@pytest.mark.parametrize("status", ["submitted", "pending", "failed", "unknown"])
def test_login_or_submit_response_is_not_settlement(world, status):
    world.browser.ledger_status = status
    proposal, _ = run(world)
    expected = (ExecutionStatus.FAILED if status == "failed"
                else ExecutionStatus.EXECUTED_UNVERIFIED)
    assert attempt(world).status == expected
    assert world.notion.creates == 0
    result = world.approvals.result(proposal.id)
    assert ("failure" if status == "failed" else "unconfirmed") in result


def test_interruption_after_claim_retains_duplicate_block(world, monkeypatch):
    monkeypatch.setattr(world.store, "insert_execution", lambda _: False)
    proposal, _ = run(world)
    assert world.browser.submissions == 0
    assert world.secrets.resolve_calls == []
    with pytest.raises(FinanceError):
        world.payments.prepare(world.source.bill.instance_id, context=world.authority.issue("cli"))
    assert "unconfirmed" in world.approvals.result(proposal.id)


def test_manual_claim_blocks_automated_route(world):
    bill = world.source.bill
    with world.store._connect() as c:
        c.execute(
            "INSERT INTO manual_payment_attempts(id,obligation,facts,state,created_at) "
            "VALUES (?,?,?,'uncertain','now')",
            ("manual", bill.obligation, "{}"),
        )
    with pytest.raises(FinanceError, match="manual or automated"):
        world.payments.prepare(bill.instance_id, context=world.authority.issue("cli"))


def test_changed_amount_cannot_bypass_occurrence_claim(world):
    proposal, _ = run(world)
    changed = replace(world.source.bill, amount="100.01", reference="reissued-invoice")
    assert changed.obligation == world.source.bill.obligation
    with pytest.raises(FinanceError):
        world.payments._duplicates(changed)
    world.payments.reconcile(proposal.id, context=worker(world))
    assert world.browser.submissions == world.notion.creates == 1


def test_notion_ambiguous_create_recovers_by_read_only(world):
    original = world.notion.handle

    def lose_response(request):
        response = original(request)
        if request.method == "POST" and request.url.path == "/v1/pages":
            raise httpx.ReadTimeout(CANARIES[1])
        return response

    world.notion.handle = lose_response
    proposal, _ = run(world)
    assert attempt(world).status == ExecutionStatus.VERIFIED_SUCCESS
    assert "unconfirmed" in world.approvals.result(proposal.id)
    world.payments.reconcile(proposal.id, context=worker(world))
    assert "Notion updated" in world.approvals.result(proposal.id)
    assert world.browser.submissions == world.notion.creates == 1


def test_existing_history_never_pays(world):
    world.notion.pages["existing"] = {"id": "existing"}
    with pytest.raises(FinanceError, match="already contains"):
        prepare_card(world)


def test_pre2027_baseline_is_not_a_payable_bill(world):
    world.source.bill = replace(
        world.source.bill, period_start="2026-12-01", period_end="2026-12-31"
    )
    with pytest.raises(FinanceError, match="Through-2026"):
        prepare_card(world)


def test_source_policy_and_forged_authority_cannot_authorize_payment(world):
    proposal, _ = prepare_card(world)
    for context in (
        None,
        RequestContext(Principal("owner", PAYMENT_WORKER, "forged"), "x"),
        world.authority.issue("telegram"),
        world.authority.issue("cli"),
    ):
        with pytest.raises(AuthorizationError):
            world.payments.execute(proposal.id, context=context)
    with pytest.raises(Exception, match="scoped review"):
        apply_user_decision(
            world.store,
            proposal.id,
            decision=UserDecision.APPROVE,
            authority=world.authority,
            context=world.authority.issue("cli"),
            now=datetime.now(UTC),
        )
    assert not world.secrets.resolve_calls


def test_unvalidated_live_route_cannot_be_enabled_by_flag(world):
    world.provider.profile = replace(world.provider.profile, sandbox=False)
    with pytest.raises(FinanceError, match="unvalidated"):
        prepare_card(world)


def test_generic_reconciler_preserves_payment_review(world):
    proposal, _ = prepare_card(world)
    ProposalReconciler(world.store).reconcile(now=datetime.now(UTC))
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED


def test_totp_never_resolves_without_authorization(world):
    with pytest.raises(ProviderUnavailableError):
        world.payments.resolver.resolve_totp(TOTP_REF, authorized=False)
    assert world.secrets.resolve_calls == []
    for reference in (TOTP_REF + "?attribute=secret", "keychain://a/b", TOTP_REF + "#seed"):
        with pytest.raises(ProviderUnavailableError):
            world.payments.resolver.resolve_totp(reference, authorized=True)
    assert world.secrets.resolve_calls == []


def test_certificate_source_reuses_exact_records_and_enablement(tmp_path):
    from tests.finance_fixture import ATTEST, PROOF
    from wally.finance.models import Kind

    authority = PrincipalAuthority()
    catalog, ids, _ = catalog_fixture(tmp_path, authority, portal=True)
    prior = catalog.store.get(ids["instance"])
    facts = {
        **asdict(prior.candidate.facts),
        "occurrence": "2027-01",
        "period_start": "2027-01-01",
        "period_end": "2027-01-31",
        "invoice_reference": "INV2027",
    }
    instance = catalog.register_local(Kind.INSTANCE, facts, context=authority.issue("cli"))
    catalog.certify(
        instance.id,
        Scope.IDENTITY,
        evidence=PROOF,
        attestations=ATTEST,
        context=authority.issue("cli"),
    )
    source = CertifiedPaymentSource(catalog)
    bill = source.read(instance.id)
    assert bill.amount == "100.00"
    assert bill.catalog_binding == catalog.binding(instance.id)
    assert bill.reference == "INV2027"
    catalog.store.invalidate_scope(instance.id, Scope.IDENTITY, "contradictory_evidence")
    with pytest.raises(FinanceError):
        source.read(instance.id)


def test_drifted_receipt_preserves_original_evidence(world):
    proposal, _ = run(world)
    world.browser.receipt = "CONTRADICTORY-RECEIPT"
    with pytest.raises(FinanceError, match="Settlement evidence changed"):
        world.payments.reconcile(proposal.id, context=worker(world))
    assert world.browser.submissions == world.notion.creates == 1


def test_expiry_while_resolving_credentials_stops_before_pay(world):
    def expire():
        with world.store._connect() as c:
            c.execute("UPDATE proposals SET expires_at='2000-01-01T00:00:00+00:00'")

    world.browser.on_fill = expire
    run(world)
    assert world.browser.submissions == 0


def test_changed_matter_after_approval_stops_before_pay(world):
    def close():
        with world.store._connect() as c:
            c.execute("UPDATE matters SET status='resolved'")

    world.browser.on_fill = close
    run(world)
    assert world.browser.submissions == 0


def test_notion_governance_drift_stops_payment(world):
    world.payments.history.registry.find_by_key_or_id = lambda _: None
    with pytest.raises(FinanceError, match="approved operational"):
        prepare_card(world)


def test_specific_unpaid_owner_command_does_not_authorize_payment(world):
    world.source.bill = replace(
        world.source.bill, period_start="2026-12-01", period_end="2026-12-31"
    )
    world.browser.quote_bill = world.source.bill
    update = {
        "update_id": 1,
        "message": {
            "from": {"id": 42},
            "chat": {"id": 42, "type": "private"},
            "text": "/unpaid sandbox-instance",
        },
    }
    world.telegram.handle_update(update)
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0
    proposal, callback = prepare_card(world)
    callback["update_id"] = 2
    world.telegram.handle_update(callback)
    # The command acknowledgement is message 1; the review is message 2.
    assert world.browser.submissions == 0
    callback["update_id"] = 3
    callback["callback_query"]["message"]["message_id"] = 2
    world.telegram.handle_update(callback)
    assert attempt(world).status == ExecutionStatus.VERIFIED_SUCCESS
    assert world.store.get_proposal(proposal.id).decision_origin == "telegram"


@pytest.mark.parametrize("kind", ["forward", "other_user", "bot", "group"])
def test_untrusted_message_cannot_lift_baseline(world, kind):
    world.source.bill = replace(
        world.source.bill, period_start="2026-12-01", period_end="2026-12-31"
    )
    message = {
        "from": {"id": 42},
        "chat": {"id": 42, "type": "private"},
        "text": "/unpaid sandbox-instance",
    }
    if kind == "forward":
        message["forward_origin"] = {"type": "user"}
    elif kind == "other_user":
        message["from"]["id"] = 43
    elif kind == "bot":
        message["from"]["is_bot"] = True
    else:
        message["chat"]["type"] = "group"
    world.telegram.handle_update({"update_id": 1, "message": message})
    with pytest.raises(FinanceError, match="Through-2026"):
        prepare_card(world)


def test_1password_totp_query_is_runtime_only(monkeypatch, world):
    from types import SimpleNamespace

    from wally.adapters.secrets.op_cli import OnePasswordCliAdapter
    from wally.runtime.secret_resolver import GovernedSecretsResolver

    calls = []
    monkeypatch.setattr("wally.adapters.secrets.op_cli.shutil.which", lambda _: "/fake/op")

    def command(args, **kwargs):
        calls.append(args)
        if args[1] == "read":
            assert kwargs["capture_output"]
        return SimpleNamespace(returncode=0, stdout="483921\n")

    monkeypatch.setattr("wally.adapters.secrets.op_cli.subprocess.run", command)
    value = GovernedSecretsResolver(OnePasswordCliAdapter()).resolve_totp(TOTP_REF, authorized=True)
    assert value == CANARIES[2]
    assert calls[-1] == ["op", "read", TOTP_REF + "?attribute=otp"]
    assert value not in json.dumps(calls)


def test_restart_does_not_reconstruct_human_authority(world):
    proposal, _ = prepare_card(world)
    context = world.authority.issue(PAYMENT_WORKER)
    with pytest.raises(AuthorizationError, match="denied"):
        world.authority.confirm_human(
            context, Capability.EXECUTE_PAYMENT, presentation={"proposal_id": proposal.id}
        )
    assert world.browser.submissions == 0


def test_concurrent_callbacks_claim_one_execution(world):
    from concurrent.futures import ThreadPoolExecutor

    proposal, callback = prepare_card(world)
    row = world.telegram.outbox.by_nonce(callback["callback_query"]["data"].split(".")[0])

    def press(number):
        cb = {**callback["callback_query"], "id": f"concurrent-{number}"}
        return world.approvals.handle(row, "approve_execute", cb)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(press, (1, 2)))
    assert any("already used" in result for result in results)
    assert world.browser.submissions == world.notion.creates == 1
    assert len(world.store.list_executions(proposal_id=proposal.id)) == 1


def test_mutated_persisted_spec_cannot_dispatch(world):
    _, callback = prepare_card(world)
    with world.store._connect() as c:
        spec = json.loads(c.execute("SELECT spec FROM payment_specs").fetchone()[0])
        spec["bill"]["destination"] = "ATTACKER"
        c.execute("UPDATE payment_specs SET spec=?", (json.dumps(spec),))
    world.telegram.handle_update(callback)
    assert not world.secrets.resolve_calls
    assert world.browser.submissions == 0


def test_poll_composition_does_not_grant_payment_authority_to_adapters(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from wally.finance.payments import SupervisedPayments
    from wally.telegram.poll import build_service
    from wally.telegram.service import TelegramConfig

    template = build_demo(tmp_path / "template", browser=FastBrowser())

    def factory(settings, store, authority):
        return SupervisedPayments(
            store,
            authority,
            template.source,
            template.provider,
            template.payments.history,
            template.payments.resolver,
            enabled=lambda: True,
        )

    settings = SimpleNamespace(
        project_root=Path(__file__).resolve().parents[1],
        ops_database=tmp_path / "poll.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit",
        dry_run=False,
    )
    service = build_service(
        settings,
        TelegramConfig("test", "42", "test", "42"),
        template.transport,
        payment_factory=factory,
    )
    service.set_dispatch_guard(lambda: True)
    payments = service._payment_approvals.payments
    proposal = payments.prepare(
        template.source.bill.instance_id, context=payments.authority.issue("cli")
    )
    service.sync()
    service.deliver_pending()
    card = template.transport.sent[0]
    callback = {
        "update_id": 1,
        "callback_query": {
            "id": "factory-tap",
            "from": {"id": 42},
            "data": card["buttons"][0][0]["callback_data"],
            "message": {"message_id": 1, "date": 1, "chat": {"id": 42, "type": "private"}},
        },
    }
    service.handle_update(callback)
    assert payments.store.list_executions(proposal_id=proposal.id)[0].status == (
        ExecutionStatus.VERIFIED_SUCCESS
    )
    for channel in ("telegram", "cli"):
        assert (
            payments.authority.check(payments.authority.issue(channel), Capability.EXECUTE_PAYMENT)
            is not None
        )


def test_baseline_retains_evidence_without_reopening_old_obligations(world):
    from wally.finance.baseline import FinancialBaseline
    from wally.models.ops import Observation, ObservationCategory
    from wally.ops.reconcile import MatterReconciler

    baseline = FinancialBaseline(world.store)
    observation = Observation(
        "evidence",
        "evidence",
        "gmail",
        "message",
        "2027-01-10",
        "2027-01-10",
        ObservationCategory.INVOICE,
        "Historical unpaid reminder",
        "Evidence only",
        False,
        "external",
        0.9,
        extra={"period_start": "2026-12-01"},
    )
    world.store.save_observation(observation)
    reconciler = MatterReconciler(world.store, financial_attention=baseline.allows)
    assert reconciler.apply(observation) is None
    assert world.store.get_observation_by_fingerprint("evidence") is not None
    assert not world.store.list_matters()
    canonical = replace(
        observation,
        source="finance",
        trusted=True,
        extra={"period_start": "2027-01-01", "finance_binding": "current"},
    )
    assert baseline.allows(canonical)
    assert not baseline.allows(replace(canonical, extra={"period_start": "2026-12-01"}))


def test_baseline_exception_does_not_survive_material_drift(world):
    from wally.finance.baseline import FinancialBaseline
    from wally.models.ops import Observation, ObservationCategory

    world.source.bill = replace(
        world.source.bill, period_start="2026-12-01", period_end="2026-12-31"
    )
    context = world.authority.issue("telegram", external_request_ref="specific-owner-command")
    world.payments.identify_unpaid(world.source.bill.instance_id, context=context)
    bill = world.source.bill
    observation = Observation(
        "canonical",
        "canonical",
        "finance",
        bill.instance_id,
        "2026-10-18",
        "2026-10-18",
        ObservationCategory.KNOWLEDGE_OBLIGATION,
        "Bill",
        "Evidence",
        True,
        "knowledge_asset",
        1,
        extra={"period_start": bill.period_start, "finance_binding": bill.catalog_binding},
    )
    baseline = FinancialBaseline(world.store)
    assert baseline.allows(observation)
    assert not baseline.allows(
        replace(
            observation, extra={"period_start": bill.period_start, "finance_binding": "changed"}
        )
    )


def test_bank_challenge_after_submission_retains_pending_claim(world):
    world.browser.on_pay = lambda: setattr(world.browser, "challenge", True)
    proposal, _ = run(world)
    assert attempt(world).outcome == "challenge_pending"
    assert attempt(world).status == ExecutionStatus.EXECUTED_UNVERIFIED
    assert "Bank app" in world.approvals.result(proposal.id)
    assert world.notion.creates == 0
    world.payments.reconcile(proposal.id, context=worker(world))
    assert world.browser.submissions == 1
    assert attempt(world).status == ExecutionStatus.VERIFIED_SUCCESS


def test_prepared_provider_transaction_is_single_use_across_occurrences(world):
    run(world)
    world.source.bill = replace(
        world.source.bill,
        occurrence="2027-02",
        period_start="2027-02-01",
        period_end="2027-02-28",
        reference="SANDBOX-INV-2027-02",
    )
    world.browser.quote_bill = world.source.bill
    # Bypass only the fake Notion query's coarse inventory for this independent
    # provider-token replay attack. Runtime duplicate/transaction claims stay real.
    world.payments.history.check = lambda _: None
    proposal = world.payments.prepare(
        world.source.bill.instance_id, context=world.authority.issue("cli")
    )
    world.telegram.sync()
    world.telegram.deliver_pending()
    card = next(item for item in reversed(world.transport.sent) if item["buttons"])
    message_id = world.transport.sent.index(card) + 1
    world.telegram.handle_update(
        {
            "update_id": 2,
            "callback_query": {
                "id": "replayed-provider-token",
                "from": {"id": 42},
                "data": card["buttons"][0][0]["callback_data"],
                "message": {
                    "message_id": message_id,
                    "date": 1,
                    "chat": {"id": 42, "type": "private"},
                },
            },
        }
    )
    assert world.browser.submissions == 1
    assert not world.store.list_executions(proposal_id=proposal.id)
