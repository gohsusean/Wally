"""Compose structured responses."""

from __future__ import annotations

from wally.models.messages import Session, WallyResponse
from wally.providers.llm import LLMResponse


def compose_response(
    session: Session,
    llm_response: LLMResponse,
    *,
    actions_taken: list[str] | None = None,
) -> WallyResponse:
    """Build a structured Wally response from LLM output."""
    return WallyResponse(
        content=llm_response.content,
        session_id=session.id,
        facts=[],
        inferences=[],
        actions_taken=actions_taken or [],
    )
