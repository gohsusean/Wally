"""In-memory secrets adapter for tests."""

from __future__ import annotations

from wally.exceptions import ProviderUnavailableError
from wally.runtime.secrets_safety import evaluate_secret_reference


class MemorySecretsProvider:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets = dict(secrets or {})
        self.resolve_calls: list[str] = []

    @property
    def name(self) -> str:
        return "secrets"

    def is_healthy(self) -> bool:
        return True

    def resolve(self, reference: str) -> str:
        policy = evaluate_secret_reference(reference)
        if not policy.allowed:
            raise ProviderUnavailableError(self.name, policy.reason or "Invalid secret reference.")
        self.resolve_calls.append(reference)
        if reference not in self._secrets:
            raise ProviderUnavailableError(self.name, "Unknown secret reference.")
        return self._secrets[reference]
