"""Reasoning router tests."""

from pathlib import Path

import pytest

from wally.config.loader import load_settings
from wally.models.task import Task, TaskIntent
from wally.runtime.reasoning_router import ReasoningRouter


@pytest.fixture
def router(project_root: Path) -> ReasoningRouter:
    settings = load_settings(project_root=project_root, config_name="macbook")
    return ReasoningRouter(settings=settings)


def test_default_conversation_routes_balanced(router: ReasoningRouter) -> None:
    decision = router.route(Task(intent=TaskIntent.CONVERSATION))
    assert decision.profile == "balanced"
    assert decision.source == "default"


def test_consolidation_routes_fast(router: ReasoningRouter) -> None:
    decision = router.route(Task(intent=TaskIntent.CONSOLIDATION))
    assert decision.profile == "fast"
    assert decision.source == "task"


def test_fast_tools_followup_routes_fast(router: ReasoningRouter) -> None:
    decision = router.route(
        Task(
            intent=TaskIntent.TOOL_FOLLOWUP,
            tool_names=("conversation_search", "knowledge_retrieve"),
        )
    )
    assert decision.profile == "fast"
    assert decision.source == "task"


def test_mixed_tools_followup_routes_balanced(router: ReasoningRouter) -> None:
    decision = router.route(
        Task(
            intent=TaskIntent.TOOL_FOLLOWUP,
            tool_names=("conversation_search", "knowledge_create"),
        )
    )
    assert decision.profile == "balanced"
    assert decision.source == "default"


def test_deep_tool_routes_deep(project_root: Path) -> None:
    settings = load_settings(project_root=project_root, config_name="macbook")
    rules = settings.reasoning_routing
    from dataclasses import replace

    deep_rules = replace(
        rules,
        deep_tools=frozenset({"knowledge_create"}),
    )
    from dataclasses import replace as settings_replace

    settings_with_deep = settings_replace(settings, reasoning_routing=deep_rules)
    router = ReasoningRouter(settings=settings_with_deep)
    decision = router.route(
        Task(intent=TaskIntent.TOOL_FOLLOWUP, tool_names=("knowledge_create",))
    )
    assert decision.profile == "deep"
    assert decision.source == "task"


def test_override_wins(project_root: Path) -> None:
    settings = load_settings(
        project_root=project_root,
        config_name="macbook",
        reasoning_profile="deep",
    )
    router = ReasoningRouter(settings=settings)
    decision = router.route(Task(intent=TaskIntent.CONVERSATION))
    assert decision.profile == "deep"
    assert decision.source == "override"
