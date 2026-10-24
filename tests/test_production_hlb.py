"""Production entry-point wiring; bank effects are mocked or locally intercepted."""

import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from wally.adapters.browser.hlb_transfer import HLBTransferProvider
from wally.finance.hlb_transfer_demo import build_hlb_demo, fixture_profile
from wally.finance.models import FinanceError
from wally.finance.payment_demo import DemoTelegram, prepare_card
from wally.finance.payments import PAYMENT_WORKER
from wally.finance.production_payments import (
    build_production_payments,
    load_hlb_route,
    runtime_status,
)
from wally.models.ops import ExecutionStatus, ProposalStatus
from wally.models.principal import Capability
from wally.telegram import poll
from wally.telegram.service import TelegramConfig


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/hlb-payments.yaml").write_text(
        "schema_version: 1\nprofile: null\nrecipients: {}\n"
    )
    return SimpleNamespace(
        project_root=tmp_path, ops_database=tmp_path / "operations.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit", dry_run=False,
        notion_api_key=None, secrets_enabled=False, communications_enabled=False,
        browser_headless=True, browser_channel="chrome",
        telegram_bot_token_ref="", telegram_owner_user_id="42",
    )


def build(settings, transport=None):
    return poll.build_service(
        settings, TelegramConfig("test", "42", "test", "42"),
        transport or DemoTelegram(), payment_factory=build_production_payments,
    )


def test_production_builder_registers_hlb_but_no_surface_can_execute(settings, monkeypatch):
    request = Mock(side_effect=AssertionError("Startup must not contact providers"))
    monkeypatch.setattr("httpx.Client.request", request)
    service = build(settings)
    payments = service._payment_approvals.payments
    assert type(payments.provider) is HLBTransferProvider
    assert payments.provider.profile is None
    assert not payments.enabled()
    assert not payments.live_validated
    authority = service.runtime._authority
    assert PAYMENT_WORKER in authority.channels()
    for channel in ("telegram", "cli", "repl"):
        for capability in (Capability.EXECUTE_PAYMENT, Capability.VERIFY_PAYMENT):
            assert authority.check(authority.issue(channel), capability) is not None
    with pytest.raises(FinanceError, match="no reviewed live payment route"):
        payments.prepare("unused", context=authority.issue("cli"))
    for operation in (payments.provider.enter_review, payments.provider.submit):
        with pytest.raises(FinanceError, match="before any bank form action"):
            operation(Mock(), Mock(), guard=lambda: True)
    assert payments.provider._request_allowed(Mock()) is False
    request.assert_not_called()
    status = runtime_status(payments)
    assert status["profile_configured"] is False
    assert status["live_submission_enabled"] is False
    assert len(status["review_code_sha256"]) == 64


def test_actual_serve_supplies_production_factory_and_reports_imported_policy(
    settings, monkeypatch, capsys,
):
    monkeypatch.setattr(poll, "resolve_bot_token", lambda *a, **kw: "test")
    monkeypatch.setattr(poll, "BotClient", lambda *a: DemoTelegram())
    services = []
    monkeypatch.setattr(poll, "poll_forever", lambda service, client: services.append(service))
    assert poll.serve(settings, secrets_provider=None, audit=None) == 0
    payments = services[0]._payment_approvals.payments
    assert type(payments.provider) is HLBTransferProvider
    line = capsys.readouterr().out.strip().removeprefix("Telegram payment runtime: ")
    assert json.loads(line) == runtime_status(payments)


@pytest.mark.parametrize("change", ["live", "synthetic", "extra", "incomplete"])
def test_configuration_cannot_enable_live_submission_or_guess_contracts(settings, change):
    profile = asdict(replace(fixture_profile(), sandbox=False, submission_enabled=False))
    data = {"schema_version": 1, "profile": profile, "recipients": {}}
    if change == "live":
        profile["submission_enabled"] = True
    elif change == "synthetic":
        profile["sandbox"] = True
    elif change == "extra":
        data["enabled"] = True
    else:
        profile.pop("payment_fields")
    path = settings.project_root / "config/hlb-payments.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(FinanceError):
        load_hlb_route(path)


def test_preparation_command_reports_missing_route_without_opening_bank(settings):
    transport = DemoTelegram()
    service = build(settings, transport)
    provider = service._payment_approvals.payments.provider
    provider._login = Mock(side_effect=AssertionError("No bank access"))
    update = {
        "update_id": 1,
        "message": {
            "from": {"id": 42}, "chat": {"id": 42, "type": "private"},
            "text": "/pay exact-issued-bill",
        },
    }
    assert service.handle_update(update) == "payment_preparation"
    assert "no reviewed live payment route" in str(transport.sent[0]["text"])
    provider._login.assert_not_called()
    assert service._store.list_proposals() == []
    update["update_id"] += 1
    update["message"]["forward_date"] = 1
    assert service.handle_update(update) == "ignored"


def test_production_composition_with_offline_bank_obeys_telegram_before_entry(settings):
    # Build the actual production service, replacing external effects only.
    world = build_hlb_demo(settings.project_root)
    try:
        service = build(settings, world.transport)
        payments = service._payment_approvals.payments
        payments.source, payments.provider = world.source, world.provider
        payments.history, payments.resolver = world.payments.history, world.payments.resolver
        payments.confirmations = None
        payments.enabled = lambda: True  # Synthetic intercepted bank only.
        world.payments, world.telegram = payments, service
        world.authority = payments.authority
        service.set_dispatch_guard(lambda: True)
        proposal, callback = prepare_card(world)
        assert world.browser.entry_reads == world.browser.submissions == 0
        assert "SIMULATED PAYMENT" in str(world.transport.sent[0]["text"])

        def entering():
            current = world.store.get_proposal(proposal.id)
            assert current.status == ProposalStatus.APPROVED
            with world.store._connect() as c:
                state = c.execute("SELECT state FROM telegram_payment_reviews").fetchone()[0]
                assert state == "claimed"
                assert c.execute("SELECT COUNT(*) FROM payment_attempts").fetchone()[0] == 1

        world.browser.on_entry = entering
        service.handle_update(callback)
        execution = world.store.list_executions(proposal_id=proposal.id)[0]
        assert execution.status == ExecutionStatus.VERIFIED_SUCCESS
        assert world.browser.entry_reads == world.browser.submissions == 1
        assert world.browser.authorizations == world.notion.creates == world.notion.reads == 1
        assert world.browser.submit_clicks == 0
        service.handle_update(callback)
        callback["update_id"] += 1
        service.handle_update(callback)
        assert world.browser.submissions == 1
    finally:
        world.provider.close()
        world.browser.shutdown()
        world.notion.client.close()


def test_disabled_execution_still_allows_read_only_preparation_and_card(settings):
    world = build_hlb_demo(settings.project_root, submission_enabled=False)
    try:
        world.payments.preparation_enabled = lambda: True
        world.payments.enabled = lambda: False
        proposal, callback = prepare_card(world)
        world.telegram.handle_update(callback)
        assert world.store.list_executions(proposal_id=proposal.id) == []
        assert world.browser.entry_reads == world.browser.submissions == world.notion.creates == 0
    finally:
        world.provider.close()
        world.browser.shutdown()
        world.notion.client.close()
