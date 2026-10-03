"""v0.18 Telegram: owner pin, opaque callbacks, ingress replay, one decision card."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.audit.logger import AuditLogger
from wally.cli import build_parser
from wally.exceptions import ProviderUnavailableError
from wally.gateway.service import GatewayRuntime
from wally.ops.request_propose import TrustedRecord
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.runtime.principals import LOCAL_OPERATOR_CHANNELS, PrincipalAuthority
from wally.telegram.ingress import LEASE
from wally.telegram.outbox import OUTBOUND_SEMANTICS
from wally.telegram.poll import resolve_bot_token, telegram_registration
from wally.telegram.service import TelegramConfig, TelegramService, telegram_policy

OWNER = "42"
CHAT = "42"
TOKEN = "super-secret-token"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SEND = "Send the Fire insurance to Maybank"
RECORDS = (
    TrustedRecord("doc-fire", "Fire insurance", "document"),
    TrustedRecord("rec-may", "Maybank", "recipient"),
)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.answers: list[tuple[str, str]] = []

    def send_message(
        self, chat_id: str, text: str, buttons: list[dict[str, str]] | None = None
    ) -> str:
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return str(len(self.sent))

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.answers.append((callback_id, text))

    def get_updates(self, offset: int) -> list[dict]:
        del offset
        return []


def _world(
    tmp_path: Path, *, limit: int = 8
) -> tuple[OperationsStore, TelegramService, FakeTelegram]:
    store = OperationsStore(tmp_path / "operations.db")
    config = TelegramConfig(TOKEN, OWNER, "gateway-secret", chat_id=CHAT)
    authority = PrincipalAuthority({**LOCAL_OPERATOR_CHANNELS, "telegram": telegram_policy()})
    audit = AuditLogger(tmp_path / "audit")
    ops = ObserveBriefService(store, audit=audit, authority=authority)
    runtime = GatewayRuntime(
        store,
        authority,
        (telegram_registration(config),),
        ops=ops,
        audit=audit,
        trusted_records=RECORDS,
    )
    transport = FakeTelegram()
    service = TelegramService(
        runtime, store, config, transport, RECORDS, message_limit=limit
    )
    return store, service, transport


def _msg(
    update_id: int,
    text: str,
    *,
    user: str = OWNER,
    chat: str = CHAT,
    chat_type: str = "private",
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": int(chat), "type": chat_type},
            "from": {"id": int(user)},
            "text": text,
        },
    }


def _callback(update_id: int, data: str, *, user: str = OWNER, chat: str = CHAT) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb-{update_id}",
            "from": {"id": int(user)},
            "data": data,
            "message": {"chat": {"id": int(chat), "type": "private"}},
        },
    }


def _cards(transport: FakeTelegram) -> list[dict]:
    return [item for item in transport.sent if item["buttons"]]


def _proposal(store: OperationsStore):
    rows = store.list_proposals()
    assert len(rows) == 1
    return rows[0]


def test_unknown_user_and_group_write_nothing(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND, user="99"), NOW)
    service.handle_update(_msg(2, SEND, chat_type="group"), NOW)
    assert store.list_proposals() == []
    assert transport.sent[0]["text"] == "This bot is private."
    assert len(transport.sent) == 1


def test_request_creates_one_decision_card(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    proposal = _proposal(store)
    cards = _cards(transport)
    assert len(cards) == 1
    assert service.outbox.count() == 1
    data = " ".join(button["callback_data"] for button in cards[0]["buttons"])
    assert proposal.id not in data
    assert proposal.fingerprint not in data
    assert ".a" in data and ".n" in data
    assert "Not now" in {button["text"] for button in cards[0]["buttons"]}
    assert TOKEN not in cards[0]["text"]
    assert "grant" not in cards[0]["text"]


def test_callback_uses_the_server_nonce(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    proposal = _proposal(store)
    nonce = service.outbox.latest_decision(CHAT).callback_nonce
    service.handle_update(_callback(2, f"{proposal.id}.a"), NOW)
    service.handle_update(_callback(3, "unknown-nonce.a"), NOW)
    assert store.get_proposal(proposal.id).status.value == "proposed"
    service.handle_update(_callback(4, f"{nonce}.a"), NOW)
    decided = store.get_proposal(proposal.id)
    assert decided.status.value == "approved"
    assert decided.decision_origin == "telegram"
    assert decided.decision_principal == "owner"
    assert decided.decision_fingerprint == proposal.fingerprint
    service.handle_update(_callback(5, f"{nonce}.a"), NOW)
    assert store.get_proposal(proposal.id).status.value == "approved"


def test_other_user_cannot_use_the_nonce(tmp_path: Path) -> None:
    store, service, _transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    nonce = service.outbox.latest_decision(CHAT).callback_nonce
    service.handle_update(_callback(2, f"{nonce}.a", user="99"), NOW)
    assert _proposal(store).status.value == "proposed"


def test_stale_fingerprint_writes_nothing(tmp_path: Path) -> None:
    store, service, _transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    row = service.outbox.latest_decision(CHAT)
    with store._connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE notification_outbox SET fingerprint = ? WHERE id = ?",
            ("stale-fingerprint", row.id),
        )
    service.handle_update(_callback(2, f"{row.callback_nonce}.a"), NOW)
    assert _proposal(store).status.value == "proposed"


def test_same_update_and_restart_replay_do_not_duplicate(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path)
    assert service.handle_update(_msg(1, SEND), NOW) == "submitted"
    assert service.handle_update(_msg(1, SEND), NOW) == "replayed"
    assert len(store.list_proposals()) == 1
    assert len(_cards(transport)) == 1
    with store._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM telegram_updates")
        conn.execute("UPDATE telegram_cursor SET next_offset = 0")
    assert service.handle_update(_msg(1, SEND), NOW) == "submitted"
    assert len(store.list_proposals()) == 1
    with store._connect() as conn:  # noqa: SLF001
        count = conn.execute("SELECT COUNT(*) AS n FROM gateway_requests").fetchone()["n"]
    assert count == 1


def test_not_now_leaves_the_proposal_pending(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    nonce = service.outbox.latest_decision(CHAT).callback_nonce
    service.handle_update(_callback(2, f"{nonce}.n"), NOW)
    assert _proposal(store).status.value == "proposed"
    assert service.outbox.latest_decision(CHAT).status == "dismissed"
    assert any("No reminder is scheduled" in text for _callback_id, text in transport.answers)


def test_approve_text_shows_the_card_and_does_not_decide(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path)
    service.handle_update(_msg(1, f"{SEND}. I approve this."), NOW)
    assert _proposal(store).status.value == "proposed"
    before = service.outbox.count()
    cards_before = len(_cards(transport))
    service.handle_update(_msg(2, "approve it"), NOW)
    assert _proposal(store).status.value == "proposed"
    assert service.outbox.count() == before
    assert len(_cards(transport)) == cards_before + 1


def test_stale_sending_is_retried_and_delivered_is_not(tmp_path: Path) -> None:
    _store, service, transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    assert len(_cards(transport)) == 1
    row = service.outbox.latest_decision(CHAT)
    old = (NOW - timedelta(minutes=5)).isoformat()
    with service.outbox._store._connect() as conn:  # noqa: SLF001
        conn.execute(
            """
            UPDATE notification_outbox
            SET status = 'sending', locked_at = ?, telegram_message_id = ''
            WHERE id = ?
            """,
            (old, row.id),
        )
    assert service.deliver_pending(NOW) == 1
    assert len(_cards(transport)) == 2
    assert service.outbox.get(row.id).status == "delivered"
    assert service.deliver_pending(NOW) == 0
    assert len(_cards(transport)) == 2


def test_outbound_semantics_are_at_least_once(tmp_path: Path) -> None:
    del tmp_path
    assert OUTBOUND_SEMANTICS == (
        "at-least-once delivery with best-effort duplicate suppression"
    )
    assert "exactly-once" not in OUTBOUND_SEMANTICS


def test_matter_continues_from_chatgpt_into_telegram(tmp_path: Path) -> None:
    store, service, _transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    proposal = _proposal(store)
    handles = [
        item for item in store.list_active_matters() if item.matter_id == proposal.matter_id
    ]
    assert len(handles) == 1
    store.touch_session(
        handles[0].id,
        channel="chatgpt",
        external_session_ref="conv-chatgpt",
        seen_at=NOW.isoformat(),
    )
    service.handle_update(_msg(2, "What is going on with the Fire insurance?"), NOW)
    again = store.get_active_matter(handles[0].id)
    channels = {session.channel for session in again.sessions}
    assert channels == {"chatgpt", "telegram"}


def test_execute_is_not_available(tmp_path: Path) -> None:
    store, service, _transport = _world(tmp_path)
    service.handle_update(_msg(1, SEND), NOW)
    proposal = _proposal(store)
    result = service.runtime.dispatch(
        adapter_id="telegram",
        credential="gateway-secret",
        op="execute",
        body={"proposal_id": proposal.id},
        now=NOW,
    )
    assert result.ok is False
    assert _proposal(store).status.value == "proposed"


def test_second_poller_does_not_take_the_lease(tmp_path: Path) -> None:
    _store, service, _transport = _world(tmp_path)
    assert service.ingress.try_acquire("poll-a", NOW) is True
    assert service.ingress.try_acquire("poll-b", NOW) is False
    assert service.ingress.try_acquire("poll-b", NOW + LEASE + timedelta(seconds=1)) is True


def test_rate_limit_rejects_instead_of_dropping(tmp_path: Path) -> None:
    store, service, transport = _world(tmp_path, limit=1)
    service.handle_update(_msg(1, "What needs my attention?"), NOW)
    service.handle_update(_msg(2, SEND), NOW)
    assert store.list_proposals() == []
    assert transport.sent[-1]["text"] == "Too many messages. Send that again in a moment."


def test_edited_message_is_ignored(tmp_path: Path) -> None:
    store, service, _transport = _world(tmp_path)
    service.handle_update(
        {"update_id": 1, "edited_message": _msg(1, SEND)["message"]},
        NOW,
    )
    assert store.list_proposals() == []


def test_bot_token_comes_from_the_secrets_provider(tmp_path: Path) -> None:
    canary = "canary-bot-token-value"
    ref = "op://Personal/Wally/telegram"
    provider = MemorySecretsProvider({ref: canary})
    audit = AuditLogger(tmp_path / "audit")
    token = resolve_bot_token(ref, provider=provider, audit=audit, env={})
    assert token == canary
    assert provider.resolve_calls == [ref]
    logged = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "audit").glob("*.jsonl")
    )
    assert ref in logged
    assert canary not in logged
    assert "telegram_bot" in logged


def test_raw_bot_token_is_not_a_secret_reference() -> None:
    provider = MemorySecretsProvider({})
    try:
        resolve_bot_token(
            "123456:abcdefghijklmnopqrstuvwxyz",
            provider=provider,
            audit=None,
            env={},
        )
    except ProviderUnavailableError as exc:
        assert "123456:" not in str(exc)
    else:
        raise AssertionError("raw token was accepted")


def test_env_token_remains_for_local_injection() -> None:
    token = resolve_bot_token(
        "",
        provider=None,
        audit=None,
        env={"WALLY_TELEGRAM_BOT_TOKEN": "local-test-token"},
    )
    assert token == "local-test-token"


def test_telegram_poll_command_parses() -> None:
    args = build_parser().parse_args(["telegram", "poll"])
    assert args.cli_command == "telegram"
    assert args.telegram_command == "poll"
