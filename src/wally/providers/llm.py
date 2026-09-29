"""LLM provider protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from wally.models.actions import ToolCall
from wally.models.messages import Message


@dataclass
class ToolResult:
    call_id: str
    output: str


@dataclass
class LLMRequest:
    instructions: str
    messages: list[Message]
    tools: list[dict[str, object]] | None = None
    previous_response_id: str | None = None
    tool_results: list[ToolResult] | None = None
    reasoning_profile: str | None = None


@dataclass
class LLMResponse:
    content: str
    model: str
    response_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_profile: str | None = None


class LLMProvider(Protocol):
    """Abstract language model interface."""

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Generate a completion for the given request."""
        ...

    def is_healthy(self) -> bool:
        """Return True if the provider is configured and reachable."""
        ...

    @property
    def name(self) -> str:
        """Provider identifier for logging and errors."""
        ...
