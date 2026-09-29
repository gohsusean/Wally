"""Deterministic matter reconciliation from observations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wally.models.ops import (
    Matter,
    MatterDomain,
    MatterStatus,
    Observation,
    ObservationCategory,
)
from wally.ops.priority import score_matter
from wally.ops.store import OperationsStore
from wally.ops.text import UNMATCHED_RECEIPT_CHANGE, UNMATCHED_RECEIPT_REASON

_DOMAIN = {
    ObservationCategory.INVOICE: MatterDomain.FINANCE,
    ObservationCategory.RECEIPT: MatterDomain.FINANCE,
    ObservationCategory.KNOWLEDGE_OBLIGATION: MatterDomain.FINANCE,
    ObservationCategory.CALENDAR_UPCOMING: MatterDomain.CALENDAR,
    ObservationCategory.CALENDAR_CHANGED: MatterDomain.CALENDAR,
    ObservationCategory.CALENDAR_CANCELLED: MatterDomain.CALENDAR,
    ObservationCategory.EMAIL_SENT: MatterDomain.COMMUNICATIONS,
    ObservationCategory.EMAIL_REPLY: MatterDomain.COMMUNICATIONS,
    ObservationCategory.EMAIL_RECEIVED: MatterDomain.COMMUNICATIONS,
}


def _now(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _link(matter: Matter, observation: Observation) -> Matter:
    ids = matter.observation_ids
    if observation.id not in ids:
        ids = (*ids, observation.id)
    knowledge = matter.knowledge_ids
    if observation.related_knowledge_id and observation.related_knowledge_id not in knowledge:
        knowledge = (*knowledge, observation.related_knowledge_id)
    matter.observation_ids = ids
    matter.knowledge_ids = knowledge
    return matter


class MatterReconciler:
    def __init__(self, store: OperationsStore, *, preparation_hours: int = 48) -> None:
        self._store = store
        self._preparation_hours = preparation_hours

    def fingerprint_for(self, observation: Observation) -> str:
        return self._matter_fingerprint(observation)

    def apply(self, observation: Observation, *, now: datetime | None = None) -> Matter | None:
        current = _now(now)
        if (
            observation.extra.get("injection_suspected") == "true"
            and observation.category == ObservationCategory.RECEIPT
        ):
            observation = replace(observation, category=ObservationCategory.EMAIL_RECEIVED)
        handler = {
            ObservationCategory.INVOICE: self._open_or_update_invoice,
            ObservationCategory.RECEIPT: self._resolve_receipt,
            ObservationCategory.EMAIL_SENT: self._open_waiting,
            ObservationCategory.EMAIL_REPLY: self._apply_reply,
            ObservationCategory.EMAIL_RECEIVED: self._apply_generic_email,
            ObservationCategory.CALENDAR_UPCOMING: self._upsert_calendar,
            ObservationCategory.CALENDAR_CHANGED: self._upsert_calendar,
            ObservationCategory.CALENDAR_CANCELLED: self._cancel_calendar,
            ObservationCategory.KNOWLEDGE_OBLIGATION: self._open_obligation,
        }.get(observation.category)
        if handler is None:
            return None
        matter = handler(observation, current)
        if matter is None:
            return None
        return self._persist_priority(matter, current)

    def refresh_priorities(self, *, now: datetime | None = None) -> list[Matter]:
        current = _now(now)
        updated: list[Matter] = []
        for matter in self._store.list_matters():
            persisted = self._persist_priority(matter, current)
            updated.append(persisted)
        return updated

    def _persist_priority(self, matter: Matter, current: datetime) -> Matter:
        previous = matter.priority_score
        score, reasons = score_matter(
            matter, now=current, preparation_hours=self._preparation_hours
        )
        matter.priority_score = score
        matter.priority_reasons = reasons
        if abs(score - previous) >= 20:
            reasons_text = ", ".join(reasons) or "no signals"
            matter.last_change = f"Priority {previous} → {score}: {reasons_text}"
            matter.updated_at = _iso(current)
        self._store.save_matter(matter)
        return matter

    def _match_existing(self, observation: Observation) -> Matter | None:
        if observation.thread_id:
            open_matters = [
                matter
                for matter in self._store.find_matters_by_thread(observation.thread_id)
                if matter.status not in {MatterStatus.RESOLVED, MatterStatus.DISMISSED}
            ]
            if open_matters:
                return open_matters[0]
        fingerprint = self._matter_fingerprint(observation)
        return self._store.get_matter_by_fingerprint(fingerprint)

    def _matter_fingerprint(self, observation: Observation) -> str:
        if observation.source == "calendar":
            return f"matter:event:{observation.source_id}"
        if observation.category == ObservationCategory.KNOWLEDGE_OBLIGATION:
            period = observation.extra.get("period", "")
            return f"matter:obligation:{observation.source_id}:{period}"
        if observation.thread_id:
            return f"matter:thread:{observation.thread_id}"
        return f"matter:source:{observation.source}:{observation.source_id}"

    def _new_matter(self, observation: Observation, current: datetime, **overrides) -> Matter:
        matter = Matter(
            id=str(uuid4()),
            fingerprint=self._matter_fingerprint(observation),
            title=observation.title,
            domain=_DOMAIN.get(observation.category, MatterDomain.OTHER),
            status=overrides.get("status", MatterStatus.OPEN),
            created_at=_iso(current),
            updated_at=_iso(current),
            summary=observation.summary,
            open_reason=overrides.get("open_reason", observation.summary),
            last_change=overrides.get("last_change", f"Opened from {observation.category.value}"),
            due_at=overrides.get("due_at", observation.extra.get("start", "")),
            expected_by=overrides.get("expected_by", ""),
            observation_ids=(observation.id,),
            knowledge_ids=(observation.related_knowledge_id,)
            if observation.related_knowledge_id
            else (),
            thread_id=observation.thread_id,
            source=observation.source,
            confidence=observation.confidence,
            recurrence_key=overrides.get("recurrence_key", ""),
        )
        return matter

    def _touch(
        self,
        matter: Matter,
        observation: Observation,
        current: datetime,
        *,
        last_change: str,
    ) -> Matter:
        _link(matter, observation)
        matter.updated_at = _iso(current)
        matter.last_change = last_change
        matter.summary = observation.summary or matter.summary
        self._store.save_matter(matter)
        return matter

    def _open_or_update_invoice(self, observation: Observation, current: datetime) -> Matter:
        existing = self._match_existing(observation)
        if existing and existing.status != MatterStatus.RESOLVED:
            existing.status = MatterStatus.OPEN
            existing.open_reason = "Invoice or bill still unpaid"
            return self._touch(
                existing, observation, current, last_change="Invoice/bill observation attached"
            )
        if existing and existing.status == MatterStatus.RESOLVED:
            existing.status = MatterStatus.OPEN
            existing.resolution_evidence = ""
            existing.open_reason = "New invoice after prior resolution"
            return self._touch(
                existing, observation, current, last_change="Reopened on new invoice"
            )
        matter = self._new_matter(
            observation,
            current,
            open_reason="Invoice or bill requires attention",
            last_change="Opened from invoice observation",
        )
        self._store.save_matter(matter)
        return matter

    def _resolve_receipt(self, observation: Observation, current: datetime) -> Matter:
        existing = self._match_existing(observation)
        if existing is None:
            knowledge_open = [
                matter
                for matter in self._store.list_matters()
                if matter.status not in {MatterStatus.RESOLVED, MatterStatus.DISMISSED}
                and matter.source == "knowledge"
                and matter.domain == MatterDomain.FINANCE
            ]
            if len(knowledge_open) == 1:
                existing = knowledge_open[0]
        if existing is None:
            # Receipt without a matching open matter is FYI, not a resolution.
            matter = self._new_matter(
                observation,
                current,
                open_reason=UNMATCHED_RECEIPT_REASON,
                last_change=UNMATCHED_RECEIPT_CHANGE,
            )
            matter.domain = MatterDomain.OTHER
            matter.status = MatterStatus.OPEN
            matter.open_reason = UNMATCHED_RECEIPT_REASON
            self._store.save_matter(matter)
            return matter
        existing.status = MatterStatus.RESOLVED
        existing.resolution_evidence = f"observation:{observation.id}"
        existing.open_reason = ""
        return self._touch(
            existing,
            observation,
            current,
            last_change="Resolved from payment receipt evidence",
        )

    def _open_waiting(self, observation: Observation, current: datetime) -> Matter:
        existing = self._match_existing(observation)
        expected = _iso(current + timedelta(days=7))
        if existing and existing.status not in {MatterStatus.RESOLVED, MatterStatus.DISMISSED}:
            existing.status = MatterStatus.WATCHING
            existing.expected_by = existing.expected_by or expected
            return self._touch(
                existing, observation, current, last_change="Waiting for reply"
            )
        matter = self._new_matter(
            observation,
            current,
            expected_by=expected,
            last_change="Opened as waiting for reply",
        )
        matter.status = MatterStatus.WATCHING
        matter.open_reason = "Waiting for a reply"
        self._store.save_matter(matter)
        return matter

    def _apply_reply(self, observation: Observation, current: datetime) -> Matter:
        existing = self._match_existing(observation)
        if existing is None:
            return self._apply_generic_email(observation, current)
        if existing.status == MatterStatus.WATCHING:
            existing.status = MatterStatus.RESOLVED
            existing.resolution_evidence = f"observation:{observation.id}"
            existing.open_reason = ""
            return self._touch(existing, observation, current, last_change="Reply received")
        return self._touch(existing, observation, current, last_change="Thread updated")

    def _apply_generic_email(self, observation: Observation, current: datetime) -> Matter | None:
        existing = self._match_existing(observation)
        if existing:
            return self._touch(existing, observation, current, last_change="Related email noted")
        return None

    def _upsert_calendar(self, observation: Observation, current: datetime) -> Matter:
        fingerprint = f"matter:event:{observation.source_id}"
        existing = self._store.get_matter_by_fingerprint(fingerprint)
        due = observation.extra.get("start", "")
        if existing:
            existing.status = MatterStatus.OPEN
            existing.due_at = due or existing.due_at
            existing.title = observation.title or existing.title
            change = (
                "Calendar event updated"
                if observation.category == ObservationCategory.CALENDAR_CHANGED
                else "Upcoming calendar event"
            )
            return self._touch(existing, observation, current, last_change=change)
        matter = self._new_matter(
            observation,
            current,
            due_at=due,
            last_change="Opened from upcoming calendar event",
        )
        matter.open_reason = "Upcoming calendar commitment"
        self._store.save_matter(matter)
        return matter

    def _cancel_calendar(self, observation: Observation, current: datetime) -> Matter | None:
        existing = self._store.get_matter_by_fingerprint(f"matter:event:{observation.source_id}")
        if existing is None:
            return None
        existing.status = MatterStatus.RESOLVED
        existing.resolution_evidence = f"observation:{observation.id}"
        existing.open_reason = ""
        return self._touch(
            existing, observation, current, last_change="Event left the upcoming window"
        )

    def _open_obligation(self, observation: Observation, current: datetime) -> Matter:
        existing = self._store.get_matter_by_fingerprint(self._matter_fingerprint(observation))
        due = observation.source_timestamp
        if existing and existing.status != MatterStatus.RESOLVED:
            return self._touch(
                existing, observation, current, last_change="Recurring obligation still open"
            )
        matter = self._new_matter(
            observation,
            current,
            due_at=due if due else "",
            recurrence_key=observation.extra.get("period", ""),
            last_change="Opened from trusted Knowledge obligation",
        )
        matter.open_reason = "Recurring obligation from Knowledge"
        self._store.save_matter(matter)
        return matter
