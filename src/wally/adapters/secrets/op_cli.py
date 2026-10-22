"""1Password CLI adapter — resolves `op://` references at execution time."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from urllib.parse import urlsplit

from wally.exceptions import ProviderUnavailableError
from wally.runtime.secrets_safety import evaluate_secret_reference


class OnePasswordCliAdapter:
    """Resolve secrets via `op read`. Values never appear in Wally config or knowledge."""

    def __init__(
        self, *, op_binary: str = "op", timeout_seconds: float = 90.0, account: str | None = None
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 120:
            raise ValueError("1Password timeout must be between 1 and 120 seconds.")
        if account is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,127}", account):
            raise ValueError("1Password account must be an account ID or sign-in domain.")
        self._op_binary = op_binary
        self._timeout_seconds = timeout_seconds
        self._account = account

    def _args(self, *args):
        return [self._op_binary, *args, *(["--account", self._account] if self._account else [])]

    @property
    def name(self) -> str:
        return "secrets"

    def is_healthy(self) -> bool:
        """Probe app-authorized access without requiring a shell session token.

        ``whoami`` can fail with desktop-app integration even when vault reads
        work. The documented vault probe requests no item fields and discards
        metadata and errors; it does not grant access to action credentials.
        """
        if shutil.which(self._op_binary) is None:
            return False
        try:
            completed = subprocess.run(
                self._args("vault", "list", "--format=json"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=min(15.0, self._timeout_seconds),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def _validate(self, reference):
        policy = evaluate_secret_reference(reference)
        if not policy.allowed or not reference.startswith("op://"):
            raise ProviderUnavailableError(self.name, policy.reason or "Invalid secret reference.")

    def _read(self, *args):
        if shutil.which(self._op_binary) is None:
            raise ProviderUnavailableError(
                self.name,
                "1Password CLI (`op`) is not installed.",
            )
        try:
            completed = subprocess.run(
                self._args(*args),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ProviderUnavailableError(
                self.name,
                "Timed out awaiting 1Password access; approve its local prompt before retrying.",
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
                "Unlock/authorize CLI access in the desktop app, or use `op signin`, "
                "and confirm that the item exists.",
            )
        return completed.stdout

    def resolve(self, reference: str) -> str:
        self._validate(reference)
        value = self._read("read", reference.strip()).rstrip("\n")
        if not value:
            raise ProviderUnavailableError(self.name, "1Password returned an empty secret.")
        return value

    def resolve_many(self, references: tuple[str, ...]) -> tuple[str, ...]:
        """Read a standard login pair once; never retrieve an entire item or OTP seed.

        Other references retain ordinary field-scoped reads, without automatic
        retries. No credential cache survives this request.
        """
        for reference in references:
            self._validate(reference)
        parts = [urlsplit(ref) for ref in references]
        paths = [part.path.strip("/").split("/") for part in parts]
        pair = (
            len(parts) == 2
            and all(not part.query and not part.fragment for part in parts)
            and all(len(path) == 2 for path in paths)
            and parts[0].netloc == parts[1].netloc
            and paths[0][0] == paths[1][0]
            and not parts[0].netloc.startswith("-")
            and not paths[0][0].startswith("-")
            and {path[1] for path in paths} == {"username", "password"}
        )
        if not pair:
            return tuple(self.resolve(ref) for ref in references)
        raw = self._read(
            "item",
            "get",
            paths[0][0],
            "--vault",
            parts[0].netloc,
            "--fields",
            "username,password",
            "--format=json",
            "--reveal",
        )
        try:
            fields = json.loads(raw)
            if not isinstance(fields, list) or len(fields) != 2:
                raise ValueError
            values = {field["id"]: field.get("value") for field in fields}
            if set(values) != {"username", "password"}:
                raise ValueError
            missing = [name for name in ("username", "password") if values[name] in (None, "")]
            if missing:
                raise ProviderUnavailableError(
                    self.name,
                    "1Password login item is missing "
                    + " and ".join(missing)
                    + "; update the item locally in 1Password.",
                )
            if any(not isinstance(value, str) for value in values.values()):
                raise ValueError
            return tuple(values[path[1]] for path in paths)
        except (ValueError, TypeError, KeyError):
            raise ProviderUnavailableError(
                self.name, "1Password returned an invalid scoped login response."
            ) from None
