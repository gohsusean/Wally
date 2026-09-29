"""OpenAI Responses API adapter."""

from __future__ import annotations

import json
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI

from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ToolCall
from wally.providers.llm import LLMProvider, LLMRequest, LLMResponse


class OpenAIAdapter:
    """Thin adapter over the OpenAI Responses API."""

    def __init__(
        self,
        *,
        api_key: str,
        profiles: dict[str, str],
        default_profile: str,
    ) -> None:
        self._client = OpenAI(api_key=api_key)
        self._profiles = profiles
        self._default_profile = default_profile

    @property
    def name(self) -> str:
        return "openai"

    def is_healthy(self) -> bool:
        return bool(self._client.api_key)

    def _model_for(self, profile: str | None) -> tuple[str, str]:
        name = profile or self._default_profile
        model = self._profiles.get(name)
        if not model:
            known = ", ".join(sorted(self._profiles))
            raise ProviderUnavailableError(
                self.name,
                f"Unknown reasoning profile '{name}'. Available profiles: {known}",
            )
        return name, model

    def complete(self, request: LLMRequest) -> LLMResponse:
        profile_name, model = self._model_for(request.reasoning_profile)
        # Responses must be stored server-side to chain tool calls via previous_response_id.
        use_store = bool(request.tools) or bool(request.previous_response_id)
        payload: dict[str, Any] = {
            "model": model,
            "instructions": request.instructions,
            "store": use_store,
        }
        if request.tools:
            payload["tools"] = request.tools

        if request.previous_response_id and request.tool_results:
            payload["previous_response_id"] = request.previous_response_id
            payload["input"] = [
                {
                    "type": "function_call_output",
                    "call_id": result.call_id,
                    "output": result.output,
                }
                for result in request.tool_results
            ]
        else:
            payload["input"] = [message.to_llm_dict() for message in request.messages]

        try:
            response = self._client.responses.create(**payload)
        except APIConnectionError as exc:
            raise ProviderUnavailableError(
                self.name, "Could not connect to OpenAI. Check your network."
            ) from exc
        except APIStatusError as exc:
            if exc.status_code in {401, 403}:
                raise ProviderUnavailableError(
                    self.name, "Authentication failed. Check OPENAI_API_KEY."
                ) from exc
            raise ProviderUnavailableError(self.name, str(exc)) from exc

        tool_calls = _extract_tool_calls(response)
        content = response.output_text or ""
        if not content and not tool_calls:
            content = _extract_message_text(response)

        return LLMResponse(
            content=content.strip(),
            model=model,
            response_id=response.id,
            tool_calls=tool_calls,
            reasoning_profile=profile_name,
        )


def _extract_tool_calls(response: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in response.output or []:
        item_type = getattr(item, "type", None)
        if item_type != "function_call":
            continue
        arguments_raw = getattr(item, "arguments", "{}")
        try:
            if isinstance(arguments_raw, str):
                arguments = json.loads(arguments_raw)
            else:
                arguments = arguments_raw
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                call_id=getattr(item, "call_id", ""),
                name=getattr(item, "name", ""),
                arguments=arguments,
            )
        )
    return calls


def _extract_message_text(response: Any) -> str:
    parts: list[str] = []
    for item in response.output or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts)


def create_llm_provider(settings) -> LLMProvider:
    """Factory for the configured LLM adapter."""
    if settings.llm_adapter != "openai":
        raise ProviderUnavailableError(
            "llm", f"Unsupported adapter: {settings.llm_adapter}"
        )
    if not settings.openai_api_key:
        raise ProviderUnavailableError(
            "openai", "OPENAI_API_KEY is not set."
        )
    return OpenAIAdapter(
        api_key=settings.openai_api_key,
        profiles={
            name: profile.model for name, profile in settings.reasoning_profiles.items()
        },
        default_profile=settings.default_reasoning_profile,
    )
