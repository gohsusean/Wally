"""Long-poll loop for the single local Telegram consumer."""

from __future__ import annotations

import os
import re
import secrets
import sys
from datetime import UTC, datetime
from uuid import uuid4

from wally.audit.logger import AuditLogger
from wally.chatgpt.http import load_trusted_records
from wally.config.loader import Settings
from wally.exceptions import ProviderUnavailableError
from wally.gateway.service import TELEGRAM_CHANNEL, AdapterRegistration, GatewayRuntime
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.providers.secrets import SecretsProvider
from wally.runtime.principals import LOCAL_OPERATOR_CHANNELS, PrincipalAuthority
from wally.runtime.secrets_safety import evaluate_secret_reference
from wally.telegram.client import BotClient, TelegramTransport
from wally.telegram.outbox import OUTBOUND_SEMANTICS
from wally.telegram.service import TelegramConfig, TelegramService, telegram_policy


def telegram_registration(config: TelegramConfig) -> AdapterRegistration:
    return AdapterRegistration(
        "telegram",
        TELEGRAM_CHANNEL,
        config.gateway_credential,
        approval_adapter=False,
    )


def config_from_env(env: dict[str, str]) -> TelegramConfig:
    """Test and local injection. The deployed poller uses :func:`resolve_bot_token`."""
    return TelegramConfig(
        bot_token=env.get("WALLY_TELEGRAM_BOT_TOKEN", ""),
        owner_user_id=env.get("WALLY_TELEGRAM_OWNER_USER_ID", "").strip(),
        gateway_credential=env.get("WALLY_TELEGRAM_GATEWAY_CREDENTIAL", ""),
        chat_id=env.get("WALLY_TELEGRAM_CHAT_ID", "").strip(),
    )


def resolve_bot_token(
    token_ref: str,
    *,
    provider: SecretsProvider | None,
    audit: AuditLogger | None,
    env: dict[str, str],
) -> str:
    """Resolve the bot token from an ``op://`` ref, or from the environment for tests.

    The owner user id is not a secret and is not resolved here. The token value
    is not written to the audit log.
    """
    ref = token_ref.strip()
    if not ref:
        return env.get("WALLY_TELEGRAM_BOT_TOKEN", "")
    policy = evaluate_secret_reference(ref)
    if not policy.allowed:
        raise ProviderUnavailableError("secrets", policy.reason or "Invalid secret reference.")
    if provider is None or not provider.is_healthy():
        raise ProviderUnavailableError(
            "secrets",
            "Enable providers.secrets and sign in to 1Password to read the Telegram bot token.",
        )
    value = provider.resolve(ref)
    if audit is not None:
        audit.log_simple(
            event_type="secrets_resolve",
            session_id="telegram",
            outcome="resolved",
            provider="secrets",
            parameters={"reference": ref, "purpose": "telegram_bot"},
        )
    return value


def build_service(
    settings: Settings,
    config: TelegramConfig,
    transport: TelegramTransport,
) -> TelegramService:
    store = OperationsStore(settings.ops_database)
    authority = PrincipalAuthority(
        {**LOCAL_OPERATOR_CHANNELS, TELEGRAM_CHANNEL: telegram_policy()}
    )
    audit = AuditLogger(settings.audit_directory)
    ops = ObserveBriefService(store, audit=audit, authority=authority)
    records = load_trusted_records(settings.project_root / "config" / "chatgpt.yaml")
    runtime = GatewayRuntime(
        store,
        authority,
        (telegram_registration(config),),
        ops=ops,
        audit=audit,
        trusted_records=records,
    )
    return TelegramService(runtime, store, config, transport, records)


def poll_once(service: TelegramService, transport: TelegramTransport, holder: str) -> bool:
    now = datetime.now(UTC)
    if not service.ingress.try_acquire(holder, now):
        return False
    service.sync(now)
    updates = transport.get_updates(service.ingress.offset())
    for update in updates:
        service.handle_update(update, now)
    service.deliver_pending(now)
    return True


def poll_forever(service: TelegramService, transport: TelegramTransport) -> None:
    holder = f"telegram-{uuid4().hex[:8]}"
    print("Telegram long poll started. Decisions stay on the approval card.")
    print(OUTBOUND_SEMANTICS)
    while True:
        if not poll_once(service, transport, holder):
            print("Another Telegram poller holds the lease.", flush=True)
            return


def serve(
    settings: Settings,
    *,
    secrets_provider: SecretsProvider | None,
    audit: AuditLogger | None,
) -> int:
    env = dict(os.environ)
    try:
        token = resolve_bot_token(
            settings.telegram_bot_token_ref,
            provider=secrets_provider,
            audit=audit,
            env=env,
        )
    except ProviderUnavailableError as exc:
        print(_public(str(exc)), file=sys.stderr)
        return 1
    owner = settings.telegram_owner_user_id or env.get("WALLY_TELEGRAM_OWNER_USER_ID", "").strip()
    if not token or not owner:
        print(
            "Configure telegram.bot_token_ref (an op:// pointer) and "
            "telegram.owner_user_id. WALLY_TELEGRAM_BOT_TOKEN remains a local test injection.",
            file=sys.stderr,
        )
        return 1
    credential = env.get("WALLY_TELEGRAM_GATEWAY_CREDENTIAL", "") or secrets.token_urlsafe(32)
    config = TelegramConfig(
        bot_token=token,
        owner_user_id=owner,
        gateway_credential=credential,
        chat_id=env.get("WALLY_TELEGRAM_CHAT_ID", "").strip(),
    )
    client = BotClient(config.bot_token)
    service = build_service(settings, config, client)
    poll_forever(service, client)
    return 0


_TOKEN_LEAK = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


def _public(message: str) -> str:
    return _TOKEN_LEAK.sub("[redacted]", message)
