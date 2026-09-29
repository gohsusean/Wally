"""Conversation context assembly and consolidation."""

from __future__ import annotations

from wally.config.loader import Settings
from wally.models.messages import Message, Role, Session
from wally.models.task import Task, TaskIntent
from wally.providers.llm import LLMProvider, LLMRequest
from wally.runtime.reasoning_router import ReasoningRouter
from wally.session.store import SessionStore

_CONSOLIDATE_INSTRUCTIONS = """\
Summarize the conversation transcript below for future context.

Preserve:
- Facts, names, numbers, and decisions
- Open questions and follow-ups
- User preferences stated explicitly

Omit greetings and filler. Use concise prose or bullet points."""


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate (chars / 4)."""
    return sum(max(1, len(message.content) // 4) for message in messages)


def prepare_llm_messages(session: Session, settings: Settings) -> list[Message]:
    """Return messages for the LLM, applying summary + recent tail windowing."""
    messages = list(session.messages)
    keep_recent = settings.conversation_keep_recent
    max_messages = settings.conversation_max_context_messages

    if session.summary and session.summary_covers_through > 0:
        covered = min(session.summary_covers_through, len(messages))
        remainder = messages[covered:]
        tail = remainder[-keep_recent:] if len(remainder) > keep_recent else remainder
        prefix = Message(
            role=Role.SYSTEM,
            content=f"Earlier in this session (summarised):\n{session.summary}",
        )
        return [prefix, *tail]

    if len(messages) <= max_messages:
        return messages

    return messages[-max_messages:]


def needs_consolidation(session: Session, settings: Settings) -> bool:
    if not settings.conversation_consolidate_after:
        return False
    if len(session.messages) <= settings.conversation_consolidate_after:
        return False
    summarize_through = len(session.messages) - settings.conversation_keep_recent
    return summarize_through > session.summary_covers_through


def consolidate_session(
    session: Session,
    *,
    store: SessionStore,
    llm: LLMProvider,
    router: ReasoningRouter,
    settings: Settings,
) -> None:
    """Summarise older messages and persist summary; update session in memory."""
    keep_recent = settings.conversation_keep_recent
    if len(session.messages) <= keep_recent:
        return

    older = session.messages[:-keep_recent]
    summarize_through = len(older)
    if summarize_through <= session.summary_covers_through:
        return

    transcript = "\n".join(f"{message.role.value}: {message.content}" for message in older)
    if session.summary:
        transcript = (
            f"Prior summary:\n{session.summary}\n\nAdditional messages:\n{transcript}"
        )

    decision = router.route(Task(intent=TaskIntent.CONSOLIDATION))
    response = llm.complete(
        LLMRequest(
            instructions=_CONSOLIDATE_INSTRUCTIONS,
            messages=[Message(role=Role.USER, content=transcript)],
            tools=None,
            reasoning_profile=decision.profile,
        )
    )
    summary = response.content.strip()
    if not summary:
        return

    store.save_summary(session.id, summary, covers_message_count=summarize_through)
    session.summary = summary
    session.summary_covers_through = summarize_through
