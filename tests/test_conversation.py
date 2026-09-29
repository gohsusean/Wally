"""Conversation intelligence tests."""

import json
from dataclasses import replace
from pathlib import Path

from wally.adapters.cli.approval import CLIApprovalProvider
from wally.adapters.conversation.local import LocalConversationAdapter
from wally.audit.logger import AuditLogger
from wally.config.loader import load_settings
from wally.conversation.context import needs_consolidation, prepare_llm_messages
from wally.models.actions import ToolCall
from wally.models.messages import Message, Role, Session
from wally.orchestrator.tools import ToolRegistry
from wally.safety.gates import ApprovalGate
from wally.session.store import SessionStore


def test_search_messages_finds_content(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create_session()
    store.add_message(session.id, Message(role=Role.USER, content="Planning a Bali trip"))
    store.add_message(
        session.id, Message(role=Role.ASSISTANT, content="Bali beach house wifi is 1234")
    )

    hits = store.search_messages("Bali")
    assert len(hits) >= 1
    assert any("Bali" in hit.content for hit in hits)


def test_search_excludes_current_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create_session()
    store.add_message(session.id, Message(role=Role.USER, content="Bali trip notes"))

    adapter = LocalConversationAdapter(store=store)
    hits = adapter.search("Bali", exclude_session=session.id)
    assert hits == []


def test_conversation_search_tool(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create_session()
    store.add_message(session.id, Message(role=Role.USER, content="electricity bill due Friday"))
    adapter = LocalConversationAdapter(store=store)

    registry = ToolRegistry(
        providers={"conversation": adapter},
        knowledge=None,
        gate=ApprovalGate(require_approval=(), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path / "audit"),
        dry_run=False,
    )
    result = registry.execute(
        ToolCall(call_id="c1", name="conversation_search", arguments={"query": "electricity"})
    )
    payload = json.loads(result.output)
    assert payload["authoritative"] is False
    assert payload["results"]


def test_prepare_llm_messages_uses_summary() -> None:
    session = Session(
        summary="Discussed Bali accommodation.",
        summary_covers_through=2,
        messages=[
            Message(role=Role.USER, content="old 1"),
            Message(role=Role.USER, content="old 2"),
            Message(role=Role.USER, content="recent question"),
        ],
    )
    settings = load_settings(project_root=Path(__file__).resolve().parents[1])
    settings = replace(settings, conversation_keep_recent=5, conversation_max_context_messages=50)
    prepared = prepare_llm_messages(session, settings)
    assert prepared[0].role == Role.SYSTEM
    assert "Bali" in prepared[0].content
    assert prepared[-1].content == "recent question"


def test_prepare_llm_messages_truncates_long_sessions() -> None:
    session = Session(messages=[Message(role=Role.USER, content=f"msg {i}") for i in range(60)])
    settings = load_settings(project_root=Path(__file__).resolve().parents[1])
    settings = replace(settings, conversation_max_context_messages=10, conversation_keep_recent=10)
    prepared = prepare_llm_messages(session, settings)
    assert len(prepared) == 10
    assert prepared[0].content == "msg 50"


def test_needs_consolidation_when_over_threshold() -> None:
    settings = load_settings(project_root=Path(__file__).resolve().parents[1])
    settings = replace(settings, conversation_consolidate_after=5, conversation_keep_recent=2)
    session = Session(messages=[Message(role=Role.USER, content="x") for _ in range(6)])
    assert needs_consolidation(session, settings)


def test_list_recent_messages_from_prior_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    old = store.create_session()
    store.add_message(old.id, Message(role=Role.USER, content="What does fairdinkum mean?"))
    store.add_message(
        old.id,
        Message(
            role=Role.ASSISTANT,
            content="Fair dinkum means genuine or true in Australian slang.",
        ),
    )
    new = store.create_session()

    hits = store.list_recent_messages(exclude_session_id=new.id, limit=10)
    assert len(hits) == 2
    assert "fairdinkum" in hits[0].content.lower() or "fair dinkum" in hits[1].content.lower()


def test_conversation_recent_tool(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    old = store.create_session()
    store.add_message(old.id, Message(role=Role.USER, content="What does fairdinkum mean?"))
    new = store.create_session()
    adapter = LocalConversationAdapter(store=store)
    adapter.set_exclude_session(new.id)

    registry = ToolRegistry(
        providers={"conversation": adapter},
        knowledge=None,
        gate=ApprovalGate(require_approval=(), dry_run=False),
        approval=CLIApprovalProvider(),
        audit=AuditLogger(tmp_path / "audit"),
        dry_run=False,
    )
    result = registry.execute(
        ToolCall(call_id="c1", name="conversation_recent", arguments={})
    )
    payload = json.loads(result.output)
    assert payload["authoritative"] is False
    assert len(payload["results"]) >= 1
    assert "fairdinkum" in payload["results"][0]["content"].lower()


def test_fts_query_uses_or() -> None:
    from wally.session.store import _fts_query

    assert _fts_query("fair dinkum") == '"fair" OR "dinkum"'

