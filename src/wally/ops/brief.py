"""Build a concise operational brief from matters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from wally.models.ops import (
    BriefItem,
    BriefProposal,
    Matter,
    MatterDomain,
    MatterStatus,
    OperationalBrief,
    ProposalStatus,
)
from wally.ops.priority import parse_time
from wally.ops.store import OperationsStore
from wally.ops.text import (
    UNMATCHED_RECEIPT_CHANGE,
    UNMATCHED_RECEIPT_REASON,
    clean_calendar_description,
    clean_email_snippet,
    format_brief_datetime,
    resolve_display_tz,
)

NO_ACTION_FOOTER = "Wally has taken no action."


def _is_unmatched_receipt(matter: Matter) -> bool:
    return matter.last_change == UNMATCHED_RECEIPT_CHANGE or matter.open_reason.startswith(
        "Unmatched receipt"
    )


def _state(matter: Matter) -> str:
    """Rows persisted before v0.12.1 were stored uncleaned, so clean at render too."""
    if _is_unmatched_receipt(matter):
        return clean_email_snippet(matter.summary, receipt=True)
    if matter.source == "calendar" or matter.domain == MatterDomain.CALENDAR:
        return clean_calendar_description(matter.summary)
    return clean_email_snippet(matter.summary)


def _item(matter: Matter) -> BriefItem:
    deadline = matter.due_at or matter.expected_by
    if _is_unmatched_receipt(matter):
        why = UNMATCHED_RECEIPT_REASON
    else:
        why = matter.open_reason or matter.last_change
    if matter.priority_reasons:
        why = f"{why} ({'; '.join(matter.priority_reasons)})"
    return BriefItem(
        matter_id=matter.id,
        title=matter.title,
        state=_state(matter),
        why=why,
        deadline=deadline,
        confidence=matter.confidence,
        priority_score=matter.priority_score,
        priority_reasons=matter.priority_reasons,
    )


def generate_brief(
    store: OperationsStore,
    *,
    now: datetime | None = None,
    since: str | None = None,
) -> OperationalBrief:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    resolved_since = since
    if resolved_since is None:
        resolved_since = (current - timedelta(days=14)).isoformat()

    needs: list[BriefItem] = []
    upcoming: list[BriefItem] = []
    waiting: list[BriefItem] = []
    resolved: list[BriefItem] = []
    fyi: list[BriefItem] = []

    for matter in store.list_matters():
        if matter.status == MatterStatus.DISMISSED:
            continue
        if matter.status == MatterStatus.RESOLVED or _is_unmatched_receipt(matter):
            if _is_unmatched_receipt(matter):
                if matter.updated_at >= resolved_since:
                    fyi.append(_item(matter))
                continue
            if matter.updated_at >= resolved_since:
                resolved.append(_item(matter))
            continue
        due = parse_time(matter.due_at) or parse_time(matter.expected_by)
        if matter.status == MatterStatus.WATCHING:
            expected = parse_time(matter.expected_by)
            if expected is not None and expected <= current:
                needs.append(_item(matter))
            else:
                waiting.append(_item(matter))
            continue
        if matter.priority_score >= 80:
            needs.append(_item(matter))
            continue
        if due is not None and due <= current + timedelta(days=14):
            upcoming.append(_item(matter))
            continue
        if matter.last_change:
            fyi.append(_item(matter))

    def _rank(items: list[BriefItem]) -> tuple[BriefItem, ...]:
        return tuple(sorted(items, key=lambda item: (-item.priority_score, item.title)))

    sections = (
        _rank(needs),
        _rank(upcoming),
        _rank(waiting),
        _rank(resolved),
        _rank(fyi)[:8],
    )
    rendered_matter_ids = [item.matter_id for section in sections for item in section]
    return OperationalBrief(
        generated_at=current.isoformat(),
        needs_attention=sections[0],
        upcoming=sections[1],
        waiting=sections[2],
        recently_resolved=sections[3],
        fyi=sections[4],
        proposals=_active_proposals(store, rendered_matter_ids),
    )


def _active_proposals(
    store: OperationsStore, rendered_matter_ids: list[str]
) -> tuple[BriefProposal, ...]:
    """Project active proposals for rendered Matters only.

    A proposal whose Matter was filtered out, or trimmed by the FYI cap, is dropped
    rather than shown without the context it refers to.
    """
    by_matter: dict[str, list[BriefProposal]] = {}
    for proposal in store.list_proposals(status=ProposalStatus.PROPOSED):
        by_matter.setdefault(proposal.matter_id, []).append(
            BriefProposal(
                proposal_id=proposal.id,
                matter_id=proposal.matter_id,
                intent=proposal.intent,
                title=proposal.title,
                rationale=proposal.rationale,
                suggestion=proposal.suggestion,
                risk=proposal.risk,
                confidence=proposal.confidence,
                expires_at=proposal.expires_at,
            )
        )
    projected: list[BriefProposal] = []
    for matter_id in rendered_matter_ids:
        projected.extend(
            sorted(
                by_matter.get(matter_id, []),
                key=lambda item: (item.intent.value, item.proposal_id),
            )
        )
    return tuple(projected)


def format_brief(brief: OperationalBrief, *, display_tz: tzinfo | None = None) -> str:
    tz = display_tz or resolve_display_tz(None)
    generated = format_brief_datetime(brief.generated_at, tz=tz)
    sections = [
        ("Needs attention", brief.needs_attention),
        ("Upcoming / deadlines", brief.upcoming),
        ("Waiting on others", brief.waiting),
        ("Recently resolved", brief.recently_resolved),
        ("FYI", brief.fyi),
    ]
    suggestions: dict[str, list[str]] = {}
    for proposal in brief.proposals:
        suggestions.setdefault(proposal.matter_id, []).append(proposal.suggestion)
    lines = [f"Wally brief — {generated}", ""]
    empty = True
    for heading, items in sections:
        if not items:
            continue
        empty = False
        lines.append(heading)
        for item in items:
            deadline = ""
            if item.deadline:
                deadline = f" (by {format_brief_datetime(item.deadline, tz=tz)})"
            lines.append(f"- {item.title}{deadline}")
            if item.state:
                lines.append(f"  {item.state}")
            lines.append(f"  Why: {item.why}")
            if item.confidence < 0.8:
                lines.append(f"  Confidence: {item.confidence:.1f}")
            for suggestion in suggestions.get(item.matter_id, ()):
                lines.append(f"  ↳ Suggested: {suggestion}")
        lines.append("")
    if empty:
        lines.append("Nothing needs attention.")
    if brief.proposals:
        lines.append(NO_ACTION_FOOTER)
    for note in brief.notes:
        lines.append(note)
    return "\n".join(lines).strip() + "\n"
