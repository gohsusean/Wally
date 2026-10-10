"""Approval Inbox: a read-only view over durable proposal decisions.

Pending items come first. Approved items show whether an executor exists and the
latest execution attempt. Nothing is executable from this view; execution needs a
separate ``wally execute`` request.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from wally.models.ops import ProposalIntent, ProposalStatus, ProposedAction
from wally.ops.execution import UNSUPPORTED_EXECUTION_MESSAGE, execution_supported
from wally.ops.priority import parse_time
from wally.ops.store import OperationsStore
from wally.ops.text import format_brief_datetime, resolve_display_tz

RECENT_DECISION_DAYS = 14


@dataclass(frozen=True)
class InboxItem:
    proposal_id: str
    title: str
    matter_title: str
    rationale: str
    suggestion: str
    deadline: str
    status: ProposalStatus
    intent: ProposalIntent
    defer_until: str = ""
    decided_at: str = ""
    execution: str = ""


@dataclass(frozen=True)
class ApprovalInbox:
    generated_at: str
    pending: tuple[InboxItem, ...] = ()
    approved: tuple[InboxItem, ...] = ()
    deferred: tuple[InboxItem, ...] = ()
    recently_rejected: tuple[InboxItem, ...] = ()


def _deadline(proposal: ProposedAction, matter_due: str) -> str:
    return matter_due or proposal.expires_at or proposal.defer_until


def _item(proposal: ProposedAction, *, matter_title: str, matter_due: str) -> InboxItem:
    headline = matter_title or proposal.title
    return InboxItem(
        proposal_id=proposal.id,
        title=proposal.title,
        matter_title=headline,
        rationale=proposal.rationale,
        suggestion=proposal.suggestion,
        deadline=_deadline(proposal, matter_due),
        status=proposal.status,
        intent=proposal.intent,
        defer_until=proposal.defer_until,
        decided_at=proposal.decided_at,
    )


def _sort_key(item: InboxItem) -> tuple:
    due = parse_time(item.deadline)
    due_rank = due.timestamp() if due is not None else float("inf")
    return (due_rank, item.matter_title, item.proposal_id)


def build_inbox(
    store: OperationsStore,
    *,
    now: datetime,
    recent_days: int = RECENT_DECISION_DAYS,
) -> ApprovalInbox:
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    cutoff = (current - timedelta(days=recent_days)).isoformat()
    matters = {matter.id: matter for matter in store.list_matters()}
    pending: list[InboxItem] = []
    approved: list[InboxItem] = []
    deferred: list[InboxItem] = []
    rejected: list[InboxItem] = []
    for proposal in store.list_proposals():
        matter = matters.get(proposal.matter_id)
        item = _item(
            proposal,
            matter_title=matter.title if matter is not None else "",
            matter_due=matter.due_at if matter is not None else "",
        )
        if proposal.status == ProposalStatus.PROPOSED:
            pending.append(item)
        elif proposal.status == ProposalStatus.APPROVED:
            approved.append(replace(item, execution=_execution_note(store, proposal)))
        elif proposal.status == ProposalStatus.DEFERRED:
            deferred.append(item)
        elif proposal.status == ProposalStatus.REJECTED and proposal.updated_at >= cutoff:
            rejected.append(item)
    return ApprovalInbox(
        generated_at=current.isoformat(),
        pending=tuple(sorted(pending, key=_sort_key)),
        approved=tuple(sorted(approved, key=_sort_key)),
        deferred=tuple(sorted(deferred, key=_sort_key)),
        recently_rejected=tuple(sorted(rejected, key=_sort_key)),
    )


def _execution_note(store: OperationsStore, proposal: ProposedAction) -> str:
    if proposal.intent == ProposalIntent.EDIT_NOTION_RECORD:
        return "Scoped Notion edit: separate execution and fresh owner confirmation are required."
    if not execution_supported(proposal):
        return UNSUPPORTED_EXECUTION_MESSAGE
    attempts = [
        item
        for item in store.list_executions(proposal_id=proposal.id)
        if item.proposal_fingerprint == proposal.fingerprint
    ]
    if not attempts:
        return f"Ready. Run `wally execute {proposal.id}` to act; you will be asked again."
    latest = attempts[0]
    return f"Last attempt {latest.id}: {latest.status.value}"


def _render_item(index: int, item: InboxItem, *, tz, extra: tuple[str, ...] = ()) -> list[str]:
    lines = [f"{index}. {item.matter_title}"]
    if item.deadline:
        lines.append(f"   Due: {format_brief_datetime(item.deadline, tz=tz)}")
    lines.append(f"   Why: {item.rationale}")
    lines.append(f"   Proposed: {item.suggestion}")
    lines.extend(extra)
    lines.append(f"   ID: {item.proposal_id}")
    return lines


def format_inbox(inbox: ApprovalInbox, *, display_tz=None) -> str:
    tz = display_tz or resolve_display_tz(None)
    generated = format_brief_datetime(inbox.generated_at, tz=tz)
    lines = [f"Approval Inbox — {generated}", ""]
    if inbox.pending:
        lines.append("Decisions waiting for you")
        for index, item in enumerate(inbox.pending, start=1):
            lines.extend(_render_item(index, item, tz=tz))
        lines.append("")
    else:
        lines.append("Nothing is waiting for a decision.")
        lines.append("")
    if inbox.approved:
        lines.append("Approved")
        for index, item in enumerate(inbox.approved, start=1):
            lines.extend(
                _render_item(
                    index,
                    item,
                    tz=tz,
                    extra=(f"   Execution: {item.execution}",),
                )
            )
        lines.append("")
    if inbox.deferred:
        lines.append("Deferred")
        for index, item in enumerate(inbox.deferred, start=1):
            until = ""
            if item.defer_until:
                until = f"   Until: {format_brief_datetime(item.defer_until, tz=tz)}"
            extra = (until,) if until else ()
            lines.extend(_render_item(index, item, tz=tz, extra=extra))
        lines.append("")
    if inbox.recently_rejected:
        lines.append("Recently rejected")
        for index, item in enumerate(inbox.recently_rejected, start=1):
            lines.extend(_render_item(index, item, tz=tz, extra=("   Decision: rejected",)))
        lines.append("")
    return "\n".join(lines).strip() + "\n"
