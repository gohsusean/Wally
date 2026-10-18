"""Secrets provider, policy, and credential injection tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wally.adapters.browser.recording import RecordingBrowserAdapter
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.adapters.secrets.op_cli import OnePasswordCliAdapter
from wally.adapters.secrets.stub import UnconfiguredSecretsProvider
from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.models.browser import BrowserActionType
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.secret_resolver import GovernedSecretsResolver
from wally.runtime.secrets_safety import evaluate_secret_access, evaluate_secret_reference

USERNAME_REF = "op://Personal/Streaming/username"
PASSWORD_REF = "op://Personal/Streaming/password"


def test_secret_reference_policy_allows_op_pointers() -> None:
    assert evaluate_secret_reference(USERNAME_REF).allowed is True
    assert evaluate_secret_reference("op://Private/Wally Telegram Bot/password").allowed is True
    assert evaluate_secret_reference("keychain://com.wally.telegram/bot-token").allowed is True
    assert evaluate_secret_reference("keychain://com.wally.telegram").allowed is False
    assert evaluate_secret_reference("hunter2").allowed is False
    assert evaluate_secret_reference("123456:abcdefghijklmnopqrstuvwxyz").allowed is False
    assert evaluate_secret_reference("op://only-vault").allowed is False
    assert evaluate_secret_reference("").allowed is False


def test_secret_access_requires_authorization() -> None:
    denied = evaluate_secret_access(reference=USERNAME_REF, authorized=False)
    assert denied.allowed is False
    allowed = evaluate_secret_access(reference=USERNAME_REF, authorized=True)
    assert allowed.allowed is True


def test_resolver_denies_unauthorized_and_does_not_call_provider() -> None:
    provider = MemorySecretsProvider({USERNAME_REF: "alice"})
    resolver = GovernedSecretsResolver(provider)
    with pytest.raises(ProviderUnavailableError, match="authorization"):
        resolver.resolve(USERNAME_REF, authorized=False)


def test_resolver_audits_reference_not_value(tmp_path: Path) -> None:
    provider = MemorySecretsProvider({PASSWORD_REF: "s3cret-value"})
    audit = AuditLogger(tmp_path)
    resolver = GovernedSecretsResolver(provider, audit=audit)
    value = resolver.resolve(PASSWORD_REF, authorized=True, session_id="s1", purpose="test")
    assert value == "s3cret-value"
    files = list(tmp_path.glob("*.jsonl"))
    payload = files[0].read_text(encoding="utf-8")
    assert PASSWORD_REF in payload
    assert "s3cret-value" not in payload


def test_authorized_read_does_not_require_a_separate_desktop_authentication() -> None:
    class AppAuthorizedProvider:
        name = "secrets"

        def __init__(self):
            self.reads = []

        def is_healthy(self):
            raise AssertionError("A separate health probe must not precede a credential read.")

        def resolve(self, reference):
            self.reads.append(reference)
            return "synthetic-canary"

    provider = AppAuthorizedProvider()
    resolver = GovernedSecretsResolver(provider)
    with pytest.raises(ProviderUnavailableError, match="authorization"):
        resolver.resolve(PASSWORD_REF, authorized=False)
    assert provider.reads == []
    assert resolver.resolve(PASSWORD_REF, authorized=True) == "synthetic-canary"
    assert provider.reads == [PASSWORD_REF]


def test_authorized_read_still_fails_when_provider_rejects_access() -> None:
    resolver = GovernedSecretsResolver(UnconfiguredSecretsProvider())
    with pytest.raises(ProviderUnavailableError):
        resolver.resolve(PASSWORD_REF, authorized=True)


def test_unconfigured_secrets_provider_is_unhealthy() -> None:
    provider = UnconfiguredSecretsProvider()
    assert provider.is_healthy() is False
    with pytest.raises(ProviderUnavailableError):
        provider.resolve(USERNAME_REF)


def test_op_cli_resolve_invokes_op_read() -> None:
    adapter = OnePasswordCliAdapter(op_binary="op")

    class _Result:
        returncode = 0
        stdout = "resolved-secret\n"

    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/usr/bin/op"),
        patch("wally.adapters.secrets.op_cli.subprocess.run", return_value=_Result()) as run,
    ):
        assert adapter.resolve(USERNAME_REF) == "resolved-secret"
        args = run.call_args[0][0]
        assert args[:3] == ["op", "read", USERNAME_REF]


def test_op_cli_rejects_raw_credentials() -> None:
    adapter = OnePasswordCliAdapter()
    with pytest.raises(ProviderUnavailableError, match="op://"):
        adapter.resolve("not-a-reference")


def test_op_health_uses_app_authorized_metadata_and_discards_output() -> None:
    adapter = OnePasswordCliAdapter(timeout_seconds=9)
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/usr/bin/op"),
        patch(
            "wally.adapters.secrets.op_cli.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run,
    ):
        assert adapter.is_healthy() is True
    run.assert_called_once_with(
        ["op", "vault", "list", "--format=json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=9,
        check=False,
    )


@pytest.mark.parametrize(
    "failure",
    [OSError("synthetic-canary"), subprocess.TimeoutExpired("op", 9)],
)
def test_op_health_handles_missing_connection_and_timeout(failure) -> None:
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/usr/bin/op"),
        patch("wally.adapters.secrets.op_cli.subprocess.run", side_effect=failure),
    ):
        assert OnePasswordCliAdapter().is_healthy() is False


def test_op_health_fails_closed_when_app_denies_access() -> None:
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/usr/bin/op"),
        patch(
            "wally.adapters.secrets.op_cli.subprocess.run",
            return_value=SimpleNamespace(returncode=1),
        ),
    ):
        assert OnePasswordCliAdapter().is_healthy() is False


def test_card_portal_falls_back_to_manual_auth_without_secrets() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    result = executor.run_card_portal_payment(
        bill={
            "payment_portal_url": "https://pay.example.com/streaming",
            "provider": "Streaming Co",
        }
    )
    assert result["waiting_for_user"] is True


def test_card_portal_injects_credentials_when_refs_present() -> None:
    recording = RecordingBrowserAdapter()
    secrets = MemorySecretsProvider(
        {USERNAME_REF: "alice@example.com", PASSWORD_REF: "s3cret-value"}
    )
    executor = GovernedBrowserExecutor(
        recording,
        secrets=GovernedSecretsResolver(secrets),
    )
    bill = {
        "payment_portal_url": "https://pay.example.com/streaming",
        "provider": "Streaming Co",
        "portal_username_ref": USERNAME_REF,
        "portal_password_ref": PASSWORD_REF,
        "login_username_selector": "#email",
        "login_password_selector": "#password",
        "login_submit_selector": "button[type=submit]",
    }
    result = executor.run_card_portal_payment(bill=bill, authorized=True)
    assert result["waiting_for_user"] is False
    assert result["status"] == "completed"
    actions = recording.session_actions(result["session_id"])
    assert any(a.action_type == BrowserActionType.FILL for a in actions)
    assert any(a.action_type == BrowserActionType.CLICK for a in actions)
    assert any(a.action_type == BrowserActionType.READ_PAGE for a in actions)


def test_governed_secret_parameter_injection_requires_authorization() -> None:
    secrets = MemorySecretsProvider({"op://Personal/Bank/otp": "123456"})
    resolver = GovernedSecretsResolver(secrets)
    result = resolver.inject_workflow_parameters(
        knowledge={"workflow_secret_refs": {"otp": "op://Personal/Bank/otp"}},
        parameters={"amount": "10"},
        authorized=True,
    )
    assert result == {"amount": "10", "otp": "123456"}


def test_load_settings_secrets_defaults(project_root: Path) -> None:
    from wally.config.loader import load_settings

    settings = load_settings(project_root=project_root, config_name="macbook")
    assert settings.secrets_enabled is True
    assert settings.secrets_adapter == "op_cli"
    assert settings.telegram_bot_token_ref == "keychain://com.wally.telegram/bot-token"
    assert settings.telegram_bot_token_source_ref == "op://Private/Wally Telegram Bot/password"
    assert settings.telegram_owner_user_id == "79539710"
