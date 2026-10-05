"""Login-keychain secret references and the Telegram credential copy."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from wally.adapters.secrets.dispatch import DispatchSecretsProvider
from wally.adapters.secrets.keychain_macos import MacKeychainSecretsProvider
from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.telegram.credential import (
    check_bot_credential,
    install_bot_credential,
    remove_bot_credential,
)

SOURCE = "op://Private/Wally Telegram Bot/password"
TARGET = "keychain://com.wally.telegram/bot-token"
CANARY = "canary-not-a-bot-token"


class _Bag:
    name = "secrets"

    def __init__(self, values: dict[str, str] | None = None, *, healthy: bool = True) -> None:
        self.values = dict(values or {})
        self.healthy = healthy
        self.stored: list[str] = []
        self.deleted: list[str] = []

    def is_healthy(self) -> bool:
        return self.healthy

    def resolve(self, reference: str) -> str:
        if reference not in self.values:
            raise ProviderUnavailableError(self.name, "Unknown secret reference.")
        return self.values[reference]

    def store(self, reference: str, value: str) -> None:
        self.stored.append(reference)
        self.values[reference] = value

    def delete(self, reference: str) -> None:
        self.deleted.append(reference)
        self.values.pop(reference, None)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        project_root=Path(__file__).resolve().parents[1],
        telegram_bot_token_ref=TARGET,
        telegram_bot_token_source_ref=SOURCE,
    )


def test_dispatch_routes_each_scheme() -> None:
    class _ReadOnly:
        name = "secrets"

        def is_healthy(self) -> bool:
            return True

        def resolve(self, reference: str) -> str:
            assert reference == SOURCE
            return CANARY

    keychain = _Bag()
    provider = DispatchSecretsProvider(_ReadOnly(), keychain)
    assert provider.resolve(SOURCE) == CANARY
    provider.store(TARGET, CANARY)
    assert keychain.values[TARGET] == CANARY
    with pytest.raises(ProviderUnavailableError, match="not written"):
        provider.store(SOURCE, CANARY)


def test_install_copies_without_printing_the_token(tmp_path: Path, capsys) -> None:
    provider = DispatchSecretsProvider(_Bag({SOURCE: CANARY}), _Bag())
    audit = AuditLogger(tmp_path / "audit")
    assert install_bot_credential(_settings(), provider, audit) == 0
    assert provider.resolve(TARGET) == CANARY
    captured = capsys.readouterr()
    assert CANARY not in captured.out
    assert CANARY not in captured.err
    logged = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "audit").glob("*.jsonl")
    )
    assert TARGET in logged
    assert SOURCE in logged
    assert CANARY not in logged


def test_check_and_remove_do_not_print_the_token(tmp_path: Path, capsys) -> None:
    provider = DispatchSecretsProvider(_Bag(), _Bag({TARGET: CANARY}))
    audit = AuditLogger(tmp_path / "audit")
    assert check_bot_credential(_settings(), provider, audit) == 0
    assert remove_bot_credential(_settings(), provider) == 0
    captured = capsys.readouterr()
    assert CANARY not in captured.out
    assert CANARY not in captured.err
    with pytest.raises(ProviderUnavailableError):
        provider.resolve(TARGET)


def test_credential_command_uses_the_app_secrets_provider(tmp_path: Path, capsys) -> None:
    from wally.cli import _run_telegram_credential

    provider = DispatchSecretsProvider(_Bag({SOURCE: CANARY}), _Bag())
    app = SimpleNamespace(
        settings=_settings(),
        secrets=provider,
        audit=AuditLogger(tmp_path / "audit"),
    )
    args = SimpleNamespace(credential_command="install")
    assert _run_telegram_credential(app, args) == 0
    assert provider.resolve(TARGET) == CANARY
    captured = capsys.readouterr()
    assert "Enable providers.secrets" not in captured.err
    assert CANARY not in captured.out
    assert CANARY not in captured.err


def test_login_keychain_round_trip_stays_in_process() -> None:
    if sys.platform != "darwin":
        pytest.skip("login keychain is macOS")
    provider = MacKeychainSecretsProvider()
    assert provider.is_healthy()
    reference = "keychain://com.wally.tests/canary"
    try:
        provider.store(reference, CANARY)
        assert provider.resolve(reference) == CANARY
        provider.store(reference, CANARY + "-2")
        assert provider.resolve(reference) == CANARY + "-2"
    finally:
        provider.delete(reference)
    with pytest.raises(ProviderUnavailableError, match="not found"):
        provider.resolve(reference)
