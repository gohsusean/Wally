"""Information authority hierarchy tests."""

import json
from pathlib import Path

from tests.mock_knowledge import MockKnowledgeProvider
from wally.adapters.cli.approval import CLIApprovalProvider
from wally.adapters.conversation.local import LocalConversationAdapter
from wally.audit.logger import AuditLogger
from wally.models.actions import ToolCall
from wally.models.messages import Message, Role
from wally.orchestrator.tools import ToolRegistry
from wally.runtime.authority import (
    AUTHORITY_PRECEDENCE,
    InformationAuthority,
    higher_authority_wins,
    wrap_conversation_results,
    wrap_web_search_result,
)
from wally.runtime.policy import evaluate_knowledge_policy
from wally.safety.gates import ApprovalGate
from wally.session.store import SessionStore


def test_authority_precedence_order() -> None:
    assert AUTHORITY_PRECEDENCE[0] == InformationAuthority.POLICY_ASSET
    assert AUTHORITY_PRECEDENCE[-1] == InformationAuthority.EXTERNAL_WEB


def test_policy_assets_outrank_conversation_recall() -> None:
    assert higher_authority_wins(
        InformationAuthority.POLICY_ASSET,
        InformationAuthority.CONVERSATION_RECALL,
    )


def test_knowledge_assets_outrank_conversation_recall() -> None:
    assert higher_authority_wins(
        InformationAuthority.KNOWLEDGE_ASSET,
        InformationAuthority.CONVERSATION_RECALL,
    )


def test_user_instruction_outrank_conversation_recall() -> None:
    assert higher_authority_wins(
        InformationAuthority.USER_INSTRUCTION,
        InformationAuthority.CONVERSATION_RECALL,
    )


def test_conversation_recall_does_not_outrank_knowledge() -> None:
    assert not higher_authority_wins(
        InformationAuthority.CONVERSATION_RECALL,
        InformationAuthority.KNOWLEDGE_ASSET,
    )


def test_wrap_conversation_results_marks_non_authoritative() -> None:
    payload = wrap_conversation_results([{"content": "old chat said $500"}])
    assert payload["authoritative"] is False
    assert payload["authority"] == InformationAuthority.CONVERSATION_RECALL.value
    assert InformationAuthority.POLICY_ASSET.value in payload["precedence"]
    assert "Policy Assets" in payload["precedence_note"]


def test_knowledge_assets_outrank_external_web() -> None:
    assert higher_authority_wins(
        InformationAuthority.KNOWLEDGE_ASSET,
        InformationAuthority.EXTERNAL_WEB,
    )


def test_wrap_web_results_marks_non_authoritative() -> None:
    payload = wrap_web_search_result(
        query="market news",
        sources=[{"title": "News", "url": "https://example.com", "snippet": "..."}],
    )
    assert payload["authoritative"] is False
    assert payload["trust"] == "low"
    assert payload["external_source_rule"] is True


def test_conversation_search_output_is_non_authoritative(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    session = store.create_session()
    store.add_message(session.id, Message(role=Role.USER, content="bill is five hundred"))
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
        ToolCall(call_id="c1", name="conversation_search", arguments={"query": "bill"})
    )
    payload = json.loads(result.output)
    assert payload["authoritative"] is False
    assert payload["results"]


def test_governance_policy_denied_even_if_conversation_implied_write(tmp_path: Path) -> None:
    """Policy Assets win: governance writes stay blocked regardless of chat context."""
    knowledge = MockKnowledgeProvider()
    store = SessionStore(tmp_path / "sessions.db")
    old = store.create_session()
    store.add_message(
        old.id,
        Message(role=Role.USER, content="Please update the governance SOP database for me"),
    )
    conversation = LocalConversationAdapter(store=store)

    policy = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "New policy", "content": "...", "database": "governance"},
    )
    assert not policy.allowed

    # Conversation recall may surface the old request — still non-authoritative
    hits = conversation.search("governance SOP", exclude_session=old.id)
    wrapped = wrap_conversation_results(
        [{"content": hit.content} for hit in hits] if hits else [{"content": "update governance"}]
    )
    assert wrapped["authoritative"] is False

    # Knowledge policy is unchanged by conversation content
    assert not evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "New policy", "content": "...", "database": "governance"},
    ).allowed


def test_operational_knowledge_policy_independent_of_conversation_recall() -> None:
    """Knowledge Assets path remains policy-gated; conversation does not bypass it."""
    knowledge = MockKnowledgeProvider()
    operational = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "Bill amount", "content": "142.50", "database": "operations"},
    )
    assert operational.allowed

    conversation_claim = wrap_conversation_results(
        [{"content": "We agreed the bill is $500 in a prior session"}]
    )
    assert conversation_claim["authoritative"] is False
    # Operational write policy still applies from knowledge config, not chat
    assert evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "Bill amount", "content": "500", "database": "operations"},
    ).allowed
