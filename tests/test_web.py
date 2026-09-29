"""Web provider tests."""

import json
from unittest.mock import MagicMock, patch

import httpx

from wally.adapters.web.adapter import OpenAIWebAdapter, fetch_url
from wally.runtime.authority import InformationAuthority, wrap_web_search_result


def test_wrap_web_search_result_marks_low_trust() -> None:
    payload = wrap_web_search_result(
        query="Accenture news",
        summary="Recent headlines mention earnings.",
        sources=[{"title": "Example", "url": "https://example.com", "snippet": "..."}],
    )
    assert payload["authoritative"] is False
    assert payload["trust"] == "low"
    assert payload["authority"] == InformationAuthority.EXTERNAL_WEB.value
    assert payload["external_source_rule"] is True
    assert InformationAuthority.POLICY_ASSET.value in payload["precedence"]


def test_openai_web_search_parses_citations() -> None:
    adapter = OpenAIWebAdapter(
        api_key="sk-test",
        search_model="gpt-5-mini",
        search_context_size="low",
    )
    annotation = MagicMock()
    annotation.type = "url_citation"
    annotation.url = "https://example.com/article"
    annotation.title = "Example Article"
    annotation.start_index = 0
    annotation.end_index = 12

    content = MagicMock()
    content.text = "Example text from the web."
    content.annotations = [annotation]

    message = MagicMock()
    message.type = "message"
    message.content = [content]

    mock_response = MagicMock()
    mock_response.output = [message]

    with patch.object(adapter._client.responses, "create", return_value=mock_response):
        result = adapter.search("Accenture news")

    assert result.query == "Accenture news"
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.com/article"
    assert result.sources[0].title == "Example Article"


def test_openai_web_search_tool_output_is_wrapped() -> None:
    adapter = OpenAIWebAdapter(api_key="sk-test", search_model="gpt-5-mini")
    mock_response = MagicMock()
    mock_response.output = []

    with patch.object(adapter._client.responses, "create", return_value=mock_response):
        output = adapter.execute_tool("web_search", {"query": "latest AI news"})

    payload = json.loads(output)
    assert payload["authoritative"] is False
    assert payload["authority"] == InformationAuthority.EXTERNAL_WEB.value
    assert payload["query"] == "latest AI news"


def test_fetch_url_extracts_html_text() -> None:
    html = (
        "<html><head><title>Example</title></head>"
        "<body><p>Hello web</p><script>evil()</script></body></html>"
    )
    response = httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with patch("wally.adapters.web.adapter.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value = response
        result = fetch_url(url="https://example.com", max_bytes=1024)

    assert result.title == "Example"
    assert "Hello web" in result.content
    assert "evil" not in result.content


def test_openai_web_fetch_tool_output_is_wrapped() -> None:
    adapter = OpenAIWebAdapter(api_key="sk-test", search_model="gpt-5-mini")
    html = "<html><head><title>Docs</title></head><body><p>API reference</p></body></html>"
    response = httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with patch("wally.adapters.web.adapter.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value = response
        output = adapter.execute_tool("web_fetch", {"url": "https://example.com/docs"})

    payload = json.loads(output)
    assert payload["authoritative"] is False
    assert payload["url"] == "https://example.com/docs"
    assert "API reference" in payload["content"]
