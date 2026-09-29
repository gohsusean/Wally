"""Runtime policy tests."""

from tests.mock_knowledge import MockKnowledgeProvider
from wally.runtime.policy import evaluate_knowledge_policy


def test_pending_write_denied() -> None:
    knowledge = MockKnowledgeProvider()
    decision = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "x", "content": "y", "database": "pending"},
    )
    assert not decision.allowed
    assert "pending" in (decision.reason or "").lower()


def test_governance_create_denied() -> None:
    knowledge = MockKnowledgeProvider()
    decision = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "x", "content": "y", "database": "governance"},
    )
    assert not decision.allowed


def test_operational_create_allowed() -> None:
    knowledge = MockKnowledgeProvider()
    decision = evaluate_knowledge_policy(
        knowledge,
        "knowledge_create",
        {"title": "x", "content": "y", "database": "operations"},
    )
    assert decision.allowed


def test_retrieve_skips_policy() -> None:
    knowledge = MockKnowledgeProvider()
    decision = evaluate_knowledge_policy(
        knowledge, "knowledge_retrieve", {"query": "test"}
    )
    assert decision.allowed
