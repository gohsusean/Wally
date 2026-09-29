"""Web provider protocol — external public information."""

from __future__ import annotations

from typing import Protocol

from wally.models.web import WebFetchResult, WebSearchResult
from wally.providers.capability import CapabilityProvider


class WebProvider(CapabilityProvider, Protocol):
    """Retrieve external information from the public web."""

    def search(self, query: str, *, max_results: int = 5) -> WebSearchResult:
        """Search the web and return sources with optional summary text."""
        ...

    def fetch(self, url: str) -> WebFetchResult:
        """Fetch and extract text from a public URL."""
        ...
