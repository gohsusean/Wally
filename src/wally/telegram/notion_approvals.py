"""Trusted Telegram review -> existing exact confirmation -> scoped executor.

Only authenticated Bot API callbacks enter ``handle``. Persisted reviews are
attribution, never credentials: they cannot recreate the in-process confirmation
binding after a crash. A new button press is required before any unstarted write.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wally.adapters.notion.edits import EDIT_VALUES
from wally.codex.config import EditConfig
from wally.exceptions import WallyError
from wally.models.ops import ExecutionStatus, ProposalIntent, ProposalStatus
from wally.models.principal import Capability
from wally.ops.decisions import UserDecision
from wally.ops.notion_edits import DecisionSelection, EditError, NotionEditService
from wally.presentation import change_lines, human_time
from wally.runtime.confirmation import canonical, digest
from wally.runtime.principals import ChannelPolicy
from wally.telegram.outbox import Notification, NotificationOutbox

WORKER_CHANNEL = "telegram_notion_worker"
WORKER_POLICY = ChannelPolicy(
    "trusted_scoped_telegram_worker",
    frozenset(
        {Capability.READ_CONTEXT, Capability.EXECUTE_NOTION_EDIT, Capability.VERIFY_NOTION_EDIT}
    ),
)
REVIEW_TTL = 900
MESSAGE_LIMIT = 3900  # UTF-16 units, leaving margin below Telegram's limit.


def execution_outcome(attempt):
    if attempt.status == ExecutionStatus.VERIFIED_SUCCESS:
        return "✅ Notion updated", "Verified successfully."
    if attempt.status == ExecutionStatus.RUNNING:
        return (
            "⏳ Change in progress",
            "Completion is unconfirmed. Check the result before trying again.",
        )
    if attempt.failure_category in {"patch_not_dispatched", "prewrite_check_failed"}:
        return (
            "❌ Change failed",
            "No change was sent. Review the record before requesting a new change.",
        )
    return (
        "⚠️ Change outcome uncertain",
        "Check Notion and ask Wally to verify the result. Do not apply it again.",
    )


@dataclass
class _Binding:
    principal: str
    correlation_id: str
    deadline: float
    decision: str
    executions: frozenset[str]
    guard: object = lambda: True
    consumed: set[str] = field(default_factory=set)


class TelegramEditConfirmation:
    """Not exposed through Gateway/MCP. Exact callback scope, never generic yes."""

    method = "authenticated_private_telegram_exact_callback"

    def __init__(self):
        self._binding: ContextVar[_Binding | None] = ContextVar("telegram_edit", default=None)

    @contextmanager
    def bind(self, binding: _Binding):
        token = self._binding.set(binding)
        try:
            yield
        finally:
            self._binding.reset(token)

    def confirm(self, review):
        binding = self._binding.get()
        if (
            binding is None
            or not self.dispatch_allowed()
            or review.principal != binding.principal
            or review.correlation_id != binding.correlation_id
            or review.presentation in binding.consumed
        ):
            return False
        decision = (
            review.channel == "telegram"
            and review.purpose == Capability.DECIDE_PROPOSAL
            and review.presentation == binding.decision
        )
        execution = (
            review.channel == WORKER_CHANNEL
            and review.purpose == Capability.EXECUTE_NOTION_EDIT
            and review.presentation in binding.executions
        )
        if not (decision or execution):
            return False
        binding.consumed.add(review.presentation)
        return True

    def dispatch_allowed(self):
        binding = self._binding.get()
        return binding is not None and time.time() < binding.deadline and binding.guard()


class TelegramNotionApprovals:
    def __init__(
        self,
        edits: NotionEditService,
        confirmation: TelegramEditConfirmation,
        policy,
        owner_user_id: str,
    ):
        self.edits = edits
        self.confirmation = confirmation
        self.policy = policy  # Reloaded, trusted configuration; never model arguments.
        self.owner_user_id = owner_user_id
        self.store = edits.store
        self.outbox = NotificationOutbox(self.store)
        self.dispatch_guard = lambda: True

    def _eligible(self, scope: list[dict], *, execution: bool = False):
        policy: EditConfig = self.policy()
        targets = {target.key: target for target in policy.targets}
        if execution and not policy.telegram_writes_enabled:
            raise EditError("Telegram Notion writes are disabled.")
        for item in scope:
            target = targets.get(item["target"])
            if target is None or target.key not in policy.telegram_targets:
                raise EditError("Target is not allowlisted for Telegram authorization.")
            rules = {rule.property_id: rule for rule in target.properties}
            if target.fingerprint != item["target_digest"] or not item["changes"]:
                raise EditError("Reviewed target policy changed.")
            for change in item["changes"]:
                rule = rules.get(change["property_id"])
                if (
                    rule is None
                    or rule.field not in {"amount_policy", "frequency"}
                    or rule.field != change["field"]
                    or not set(rule.values) <= EDIT_VALUES[rule.field]
                    or change["after"] not in rule.values
                ):
                    raise EditError("Change is outside Telegram's metadata policy.")

    def _scope(self, proposal, *, refresh=False):
        context = self.edits.authority.issue("telegram")
        review = self.edits.review(proposal.id, context=context, refresh=refresh)
        if datetime.fromisoformat(review["expires_at"]) <= datetime.now(UTC):
            raise EditError("Proposal expired; no actionable Telegram review.")
        spec = self.store.get_notion_edit(proposal.id)
        # Full immutable provider scope is retained; no source title/text is trusted.
        return {
            key: review[key]
            for key in ("proposal_id", "fingerprint", "target", "page_id", "changes")
        } | {key: spec[key] for key in ("database_id", "data_source_id", "target_digest")}

    def sync(self, chat_id: str, now: datetime, *, refresh_reviews=False):
        # Deliver persisted read-time findings; polling never refreshes Notion.
        for event_id, _event in self.edits.reconciliation.events(current_only=True):
            self.outbox.enqueue_decision(
                proposal_id=event_id,
                fingerprint=event_id,
                owner_user_id=self.owner_user_id,
                chat_id=chat_id,
                now=now,
                kind="notion_change_detected",
                allowed_actions="not_now",
            )
        scopes = []
        for proposal in self.store.list_proposals():
            defer_released = False
            if (
                proposal.intent == ProposalIntent.EDIT_NOTION_RECORD
                and proposal.status == ProposalStatus.DEFERRED
                and proposal.defer_until
                and datetime.fromisoformat(proposal.defer_until) <= datetime.now(UTC)
            ):
                # Existing proposal lifecycle releases defer; never mints approval.
                try:
                    self._eligible([self._scope(proposal)])
                    self.store.release_defer(
                        proposal.id,
                        updated_at=datetime.now(UTC).isoformat(),
                        status_reason="Telegram defer elapsed; review again",
                    )
                    proposal = self.store.get_proposal(proposal.id)
                    defer_released = True
                except WallyError:
                    continue
            if (
                proposal.intent != ProposalIntent.EDIT_NOTION_RECORD
                or proposal.status not in {ProposalStatus.PROPOSED, ProposalStatus.APPROVED}
                or self.store.list_executions(proposal_id=proposal.id)
            ):
                continue
            try:
                scope = self._scope(proposal)
                self._eligible([scope])
            except WallyError:
                continue
            scopes.append(scope)
            self._enqueue([scope], chat_id, now, refresh=refresh_reviews or defer_released)
        # Individual cards provide selected approval; a full-scope batch card is
        # additional convenience only when every change fits. Never abbreviate.
        for start in range(0, len(scopes), 20):
            batch = scopes[start : start + 20]
            if len(batch) > 1 and self._fits(self._text(batch)):
                self._enqueue(batch, chat_id, now, refresh=refresh_reviews)

    def _enqueue(self, scope, chat_id, now, *, refresh=False):
        text = self._text(scope)
        if not self._fits(text + "\nExpires " + "0" * 45):
            # Fail closed: not even a single target may be abbreviated for approval.
            self.store.note_edit_event(
                "telegram_review_too_large",
                {"scope_digest": digest(scope)},
                self.edits.authority.issue("telegram").provenance(),
            )
            return
        body = canonical(scope)
        with self.store._connect() as conn:  # noqa: SLF001
            existing = conn.execute(
                "SELECT notification_id,expires_at FROM telegram_edit_reviews WHERE scope=? "
                "ORDER BY expires_at DESC LIMIT 1",
                (body,),
            ).fetchone()
        if existing and existing["expires_at"] <= time.time():
            if not refresh:
                return  # No periodic re-prompt storm; owner can request fresh review.
            existing = None
        if existing:
            row = self.outbox.get(existing["notification_id"])
            if chat_id and not row.chat_id:
                self.outbox.enqueue_decision(
                    proposal_id=row.proposal_id,
                    fingerprint=row.fingerprint,
                    owner_user_id=self.owner_user_id,
                    chat_id=chat_id,
                    now=now,
                    kind=row.kind,
                    allowed_actions=row.allowed_actions,
                    generation=row.dedupe_key.rsplit(":", 1)[-1],
                )
            return
        row = self.outbox.enqueue_decision(
            proposal_id=scope[0]["proposal_id"],
            fingerprint=digest(scope),
            owner_user_id=self.owner_user_id,
            chat_id=chat_id,
            now=now,
            kind="notion_edit_review",
            allowed_actions="approve_execute,reject,later",
            generation=uuid4().hex,
        )
        with self.store._connect() as conn:  # noqa: SLF001
            conn.execute(
                "INSERT INTO telegram_edit_reviews "
                "(notification_id,scope,expires_at,owner_user_id) VALUES (?,?,?,?)",
                (row.id, body, time.time() + REVIEW_TTL, self.owner_user_id),
            )

    def render(self, row: Notification):
        with self.store._connect() as conn:  # noqa: SLF001
            review = conn.execute(
                "SELECT * FROM telegram_edit_reviews WHERE notification_id=?", (row.id,)
            ).fetchone()
        if review is None:
            raise EditError("Review is unavailable.")
        scope = json.loads(review["scope"])
        text = (
            self._text(scope)
            + "\nExpires "
            + human_time(datetime.fromtimestamp(review["expires_at"], UTC), now=datetime.now(UTC))
        )
        if not self._fits(text):
            raise EditError("Complete review exceeds Telegram's message limit.")
        buttons = None
        pending = all(
            (proposal := self.store.get_proposal(item["proposal_id"])) is not None
            and proposal.fingerprint == item["fingerprint"]
            and proposal.status in {ProposalStatus.PROPOSED, ProposalStatus.APPROVED}
            and not self.store.list_executions(proposal_id=proposal.id)
            for item in scope
        )
        if review["state"] == "open" and time.time() < review["expires_at"] and pending:
            buttons = [
                [
                    {
                        "text": "✅ Apply change" if len(scope) == 1 else "✅ Apply all",
                        "callback_data": row.callback_nonce + ".e",
                    },
                    {"text": "❌ Reject", "callback_data": row.callback_nonce + ".r"},
                ],
                [{"text": "⏰ Later", "callback_data": row.callback_nonce + ".l"}],
            ]
            if len(scope) == 1:
                buttons[1].append({"text": "View in Notion", "url": self._link(scope[0])})
            else:
                buttons.extend(
                    [[{"text": f"View record {n}", "url": self._link(item)}]]
                    for n, item in enumerate(scope, 1)
                )
        return text, buttons

    @staticmethod
    def _fits(text):
        return len(text.encode("utf-16-le")) // 2 <= MESSAGE_LIMIT

    @staticmethod
    def _link(item):
        return "https://www.notion.so/" + item["page_id"].replace("-", "")

    def _text(self, scope):
        lines = ["📝 Notion update" if len(scope) == 1 else "📝 Notion updates — apply all shown"]
        targets = {target.key: target for target in self.policy().targets}
        for item in scope:
            lines += ["", item["target"], "", *change_lines(item["changes"])]
            target = targets.get(item["target"])
            if target is not None and target.finance_id:
                lines += ["⚠️ This change will require recertification."]
        return "\n".join(lines)

    def change_notification(self, row):
        event = next(
            event
            for event_id, event in self.edits.reconciliation.events()
            if event_id == row.proposal_id
        )
        baseline = "verified record" if event["baseline"] == "verified" else "previous record"
        lines = [
            "📝 Notion change detected",
            "",
            event["target"],
            "",
            *change_lines(event["changes"]),
        ]
        if event["other_business_changes"]:
            lines += ["Other record information also changed. Review the record in Notion."]
        if event["contract_changed"]:
            lines += ["The record’s structure or review settings changed."]
        if event["catalog_changed"]:
            lines += ["Wally’s financial record also changed and needs a fresh check."]
        lines += [
            "",
            f"This differs from Wally’s last {baseline}.",
            "Review the change before relying on this information.",
        ]
        if event["certification_affected"]:
            lines += ["⚠️ This change will require recertification."]
        text = "\n".join(lines)
        if not self._fits(text):
            raise EditError("Complete change notice exceeds the message limit.")
        return text, [
            [
                {"text": "Review in Notion", "url": self._link(event)},
                {"text": "Later", "callback_data": row.callback_nonce + ".n"},
            ]
        ]

    def completion(self, proposal_id, attempt=None):
        spec = self.store.get_notion_edit(proposal_id)
        heading, outcome = (
            execution_outcome(attempt)
            if attempt
            else (
                "⏳ Change approved",
                "Awaiting execution. Open a fresh review to apply the change.",
            )
        )
        if spec and attempt and attempt.status == ExecutionStatus.VERIFIED_SUCCESS:
            with self.store._connect() as conn:  # noqa: SLF001
                baseline = conn.execute(
                    "SELECT stale FROM notion_trusted_snapshots WHERE page_id=?",
                    (spec["page_id"],),
                ).fetchone()
            if baseline and baseline["stale"]:
                heading = "⚠️ Previous Notion check is stale"
                outcome = (
                    "The record needs a fresh check. "
                    "Review its current values before relying on it."
                )
        if spec is None:
            return heading + "\n\n" + outcome
        return "\n".join(
            [heading, "", spec["target"], "", *change_lines(spec["changes"]), "", outcome]
        )

    def handle(self, row: Notification, action: str, callback: dict) -> str:
        message = callback.get("message", {})
        user = callback.get("from", {})
        chat = message.get("chat", {})
        # Callback evidence comes only from the authenticated polling transport.
        # Forwarded/copied/inaccessible messages and delivery ambiguity cannot act.
        if (
            str(user.get("id", "")) != self.owner_user_id
            or user.get("is_bot")
            or chat.get("type") != "private"
            or str(chat.get("id", "")) != row.chat_id
            or row.owner_user_id != self.owner_user_id
            or row.status != "delivered"
            or not row.telegram_message_id
            or message.get("date") == 0
            or str(message.get("message_id", "")) != row.telegram_message_id
            or any(message.get(key) for key in ("forward_origin", "forward_from", "forward_date"))
            or not callback.get("id")
            or action not in {"approve_execute", "reject", "later"}
            or not row.allows(action)
        ):
            return "This button is not valid. No change was started."
        now = time.time()
        with self.store._connect() as conn:  # noqa: SLF001
            conn.execute("BEGIN IMMEDIATE")
            review = conn.execute(
                "SELECT * FROM telegram_edit_reviews WHERE notification_id=?", (row.id,)
            ).fetchone()
            if (
                review is None
                or review["state"] != "open"
                or review["expires_at"] <= now
                or review["owner_user_id"] != self.owner_user_id
                or digest(json.loads(review["scope"])) != row.fingerprint
            ):
                return "This review expired or was already used. Open a fresh review."
            # Durable single-use claim happens BEFORE decision or provider effects.
            conn.execute(
                "UPDATE telegram_edit_reviews SET state='claimed', action=?, "
                "callback_id=?, authorized_at=? WHERE notification_id=?",
                (action, str(callback["id"]), now, row.id),
            )
            scope = json.loads(review["scope"])
            deadline = min(review["expires_at"], now + 300)
        context = self.edits.authority.issue(
            "telegram",
            external_session_ref=row.chat_id,
            external_request_ref=str(callback["id"]),
        )
        result = "⚠️ Review interrupted. Check the result before trying again."
        try:
            self._eligible(scope, execution=action == "approve_execute")
            decision = {
                "approve_execute": UserDecision.APPROVE,
                "reject": UserDecision.REJECT,
                "later": UserDecision.DEFER,
            }[action]
            until = (
                (datetime.now(UTC) + timedelta(hours=1)).isoformat() if action == "later" else ""
            )
            selections, prepared, executions = [], [], []
            for item in scope:
                proposal = self.store.get_proposal(item["proposal_id"])
                if self._scope(proposal, refresh=True) != item:
                    raise EditError("Proposal version changed.")
                if proposal.status == ProposalStatus.PROPOSED:
                    selections.append(
                        DecisionSelection(proposal.id, proposal.fingerprint, decision, until)
                    )
                    prepared.append(
                        {
                            key: item[key]
                            for key in (
                                "proposal_id",
                                "fingerprint",
                                "target",
                                "page_id",
                                "changes",
                            )
                        }
                        | {
                            "decision": decision.value,
                            "defer_until": until,
                            "execution_is_separate": True,
                        }
                    )
                elif not (
                    action == "approve_execute"
                    and proposal.status == ProposalStatus.APPROVED
                    and proposal.decision_fingerprint == item["fingerprint"]
                ):
                    raise EditError("Proposal is no longer pending or approved.")
                executions.append(
                    canonical(
                        {
                            key: item[key]
                            for key in (
                                "proposal_id",
                                "fingerprint",
                                "page_id",
                                "target",
                                "database_id",
                                "data_source_id",
                                "target_digest",
                                "changes",
                            )
                        }
                        | {
                            "operation": "execute_and_verify",
                            "certification": (
                                "Invalidate financial certification; never certify or pay."
                            ),
                        }
                    )
                )

            def guard():
                try:
                    self._eligible(scope, execution=action == "approve_execute")
                    return self.dispatch_guard()
                except Exception:
                    return False

            binding = _Binding(
                context.principal.subject,
                context.correlation_id,
                deadline,
                canonical({"operation": "decide", "selections": prepared}),
                frozenset(executions) if action == "approve_execute" else frozenset(),
                guard,
            )
            self.store.note_edit_event(
                "telegram_callback_authorized",
                {
                    "notification_id": row.id,
                    "owner_user_id": self.owner_user_id,
                    "action": action,
                    "scope": scope,
                    "authorized_at": now,
                    "expires_at": deadline,
                },
                context.provenance(),
            )
            with self.confirmation.bind(binding):
                if selections:
                    self.edits.decide(tuple(selections), context=context)
                if action == "approve_execute":
                    worker = self.edits.authority.issue(
                        WORKER_CHANNEL,
                        correlation_id=context.correlation_id,
                        external_session_ref=row.chat_id,
                        external_request_ref=str(callback["id"]),
                    )
                    outcomes = []
                    for item in scope:
                        # Scope and policy checked anew for EACH record, not once per batch.
                        self._eligible([item], execution=True)
                        attempt = self.edits.execute(item["proposal_id"], context=worker)
                        outcomes.append(self.completion(item["proposal_id"], attempt))
                    result = "\n".join(outcomes)
                else:
                    result = (
                        "❌ Change rejected\n\n"
                        + "\n".join(item["target"] for item in scope)
                        + "\n\nNo changes made."
                        if action == "reject"
                        else (
                            "⏰ Review postponed for 1 hour. No changes made. "
                            "A fresh review will be needed."
                        )
                    )
        except Exception:
            # Provider errors can contain secrets. Detailed states remain in runtime audit.
            result = (
                "❌ Change stopped. Review the current record and request a fresh review. "
                "Any completed or uncertain changes are reported separately."
            )
        finally:
            with self.store._connect() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE telegram_edit_reviews SET state='finished', result=? "
                    "WHERE notification_id=?",
                    (result, row.id),
                )
            if action != "approve_execute" or not any(
                self.store.list_executions(proposal_id=item["proposal_id"]) for item in scope
            ):
                # Attempts have their own durable status notifications. Do not send
                # an identical callback summary as a second completion message.
                self.outbox.enqueue_status(
                    kind="edit_review_result",
                    execution_id=row.id,
                    proposal_id=row.proposal_id,
                    fingerprint=row.fingerprint,
                    owner_user_id=self.owner_user_id,
                    chat_id=row.chat_id,
                    now=datetime.now(UTC),
                )
        return result

    def result(self, notification_id):
        with self.store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT result FROM telegram_edit_reviews WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
        return row["result"] if row else "Review outcome unavailable; no write retry."
