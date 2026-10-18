"""Secrets provider adapters."""

from __future__ import annotations

from wally.adapters.secrets.dispatch import DispatchSecretsProvider
from wally.adapters.secrets.keychain_macos import MacKeychainSecretsProvider
from wally.adapters.secrets.op_cli import OnePasswordCliAdapter
from wally.adapters.secrets.stub import UnconfiguredSecretsProvider
from wally.exceptions import ProviderUnavailableError


def create_secrets_provider(settings):
    if not settings.secrets_enabled:
        return None
    adapter = getattr(settings, "secrets_adapter", "op_cli")
    if adapter == "op_cli":
        return DispatchSecretsProvider(
            OnePasswordCliAdapter(
                timeout_seconds=getattr(settings, "secrets_timeout_seconds", 90),
                account=getattr(settings, "secrets_account", None),
            ),
            MacKeychainSecretsProvider(),
        )
    if adapter == "stub":
        return UnconfiguredSecretsProvider()
    if adapter == "memory":
        from wally.adapters.secrets.memory import MemorySecretsProvider

        return MemorySecretsProvider()
    raise ProviderUnavailableError("secrets", f"Unsupported secrets adapter: {adapter}")
