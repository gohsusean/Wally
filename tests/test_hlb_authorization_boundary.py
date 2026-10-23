"""No bank form action while the HLB submission lock is disabled."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from wally.adapters.browser.hlb_transfer import HLBTransferProvider
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.finance.hlb_transfer_demo import fixture_profile
from wally.finance.models import FinanceError


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


@pytest.mark.parametrize("failure", ["lost_review_response", "review_mismatch"])
def test_uncertain_review_cannot_start_another_authorization_request(failure):
    browser = Mock(offline_simulation=True)
    provider = HLBTransferProvider(browser, fixture_profile(), Mock())
    bill, quote = Mock(), Mock(transaction="exact-reviewed-transaction")
    provider.inspect = Mock(return_value=quote)
    page = Mock(url="https://s.hongleongconnect.my/transfer")
    form = Mock()
    provider._page = Mock(return_value=page)
    provider._form = Mock(return_value=form)
    review = form.locator.return_value.click
    provider._check_review = Mock()
    if failure == "lost_review_response":
        review.side_effect = TimeoutError("Review response lost")
    else:
        provider._check_review.side_effect = FinanceError("Review mismatch")
    with pytest.raises((TimeoutError, FinanceError)):
        provider.enter_review(bill, quote, guard=Mock())
    with pytest.raises(FinanceError, match="already submitted"):
        provider.submit(bill, quote, guard=Mock())
    review.assert_called_once()


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
