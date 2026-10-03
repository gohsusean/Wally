"""Map one Telegram update onto the Gateway. Text never records a decision."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from wally.gateway.service import TELEGRAM_CHANNEL, GatewayHost, GatewayRuntime
from wally.models.ops import ExecutionStatus, ProposalStatus
from wally.models.principal import Capability
from wally.ops.decisions import UserDecision
from wally.ops.request_propose import TrustedRecord
from wally.ops.store import OperationsStore
from wally.runtime.principals import ChannelPolicy
from wally.telegram.client import TelegramTransport
from wally.telegram.ingress import TelegramIngress
from wally.telegram.outbox import Notification, NotificationOutbox

_CALLBACK = re.compile(r"^([A-Za-z0-9_-]{8,32})\.([arn])$")
_DECISION_UTTERANCE = re.compile(r"^(please\s+)?(approve|reject)(\s+it)?[.!]?$", re.IGNORECASE)
_ACTIONS = {"a": "approve", "r": "reject", "n": "not_now"}
_DECISIONS = {"approve": UserDecision.APPROVE, "reject": UserDecision.REJECT}
_ARCHIVE = frozenset({"archive", "archive that", "i dealt with this already"})
_EXECUTION_KIND = {
    ExecutionStatus.EXECUTED_UNVERIFIED.value: "execution_completed",
    ExecutionStatus.VERIFIED_SUCCESS.value: "verification_completed",
    ExecutionStatus.VERIFIED_FAILURE.value: "verification_failed",
    ExecutionStatus.FAILED.value: "verification_failed",
}
_PRIVATE = "This bot is private."
_NOT_NOW = "Still pending. No reminder is scheduled."
_RATE = "Too many messages. Send that again in a moment."
_NO_CARD = "There is no proposal waiting for a decision."


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    owner_user_id: str
    gateway_credential: str
    chat_id: str = ""


def telegram_policy() -> ChannelPolicy:
    return ChannelPolicy(
        "telegram_private",
        frozenset(
            {
                Capability.SUBMIT_REQUEST,
                Capability.READ_CONTEXT,
                Capability.LINK_CHANNEL,
                Capability.DECIDE_PROPOSAL,
            }
        ),
    )


class TelegramService:
    """One private chat with the allowlisted owner. Groups are ignored."""

    def __init__(
        self,
        runtime: GatewayRuntime,
        store: OperationsStore,
        config: TelegramConfig,
        transport: TelegramTransport,
        records: tuple[TrustedRecord, ...] = (),
        *,
        message_limit: int = 8,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._config = config
        self._transport = transport
        self._records = records
        self._outbox = NotificationOutbox(store)
        self._ingress = TelegramIngress(store)
        self._limit = message_limit
        self._hits: list[datetime] = []
        if config.chat_id:
            self._ingress.remember_chat(config.chat_id)

    @property
    def outbox(self) -> NotificationOutbox:
        return self._outbox

    @property
    def ingress(self) -> TelegramIngress:
        return self._ingress

    @property
    def runtime(self) -> GatewayRuntime:
        return self._runtime

    def handle_update(self, update: dict, now: datetime | None = None) -> str:
        current = now or datetime.now(UTC)
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return "ignored"
        if self._ingress.seen(update_id):
            return "replayed"
        outcome = self._apply(update, current)
        self._ingress.record(update_id, outcome, current)
        self._ingress.advance(update_id)
        return outcome

    def sync(self, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        chat_id = self._known_chat()
        owner = self._config.owner_user_id
        for proposal in self._store.list_proposals(status=ProposalStatus.PROPOSED):
            self._outbox.enqueue_decision(
                proposal_id=proposal.id,
                fingerprint=proposal.fingerprint,
                owner_user_id=owner,
                chat_id=chat_id,
                active_matter_id="",
                correlation_id=proposal.request_provenance.correlation_id,
                now=current,
            )
        for execution in self._store.list_executions():
            kind = _EXECUTION_KIND.get(execution.status.value)
            if kind is None:
                continue
            self._outbox.enqueue_status(
                kind=kind,
                execution_id=execution.id,
                proposal_id=execution.proposal_id,
                fingerprint=execution.proposal_fingerprint,
                owner_user_id=owner,
                chat_id=chat_id,
                correlation_id=execution.correlation_id,
                now=current,
            )

    def deliver_pending(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        self._outbox.recover_stale(current)
        sent = 0
        while True:
            row = self._outbox.claim(current)
            if row is None:
                return sent
            if self._send_row(row, current):
                sent += 1

    def _apply(self, update: dict, now: datetime) -> str:
        if isinstance(update.get("edited_message"), dict):
            return "ignored"
        update_id = int(update["update_id"])
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            return self._on_callback(callback, now)
        message = update.get("message")
        if not isinstance(message, dict):
            return "ignored"
        return self._on_message(message, update_id, now)

    def _on_callback(self, callback: dict, now: datetime) -> str:
        user_id, chat = _callback_identity(callback)
        if not self._allowed(user_id, chat):
            self._answer(callback, _PRIVATE)
            return "ignored"
        chat_id = str(chat.get("id", ""))
        self._ingress.remember_chat(chat_id)
        parsed = _CALLBACK.fullmatch(str(callback.get("data") or ""))
        if parsed is None:
            self._answer(callback, "That button is not valid.")
            return "ignored"
        nonce, code = parsed.group(1), parsed.group(2)
        action = _ACTIONS[code]
        row = self._outbox.by_nonce(nonce)
        if row is None or row.chat_id != chat_id or row.owner_user_id != self._config.owner_user_id:
            self._answer(callback, "That button is not valid.")
            return "ignored"
        if not row.allows(action):
            self._answer(callback, "That button is not valid.")
            return "ignored"
        if action == "not_now":
            self._outbox.dismiss(row.id, now)
            self._answer(callback, _NOT_NOW)
            return "dismissed"
        return self._decide(row, action, callback, now)

    def _decide(self, row: Notification, action: str, callback: dict, now: datetime) -> str:
        proposal = self._store.get_proposal(row.proposal_id)
        if proposal is None or proposal.fingerprint != row.fingerprint:
            self._answer(callback, "That proposal changed. Nothing was recorded.")
            return "ignored"
        expected = _DECISIONS[action]
        if proposal.status == ProposalStatus.PROPOSED:
            result = self._call(
                "decide",
                {
                    "proposal_id": row.proposal_id,
                    "decision": expected.value,
                    "expected_fingerprint": row.fingerprint,
                },
                now,
            )
            if not result.ok:
                self._answer(callback, "Nothing was recorded.")
                return "ignored"
            self._answer(callback, "Recorded.")
            return "decided"
        already = _status_matches(proposal.status, expected)
        same = proposal.decision_fingerprint == row.fingerprint
        if already and same:
            self._answer(callback, "Already recorded.")
            return "decided"
        self._answer(callback, "That proposal is no longer pending.")
        return "ignored"

    def _on_message(self, message: dict, update_id: int, now: datetime) -> str:
        user = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        user_id = str(user.get("id", ""))
        chat_id = str(chat.get("id", ""))
        if chat.get("type") != "private":
            return "ignored"
        if user_id != self._config.owner_user_id or not self._config.owner_user_id:
            if chat_id:
                self._transport.send_message(chat_id, _PRIVATE)
            return "ignored"
        self._ingress.remember_chat(chat_id)
        text = str(message.get("text") or "")
        if message.get("forward_origin") or message.get("forward_from"):
            text = text  # still evidence; the sender id was already checked
        if not self._allow(now):
            self._transport.send_message(chat_id, _RATE)
            return "rejected"
        return self._route(text, chat_id, update_id, now)

    def _route(self, text: str, chat_id: str, update_id: int, now: datetime) -> str:
        norm = _norm(text)
        if _DECISION_UTTERANCE.fullmatch(norm):
            self._resurface(chat_id, now)
            return "shown"
        if norm in _ARCHIVE:
            return self._archive(chat_id, now)
        if _is_question(norm):
            self._link_mentioned(norm, chat_id, now)
            self.sync(now)
            self.deliver_pending(now)
            self._transport.send_message(chat_id, self._attention(norm))
            return "read"
        document, recipient = _hints(self._records, norm)
        if not document or not recipient:
            self._transport.send_message(
                chat_id, "Name one trusted document and one trusted recipient."
            )
            return "ignored"
        result = self._call(
            "submit_request",
            {
                "document_hint": document,
                "recipient_hint": recipient,
                "external_request_ref": f"tg-{update_id}" if update_id else "",
                "evidence": [{"kind": "latest_user", "text": text[:280]}],
            },
            now,
        )
        if not result.ok:
            self._transport.send_message(chat_id, result.error or "Nothing was saved.")
            return "ignored"
        proposal = result.data.get("proposal")
        if not isinstance(proposal, dict):
            self._transport.send_message(chat_id, "Nothing was proposed.")
            return "ignored"
        self._enqueue_and_deliver(proposal, result.data, chat_id, now)
        return "submitted"

    def _enqueue_and_deliver(
        self, proposal: dict, data: dict, chat_id: str, now: datetime
    ) -> None:
        row = self._outbox.enqueue_decision(
            proposal_id=str(proposal.get("id") or ""),
            fingerprint=str(proposal.get("fingerprint") or ""),
            owner_user_id=self._config.owner_user_id,
            chat_id=chat_id,
            active_matter_id=str(data.get("active_matter_id") or ""),
            correlation_id=str(data.get("correlation_id") or ""),
            now=now,
        )
        self.deliver_pending(now)
        if row.status == "pending":
            claimed = self._outbox.claim(now, row.id)
            if claimed is not None:
                self._send_row(claimed, now)

    def _resurface(self, chat_id: str, now: datetime) -> None:
        row = self._pending_card(chat_id)
        if row is None:
            self._transport.send_message(chat_id, _NO_CARD)
            return
        if row.status == "pending":
            self.deliver_pending(now)
            return
        proposal = self._store.get_proposal(row.proposal_id)
        if proposal is None:
            self._transport.send_message(chat_id, _NO_CARD)
            return
        self._transport.send_message(
            chat_id,
            _card(proposal.title, proposal.suggestion, proposal.id),
            _buttons(row),
        )

    def _pending_card(self, chat_id: str) -> Notification | None:
        row = self._outbox.latest_decision(chat_id)
        if row is None:
            return None
        proposal = self._store.get_proposal(row.proposal_id)
        if proposal is None or proposal.status != ProposalStatus.PROPOSED:
            return None
        if proposal.fingerprint != row.fingerprint:
            return None
        return row

    def _link_mentioned(self, norm: str, chat_id: str, now: datetime) -> None:
        matched = []
        for handle in self._store.list_active_matters():
            matter = self._store.get_matter(handle.matter_id) if handle.matter_id else None
            title = matter.title if matter is not None else handle.title
            if _norm(title) and _norm(title) in norm:
                matched.append(handle)
        if len(matched) != 1:
            return
        self._call(
            "link_channel",
            {"active_matter_id": matched[0].id, "external_session_ref": chat_id},
            now,
        )

    def _archive(self, chat_id: str, now: datetime) -> str:
        handles = self._store.list_active_matters()
        linked = [
            item
            for item in handles
            if any(
                session.channel == TELEGRAM_CHANNEL and session.external_session_ref == chat_id
                for session in item.sessions
            )
        ]
        if len(linked) != 1:
            self._transport.send_message(chat_id, "Say which matter to archive.")
            return "ignored"
        result = self._call(
            "set_visibility",
            {"active_matter_id": linked[0].id, "visibility": "archived"},
            now,
        )
        if not result.ok:
            self._transport.send_message(chat_id, "Nothing was archived.")
            return "ignored"
        self._transport.send_message(
            chat_id, "Archived the handle. The matter itself is unchanged."
        )
        return "archived"

    def _attention(self, norm: str) -> str:
        proposals = self._store.list_proposals(status=ProposalStatus.PROPOSED)
        if "attention" in norm:
            if not proposals:
                return "Nothing is waiting for a decision."
            lines = [f"{item.title} — {item.status.value}" for item in proposals]
            return "\n".join(lines)
        titled = [item for item in proposals if _norm(item.title) and _norm(item.title) in norm]
        if len(titled) == 1:
            item = titled[0]
            return f"{item.title}\n{item.suggestion}\nStatus: {item.status.value}"
        if not proposals:
            return "Nothing is waiting for a decision."
        return "\n".join(f"{item.title} — {item.status.value}" for item in proposals)

    def _send_row(self, row: Notification, now: datetime) -> bool:
        proposal = self._store.get_proposal(row.proposal_id) if row.proposal_id else None
        title = proposal.title if proposal is not None else row.kind
        suggestion = proposal.suggestion if proposal is not None else ""
        text = _card(title, suggestion, row.proposal_id or row.execution_id)
        if row.kind == "verification_completed":
            text = f"{title}\nCompleted and verified."
        elif row.kind == "verification_failed":
            text = f"{title}\nVerification failed."
        elif row.kind == "execution_completed":
            text = f"{title}\nExecution finished. Verification is still open."
        buttons = _buttons(row) if row.callback_nonce else None
        try:
            message_id = self._transport.send_message(row.chat_id, text, buttons)
        except Exception as exc:
            self._outbox.mark_retry(row, _safe_error(exc), now)
            return False
        self._outbox.mark_delivered(row.id, message_id, now)
        return True

    def _call(self, op: str, body: dict, now: datetime):
        return self._runtime.dispatch(
            adapter_id="telegram",
            credential=self._config.gateway_credential,
            op=op,
            body=body,
            now=now,
            host=GatewayHost(
                connection_authenticated=True,
                subject=self._config.owner_user_id,
                session=self._known_chat(),
            ),
        )

    def _allowed(self, user_id: str, chat: dict) -> bool:
        if not self._config.owner_user_id or user_id != self._config.owner_user_id:
            return False
        return chat.get("type") == "private"

    def _known_chat(self) -> str:
        return self._config.chat_id or self._ingress.chat_id()

    def _allow(self, now: datetime) -> bool:
        window = now - timedelta(seconds=10)
        self._hits = [item for item in self._hits if item >= window]
        if len(self._hits) >= self._limit:
            return False
        self._hits.append(now)
        return True

    def _answer(self, callback: dict, text: str) -> None:
        callback_id = str(callback.get("id") or "")
        if callback_id:
            self._transport.answer_callback(callback_id, text)


def _callback_identity(callback: dict) -> tuple[str, dict]:
    user = callback.get("from") if isinstance(callback.get("from"), dict) else {}
    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return str(user.get("id", "")), chat


def _hints(records: tuple[TrustedRecord, ...], text: str) -> tuple[str, str]:
    documents = [
        item.title
        for item in records
        if item.kind == "document" and _norm(item.title) and _norm(item.title) in text
    ]
    recipients = [
        item.title
        for item in records
        if item.kind == "recipient" and _norm(item.title) and _norm(item.title) in text
    ]
    if len(documents) == 1 and len(recipients) == 1:
        return documents[0], recipients[0]
    return "", ""


def _is_question(text: str) -> bool:
    return text.startswith("what") or text.startswith("how") or "attention" in text


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


def _card(title: str, suggestion: str, identity: str) -> str:
    identity_line = f"Proposal {identity}" if identity else ""
    lines = [line for line in (title, suggestion, identity_line) if line]
    return "\n".join(lines)


def _buttons(row: Notification) -> list[dict[str, str]]:
    nonce = row.callback_nonce
    return [
        {"text": "Approve", "callback_data": f"{nonce}.a"},
        {"text": "Reject", "callback_data": f"{nonce}.r"},
        {"text": "Not now", "callback_data": f"{nonce}.n"},
    ]


def _status_matches(status: ProposalStatus, decision: UserDecision) -> bool:
    if decision == UserDecision.APPROVE:
        return status == ProposalStatus.APPROVED
    if decision == UserDecision.REJECT:
        return status == ProposalStatus.REJECTED
    return False


def _safe_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())[:160]
    lowered = text.casefold()
    if "token" in lowered or "grant" in lowered or "secret" in lowered:
        return "delivery failed"
    return text or "delivery failed"
