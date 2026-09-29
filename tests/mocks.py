"""Deterministic LLM for orchestrator tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from wally.models.actions import ToolCall
from wally.providers.llm import LLMRequest, LLMResponse


@dataclass
class MockLLM:
    """Deterministic LLM for orchestrator tests."""

    content: str = "Mock response."
    healthy: bool = True
    model: str = "mock"
    tool_calls: list[ToolCall] = field(default_factory=list)
    calls: list[LLMRequest] = field(default_factory=list)
    _turn: int = 0

    @property
    def name(self) -> str:
        return "mock"

    def is_healthy(self) -> bool:
        return self.healthy

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if not self.healthy:
            from wally.exceptions import ProviderUnavailableError

            raise ProviderUnavailableError(self.name, "mock unavailable")

        if request.tool_results:
            self._turn += 1
            return LLMResponse(
                content="Done after tools.",
                model=self.model,
                response_id=f"mock-followup-{self._turn}",
                reasoning_profile=request.reasoning_profile,
            )

        return LLMResponse(
            content=self.content,
            model=self.model,
            response_id="mock-id",
            tool_calls=list(self.tool_calls),
            reasoning_profile=request.reasoning_profile,
        )
