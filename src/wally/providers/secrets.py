"""Secrets provider protocol — execution-time credentials."""

from __future__ import annotations

from typing import Protocol


class SecretsProvider(Protocol):
    """Resolve secrets at execution time. Never store secrets in knowledge or config."""

    @property
    def name(self) -> str:
        ...

    def is_healthy(self) -> bool:
        ...

    def resolve(self, reference: str) -> str:
        """Return a secret value for the given reference (e.g. 1Password item ref)."""
        ...
