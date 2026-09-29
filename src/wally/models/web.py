"""Web search and fetch result types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebSource:
    """A cited external web source."""

    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class WebSearchResult:
    """Structured output from a web search."""

    query: str
    sources: tuple[WebSource, ...]
    summary: str = ""


@dataclass(frozen=True)
class WebFetchResult:
    """Structured output from fetching a URL."""

    url: str
    title: str
    content: str
    content_type: str = ""
