"""Browser automation provider factory."""

from __future__ import annotations

from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.exceptions import ProviderUnavailableError


def create_browser_provider(settings):
    if not settings.browser_enabled:
        return None
    adapter = settings.browser_adapter
    if adapter == "playwright":
        provider = PlaywrightBrowserAdapter(headless=settings.browser_headless)
        if not provider.is_healthy():
            raise ProviderUnavailableError(
                "browser",
                "Playwright package not installed. "
                "Run: uv sync --extra browser && playwright install",
            )
        return provider
    if adapter == "recording":
        from wally.adapters.browser.recording import RecordingBrowserAdapter

        return RecordingBrowserAdapter()
    raise ProviderUnavailableError("browser", f"Unsupported browser adapter: {adapter}")
