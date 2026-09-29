"""Explainable matter priority. Scores are rule-based, not model outputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wally.models.ops import Matter, MatterDomain, MatterStatus


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            continue
    return None


def score_matter(
    matter: Matter, *, now: datetime, preparation_hours: int = 48
) -> tuple[int, tuple[str, ...]]:
    if matter.status in {MatterStatus.RESOLVED, MatterStatus.DISMISSED}:
        return 0, ()

    score = 0
    reasons: list[str] = []
    due = parse_time(matter.due_at) or parse_time(matter.expected_by)
    if due is not None:
        if due <= now:
            score += 100
            reasons.append(f"overdue relative to {due.date().isoformat()}")
        elif due <= now + timedelta(hours=48):
            score += 80
            reasons.append("due within 48 hours")
        elif due <= now + timedelta(days=7):
            score += 50
            reasons.append("due this week")
        elif due <= now + timedelta(hours=preparation_hours):
            score += 40
            reasons.append("inside preparation window")

    if matter.domain == MatterDomain.FINANCE:
        score += 25
        reasons.append("financial/admin consequence")
        if matter.status == MatterStatus.OPEN:
            score += 55
            reasons.append("open financial matter")

    if matter.status == MatterStatus.WATCHING:
        created = parse_time(matter.created_at)
        if created is not None and now - created >= timedelta(days=7):
            score += 20
            reasons.append("waiting more than 7 days")
        expected = parse_time(matter.expected_by)
        if expected is not None and expected <= now:
            score += 40
            reasons.append("expected reply is overdue")

    return score, tuple(reasons)
