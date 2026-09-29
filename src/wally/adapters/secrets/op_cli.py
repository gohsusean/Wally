"""1Password CLI adapter — resolves `op://` references at execution time."""

from __future__ import annotations

import shutil
import subprocess

from wally.exceptions import ProviderUnavailableError
from wally.runtime.secrets_safety import evaluate_secret_reference


class OnePasswordCliAdapter:
    """Resolve secrets via `op read`. Values never appear in Wally config or knowledge."""

    def __init__(self, *, op_binary: str = "op", timeout_seconds: float = 15.0) -> None:
        self._op_binary = op_binary
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "secrets"

    def is_healthy(self) -> bool:
        if shutil.which(self._op_binary) is None:
            return False
        try:
            completed = subprocess.run(
                [self._op_binary, "whoami"],
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def resolve(self, reference: str) -> str:
        policy = evaluate_secret_reference(reference)
        if not policy.allowed:
            raise ProviderUnavailableError(self.name, policy.reason or "Invalid secret reference.")
        if shutil.which(self._op_binary) is None:
            raise ProviderUnavailableError(
                self.name,
                "1Password CLI (`op`) is not installed.",
            )
        try:
            completed = subprocess.run(
                [self._op_binary, "read", reference.strip()],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ProviderUnavailableError(
                self.name,
                "Timed out reading secret from 1Password CLI.",
            ) from None
        except OSError:
            raise ProviderUnavailableError(
                self.name,
                "Could not execute 1Password CLI.",
            ) from None
        if completed.returncode != 0:
            raise ProviderUnavailableError(
                self.name,
                "1Password CLI could not resolve the secret reference. "
                "Confirm `op signin` and that the item exists.",
            )
        value = completed.stdout.rstrip("\n")
        if not value:
            raise ProviderUnavailableError(self.name, "1Password returned an empty secret.")
        return value
