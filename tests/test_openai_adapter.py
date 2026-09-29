"""OpenAI adapter tests."""

from unittest.mock import MagicMock, patch

from wally.adapters.openai.adapter import OpenAIAdapter
from wally.models.messages import Message, Role
from wally.providers.llm import LLMRequest, ToolResult

_TEST_PROFILES = {"balanced": "gpt-4.1", "fast": "gpt-4.1-mini"}


def test_complete_stores_response_when_tools_enabled() -> None:
    adapter = OpenAIAdapter(
        api_key="sk-test",
        profiles=_TEST_PROFILES,
        default_profile="balanced",
    )
    mock_response = MagicMock()
    mock_response.id = "resp_123"
    mock_response.output_text = "ok"
    mock_response.output = []

    with patch.object(adapter._client.responses, "create", return_value=mock_response) as create:
        adapter.complete(
            LLMRequest(
                instructions="test",
                messages=[Message(role=Role.USER, content="hi")],
                tools=[{"type": "function", "name": "workflow_list"}],
            )
        )
        assert create.call_args.kwargs["store"] is True


def test_complete_stores_response_for_tool_follow_up() -> None:
    adapter = OpenAIAdapter(
        api_key="sk-test",
        profiles=_TEST_PROFILES,
        default_profile="balanced",
    )
    mock_response = MagicMock()
    mock_response.id = "resp_456"
    mock_response.output_text = "done"
    mock_response.output = []

    with patch.object(adapter._client.responses, "create", return_value=mock_response) as create:
        adapter.complete(
            LLMRequest(
                instructions="test",
                messages=[Message(role=Role.USER, content="hi")],
                previous_response_id="resp_123",
                tool_results=[ToolResult(call_id="call_1", output="{}")],
            )
        )
        assert create.call_args.kwargs["store"] is True
        assert create.call_args.kwargs["previous_response_id"] == "resp_123"


def test_complete_does_not_store_plain_chat() -> None:
    adapter = OpenAIAdapter(
        api_key="sk-test",
        profiles=_TEST_PROFILES,
        default_profile="balanced",
    )
    mock_response = MagicMock()
    mock_response.id = "resp_789"
    mock_response.output_text = "hello"
    mock_response.output = []

    with patch.object(adapter._client.responses, "create", return_value=mock_response) as create:
        adapter.complete(
            LLMRequest(
                instructions="test",
                messages=[Message(role=Role.USER, content="hi")],
            )
        )
        assert create.call_args.kwargs["store"] is False


def test_complete_uses_request_profile_model() -> None:
    adapter = OpenAIAdapter(
        api_key="sk-test",
        profiles=_TEST_PROFILES,
        default_profile="balanced",
    )
    mock_response = MagicMock()
    mock_response.id = "resp_fast"
    mock_response.output_text = "hello"
    mock_response.output = []

    with patch.object(adapter._client.responses, "create", return_value=mock_response) as create:
        response = adapter.complete(
            LLMRequest(
                instructions="test",
                messages=[Message(role=Role.USER, content="hi")],
                reasoning_profile="fast",
            )
        )
        assert create.call_args.kwargs["model"] == "gpt-4.1-mini"
        assert response.reasoning_profile == "fast"
