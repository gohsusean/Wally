"""Route secret references to the provider that owns that scheme."""

from __future__ import annotations

from wally.exceptions import ProviderUnavailableError
from wally.providers.secrets import SecretsProvider


class DispatchSecretsProvider:
    """Send ``op://`` to 1Password and ``keychain://`` to the login keychain."""

    def __init__(self, onepassword: SecretsProvider, keychain: SecretsProvider) -> None:
        self._op = onepassword
        self._keychain = keychain

    @property
    def name(self) -> str:
        return "secrets"

    def is_healthy(self) -> bool:
        """Report interactive 1Password health. Keychain reads do not use this."""
        return self._op.is_healthy()

    def resolve(self, reference: str) -> str:
        return self._backend(reference).resolve(reference)

    def store(self, reference: str, value: str) -> None:
        backend = self._backend(reference)
        store = getattr(backend, "store", None)
        if store is None:
            raise ProviderUnavailableError(
                self.name,
                "This reference is not written by Wally.",
            )
        store(reference, value)

    def delete(self, reference: str) -> None:
        backend = self._backend(reference)
        delete = getattr(backend, "delete", None)
        if delete is None:
            raise ProviderUnavailableError(
                self.name,
                "This reference is not removed by Wally.",
            )
        delete(reference)

    def _backend(self, reference: str) -> SecretsProvider:
        if reference.startswith("keychain://"):
            return self._keychain
        if reference.startswith("op://"):
            return self._op
        raise ProviderUnavailableError(self.name, "Unknown secret reference.")
