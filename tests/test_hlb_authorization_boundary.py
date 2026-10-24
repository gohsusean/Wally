"""No bank form action while the HLB submission lock is disabled."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wally.adapters.browser.hlb_transfer import HLBTransferProvider
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.finance.hlb_transfer_demo import fixture_profile
from wally.finance.models import FinanceError
from wally.finance.payments import PAYMENT_WORKER
from wally.models.principal import Capability
from wally.telegram.poll import build_service
from wally.telegram.service import TelegramConfig


@pytest.mark.parametrize("entry", ["enter_review", "submit"])
def test_disabled_route_stops_before_review_can_initiate_app_authorise(entry):
    browser = Mock()
    provider = HLBTransferProvider(
        browser, replace(fixture_profile(), submission_enabled=False), Mock()
    )
    guard = Mock()
    with pytest.raises(FinanceError, match="before any bank form action"):
        getattr(provider, entry)(Mock(), Mock(), guard=guard)
    assert browser.mock_calls == []
    guard.assert_not_called()


@pytest.mark.parametrize("entry", ["enter_review", "submit"])
def test_even_enabled_route_cannot_use_an_arbitrary_guard_as_telegram_approval(entry):
    browser = Mock(offline_simulation=True)
    provider = HLBTransferProvider(browser, fixture_profile(), Mock())
    with pytest.raises(FinanceError, match="Telegram payment authorization"):
        getattr(provider, entry)(Mock(), Mock(), guard=lambda: True)
    assert browser.mock_calls == []


@pytest.mark.parametrize("enabled", [False, True])
def test_receipt_download_permission_is_explicit_and_scoped(enabled):
    adapter = PlaywrightBrowserAdapter()
    adapter._ensure_browser = Mock()
    adapter._browser = Mock()
    page = adapter._browser.new_context.return_value.new_page.return_value
    page.url = "https://bank.example.test/history"
    adapter.restrict_origins = Mock()
    session = adapter.open_session(
        url=page.url,
        allowed_origins=("https://bank.example.test",),
        allow_read_pages=True,
        allow_receipt_downloads=enabled,
    )
    adapter._browser.new_context.assert_called_once_with(
        service_workers="block", accept_downloads=enabled
    )
    assert adapter._pages[session.session_id] is page


@pytest.mark.parametrize(
    "options", [{}, {"allow_read_pages": True}, {"allowed_origins": ("https://bank.example.test",)}]
)
def test_downloads_refused_without_both_read_scope_and_reviewed_origin(options):
    adapter = PlaywrightBrowserAdapter()
    adapter._ensure_browser = Mock()
    with pytest.raises(ValueError, match="scoped read context"):
        adapter.open_session(
            url="https://bank.example.test/history", allow_receipt_downloads=True, **options
        )
    adapter._ensure_browser.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_unmapped_hlb_payment_request_is_blocked_without_any_approval(method):
    browser = Mock(offline_simulation=True)
    provider = HLBTransferProvider(browser, fixture_profile(), Mock())
    request = SimpleNamespace(
        method=method, url="https://s.hongleongconnect.my/unmapped-payment-boundary"
    )
    assert provider._request_allowed(request) is False
    assert provider._request_allowed(request) is False  # A retry cannot create authority.
    assert browser.mock_calls == []
    assert provider._request_allowed(
        SimpleNamespace(method="GET", url=provider.profile.history_url)
    ) is True  # Independent history reads remain possible.


def test_production_default_composition_has_no_payment_worker_or_execution_grant(tmp_path):
    settings = SimpleNamespace(
        project_root=tmp_path,
        ops_database=tmp_path / "operations.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit",
        dry_run=False,
    )
    service = build_service(settings, TelegramConfig("test", "42", "test", "42"), Mock())
    assert service._payment_approvals is None
    authority = service.runtime._authority
    assert PAYMENT_WORKER not in authority.channels()
    for channel in ("telegram", "cli", "repl"):
        context = authority.issue(channel)
        assert authority.check(context, Capability.EXECUTE_PAYMENT) is not None
        assert authority.check(context, Capability.VERIFY_PAYMENT) is not None
    with service._store._connect() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='payment_attempts'"
        ).fetchone() is None
