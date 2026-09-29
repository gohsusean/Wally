"""Deterministic reasoning profile selection."""

from __future__ import annotations

from dataclasses import dataclass

from wally.config.loader import Settings
from wally.exceptions import ConfigurationError
from wally.models.task import Task


@dataclass(frozen=True)
class RoutingDecision:
    profile: str
    source: str  # override | task | default


class ReasoningRouter:
    """Select a reasoning profile for a task. Routing only — not reasoning."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._rules = settings.reasoning_routing
        self._default = settings.default_reasoning_profile
        self._known = set(settings.reasoning_profiles)

    def route(self, task: Task) -> RoutingDecision:
        override = self._settings.reasoning_profile_override
        if override:
            return RoutingDecision(profile=self._validate(override), source="override")

        profile = self._route_task(task)
        return RoutingDecision(
            profile=profile, source="task" if profile != self._default else "default"
        )

    def _route_task(self, task: Task) -> str:
        if task.intent.value in self._rules.fast_tasks:
            return "fast"

        if task.tool_names:
            if any(name in self._rules.deep_tools for name in task.tool_names):
                return "deep"
            if all(name in self._rules.fast_tools for name in task.tool_names):
                return "fast"

        return self._default

    def _validate(self, profile: str) -> str:
        if profile not in self._known:
            known = ", ".join(sorted(self._known))
            raise ConfigurationError(
                f"Unknown reasoning profile '{profile}'. Available profiles: {known}"
            )
        return profile
