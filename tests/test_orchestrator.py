"""Orchestrator tests."""

from pathlib import Path

import pytest

from tests.mock_knowledge import MockKnowledgeProvider
from tests.mocks import MockLLM
from wally.adapters.cli.approval import CLIApprovalProvider
from wally.audit.logger import AuditLogger
from wally.config.loader import load_settings
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ToolCall
from wally.models.messages import Role
from wally.orchestrator.core import Orchestrator
from wally.orchestrator.tools import ToolRegistry
from wally.safety.gates import ApprovalGate
from wally.session.store import SessionStore


@pytest.fixture
def orchestrator(project_root: Path, tmp_path: Path) -> Orchestrator:
    from dataclasses import replace

    settings = load_settings(project_root=project_root, config_name="macbook")
    settings = replace(
        settings,
        knowledge_enabled=False,
        workflow_enabled=False,
        communications_enabled=False,
        conversation_enabled=False,
        web_enabled=False,
        finance_enabled=False,
        session_database=tmp_path / "sessions.db",
        audit_directory=tmp_path / "audit",
    )
    knowledge = MockKnowledgeProvider()
    gate = ApprovalGate(require_approval=settings.require_approval, dry_run=False)
    audit = AuditLogger(settings.audit_directory)
    tools = ToolRegistry(
        providers={"knowledge": knowledge},
        knowledge=knowledge,
        gate=gate,
        approval=CLIApprovalProvider(),
        audit=audit,
        dry_run=False,
    )
    return Orchestrator(
        settings=settings,
        llm=MockLLM(content="Hello from Wally."),
        sessions=SessionStore(settings.session_database),
        audit=audit,
        tools=tools,
        knowledge=knowledge,
    )


def test_handle_conversation(orchestrator: Orchestrator) -> None:
    session = orchestrator._sessions.create_session()
    response = orchestrator.handle(session, "Hi Wally")
    assert response.content == "Hello from Wally."
    assert response.session_id == session.id
    mock = orchestrator._llm
    assert isinstance(mock, MockLLM)
    assert mock.calls[0].reasoning_profile == "balanced"

    loaded = orchestrator._sessions.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0].role == Role.USER
    assert loaded.messages[1].role == Role.ASSISTANT


def test_handle_unavailable_llm(orchestrator: Orchestrator) -> None:
    orchestrator._llm = MockLLM(healthy=False)
    session = orchestrator._sessions.create_session()
    with pytest.raises(ProviderUnavailableError):
        orchestrator.handle(session, "Hi")


def test_tool_loop(orchestrator: Orchestrator) -> None:
    orchestrator._llm = MockLLM(
        tool_calls=[
            ToolCall(call_id="call-1", name="knowledge_retrieve", arguments={"query": "Bali"})
        ]
    )
    orchestrator._knowledge.seed("Bali trip", "Beach house wifi is 1234")
    session = orchestrator._sessions.create_session()
    response = orchestrator.handle(session, "What do I know about Bali?")
    assert "Done after tools" in response.content
    assert any("knowledge_retrieve" in action for action in response.actions_taken)
    mock = orchestrator._llm
    assert isinstance(mock, MockLLM)
    assert mock.calls[1].reasoning_profile == "fast"
