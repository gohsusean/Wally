"""Copy the Telegram bot token from 1Password into the login keychain."""

from __future__ import annotations

import sys
from pathlib import Path

from wally.audit.logger import AuditLogger
from wally.config.loader import Settings
from wally.exceptions import ProviderUnavailableError
from wally.providers.secrets import SecretsProvider
from wally.telegram.poll import _public, resolve_bot_token


def install_bot_credential(
    settings: Settings,
    provider: SecretsProvider | None,
    audit: AuditLogger | None,
) -> int:
    """Resolve the 1Password source and store it at the keychain reference."""
    source, target, provider = _refs(settings, provider)
    if source is None:
        return 1
    runtime = _runtime_python(settings)
    if Path(sys.executable).resolve() != runtime.resolve():
        print(
            f"Run this with {runtime} so the LaunchAgent can read the item.",
            file=sys.stderr,
        )
        return 1
    try:
        value = resolve_bot_token(source, provider=provider, audit=audit, env={})
        provider.store(target, value)
    except ProviderUnavailableError as exc:
        print(_public(str(exc)), file=sys.stderr)
        return 1
    if audit is not None:
        audit.log_simple(
            event_type="secrets_store",
            session_id="telegram",
            outcome="stored",
            provider="secrets",
            parameters={"reference": target, "purpose": "telegram_bot"},
        )
    print("Stored the Telegram bot token in the login keychain.")
    return 0


def check_bot_credential(
    settings: Settings,
    provider: SecretsProvider | None,
    audit: AuditLogger | None,
) -> int:
    """Resolve the runtime reference and report only success or failure."""
    _source, target, provider = _refs(settings, provider, require_source=False)
    if target is None or provider is None:
        return 1
    try:
        value = resolve_bot_token(target, provider=provider, audit=audit, env={})
    except ProviderUnavailableError as exc:
        print(_public(str(exc)), file=sys.stderr)
        return 1
    if not value:
        print("The keychain item was empty.", file=sys.stderr)
        return 1
    print("Resolved the Telegram bot token from the login keychain.")
    return 0


def remove_bot_credential(settings: Settings, provider: SecretsProvider | None) -> int:
    """Delete the local keychain copy. The 1Password item stays."""
    _source, target, provider = _refs(settings, provider, require_source=False)
    if target is None or provider is None:
        return 1
    try:
        provider.delete(target)
    except ProviderUnavailableError as exc:
        print(_public(str(exc)), file=sys.stderr)
        return 1
    print("Removed the login-keychain copy of the Telegram bot token.")
    return 0


def _refs(
    settings: Settings,
    provider: SecretsProvider | None,
    *,
    require_source: bool = True,
) -> tuple[str | None, str | None, SecretsProvider | None]:
    if provider is None:
        print("Enable providers.secrets to use the login keychain.", file=sys.stderr)
        return None, None, None
    target = settings.telegram_bot_token_ref.strip()
    source = settings.telegram_bot_token_source_ref.strip()
    if not target.startswith("keychain://"):
        print("telegram.bot_token_ref must be a keychain:// pointer.", file=sys.stderr)
        return None, None, None
    if require_source and not source.startswith("op://"):
        print("telegram.bot_token_source_ref must be an op:// pointer.", file=sys.stderr)
        return None, None, None
    return source, target, provider


def _runtime_python(settings: Settings) -> Path:
    return settings.project_root / ".venv" / "bin" / "python"
