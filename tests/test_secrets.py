"""Secrets provider, policy, and credential injection tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_finance import _MockKnowledge, _MockWorkflow, _settings
from wally.adapters.browser.recording import RecordingBrowserAdapter
from wally.adapters.finance.local import LocalFinanceAdapter
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.adapters.secrets.op_cli import OnePasswordCliAdapter
from wally.adapters.secrets.stub import UnconfiguredSecretsProvider
from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ActionClass
from wally.models.browser import BrowserActionType
from wally.models.workflow import WorkflowDefinition
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


def test_workflow_secret_injection_from_knowledge() -> None:
    workflow = _MockWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
            )
        ]
    )
    secrets = MemorySecretsProvider({"op://Personal/Bank/otp": "123456"})
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=workflow,
        secrets=GovernedSecretsResolver(secrets),
    )
    adapter.trigger_payment(
        "pay-bill-bank-transfer",
        parameters={"amount": "10"},
        bill={"workflow_secret_refs": {"otp": "op://Personal/Bank/otp"}},
        payment_method="bank_transfer",
        authorized=True,
    )
    assert workflow.triggered[0][1]["otp"] == "123456"
    assert workflow.triggered[0][1]["amount"] == "10"


def test_load_settings_secrets_defaults(project_root: Path) -> None:
    from wally.config.loader import load_settings

    settings = load_settings(project_root=project_root, config_name="macbook")
    assert settings.secrets_enabled is True
    assert settings.secrets_adapter == "op_cli"
    assert settings.telegram_bot_token_ref == "keychain://com.wally.telegram/bot-token"
    assert (
        settings.telegram_bot_token_source_ref == "op://Private/Wally Telegram Bot/password"
    )
    assert settings.telegram_owner_user_id == "79539710"
