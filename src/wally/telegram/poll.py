"""Long-poll loop for the single local Telegram consumer."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from wally.audit.logger import AuditLogger
from wally.chatgpt.http import load_trusted_records
from wally.config.loader import Settings
from wally.gateway.service import TELEGRAM_CHANNEL, AdapterRegistration, GatewayRuntime
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.runtime.principals import LOCAL_OPERATOR_CHANNELS, PrincipalAuthority
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
    return TelegramConfig(
        bot_token=env.get("WALLY_TELEGRAM_BOT_TOKEN", ""),
        owner_user_id=env.get("WALLY_TELEGRAM_OWNER_USER_ID", "").strip(),
        gateway_credential=env.get("WALLY_TELEGRAM_GATEWAY_CREDENTIAL", ""),
        chat_id=env.get("WALLY_TELEGRAM_CHAT_ID", "").strip(),
    )


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


def serve_from_env(settings: Settings) -> int:
    config = config_from_env(dict(os.environ))
    if not config.bot_token or not config.owner_user_id or not config.gateway_credential:
        print(
            "Set WALLY_TELEGRAM_BOT_TOKEN, WALLY_TELEGRAM_OWNER_USER_ID, "
            "and WALLY_TELEGRAM_GATEWAY_CREDENTIAL.",
        )
        return 1
    client = BotClient(config.bot_token)
    service = build_service(settings, config, client)
    poll_forever(service, client)
    return 0
