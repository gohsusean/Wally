"""Browser automation stub tests."""

import pytest

from wally.adapters.browser.stub import UnconfiguredBrowserAutomationProvider
from wally.exceptions import ProviderUnavailableError


def test_unconfigured_browser_provider_raises() -> None:
    provider = UnconfiguredBrowserAutomationProvider()
    assert not provider.is_healthy()
    with pytest.raises(ProviderUnavailableError):
        provider.open_session(url="https://example.com/pay")
