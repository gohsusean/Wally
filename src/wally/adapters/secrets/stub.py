"""Unconfigured secrets provider stub."""

from __future__ import annotations

from wally.exceptions import ProviderUnavailableError


class UnconfiguredSecretsProvider:
    """Placeholder when secrets are enabled but no adapter is usable."""

    @property
    def name(self) -> str:
        return "secrets"

    def is_healthy(self) -> bool:
        return False

    def resolve(self, reference: str) -> str:
        raise ProviderUnavailableError(
            self.name,
            "SecretsProvider is not configured. "
            "Install and sign in to 1Password CLI (`op`), then set "
            "providers.secrets.adapter: op_cli.",
        )
