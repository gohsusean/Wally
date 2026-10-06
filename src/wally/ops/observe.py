"""Incremental observe pass over existing read-only providers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from wally.models.ops import Observation, ObservationCategory
from wally.ops.classify import classify_email, looks_like_injection
from wally.ops.store import OperationsStore
from wally.ops.text import (
    clean_calendar_description,
    clean_email_snippet,
    clean_title,
    extract_currency_amount,
)
from wally.providers.communications import CommunicationsProvider
from wally.runtime.authority import InformationAuthority

EXTERNAL = InformationAuthority.EXTERNAL_COMMUNICATIONS.value
KNOWLEDGE = InformationAuthority.KNOWLEDGE_ASSET.value


def _now(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class SourceObserver:
    def __init__(self, store: OperationsStore) -> None:
        self._store = store
        self.skipped_fingerprints: list[str] = []

    def observe_email(
        self,
        communications: CommunicationsProvider,
        *,
        now: datetime | None = None,
        lookback_days: int = 14,
        limit: int = 50,
    ) -> list[Observation]:
        current = _now(now)
        after = (current - timedelta(days=lookback_days)).strftime("%Y/%m/%d")
        summaries = communications.search_email(query=f"after:{after}", limit=limit)
        ingested: list[Observation] = []
        checkpoint = self._store.get_checkpoint("gmail")
        seen = set(checkpoint.get("seen_ids", []))
        for summary in summaries:
            fingerprint = f"gmail:{summary.message_id}"
            if self._store.get_observation_by_fingerprint(fingerprint) is not None:
                self.skipped_fingerprints.append(fingerprint)
                continue
            if summary.message_id in seen:
                continue
            text = f"{summary.subject}\n{summary.snippet}"
            category = classify_email(
                subject=summary.subject,
                snippet=summary.snippet,
                labels=summary.labels,
            )
            extra = {}
            if looks_like_injection(text):
                extra["injection_suspected"] = "true"
            if category == ObservationCategory.INVOICE:
                amount = extract_currency_amount(text)
                if amount:
                    extra["amount"] = amount
            observation = Observation(
                id=str(uuid4()),
                fingerprint=fingerprint,
                source="gmail",
                source_id=summary.message_id,
                observed_at=_iso(current),
                source_timestamp=summary.date,
                category=category,
                title=clean_title(summary.subject),
                summary=clean_email_snippet(
                    summary.snippet,
                    receipt=category == ObservationCategory.RECEIPT,
                ),
                trusted=False,
                authority=EXTERNAL,
                confidence=0.9
                if category
                in {
                    ObservationCategory.INVOICE,
                    ObservationCategory.RECEIPT,
                }
                else 0.7,
                thread_id=summary.thread_id,
                extra=extra,
            )
            self._store.save_observation(observation)
            ingested.append(observation)
            seen.add(summary.message_id)
        self._store.set_checkpoint("gmail", {"seen_ids": list(seen)[-500:]})
        return ingested

    def observe_calendar(
        self,
        communications: CommunicationsProvider,
        *,
        now: datetime | None = None,
        horizon_days: int = 14,
    ) -> list[Observation]:
        current = _now(now)
        start = current.isoformat()
        end = (current + timedelta(days=horizon_days)).isoformat()
        events = communications.list_calendar_events(start=start, end=end)
        ingested: list[Observation] = []
        checkpoint = self._store.get_checkpoint("calendar")
        snapshots: dict[str, str] = dict(checkpoint.get("snapshots", {}))
        current_ids = set()
        for event in events:
            current_ids.add(event.event_id)
            snapshot = f"{event.start}|{event.summary}"
            fingerprint = f"calendar:{event.event_id}:{snapshot}"
            previous = snapshots.get(event.event_id)
            if previous == snapshot and self._store.get_observation_by_fingerprint(
                f"calendar:{event.event_id}:{previous}"
            ):
                continue
            if self._store.get_observation_by_fingerprint(fingerprint) is not None:
                snapshots[event.event_id] = snapshot
                continue
            category = (
                ObservationCategory.CALENDAR_CHANGED
                if previous and previous != snapshot
                else ObservationCategory.CALENDAR_UPCOMING
            )
            description = clean_calendar_description(event.description)
            extra = {"start": event.start, "end": event.end}
            if looks_like_injection(event.description or event.summary):
                extra["injection_suspected"] = "true"
            observation = Observation(
                id=str(uuid4()),
                fingerprint=fingerprint,
                source="calendar",
                source_id=event.event_id,
                observed_at=_iso(current),
                source_timestamp=event.start,
                category=category,
                title=clean_title(event.summary),
                summary=description or clean_title(event.summary),
                trusted=False,
                authority=EXTERNAL,
                confidence=1.0,
                extra=extra,
            )
            self._store.save_observation(observation)
            ingested.append(observation)
            snapshots[event.event_id] = snapshot
        for event_id, snapshot in list(snapshots.items()):
            if event_id in current_ids:
                continue
            fingerprint = f"calendar-cancelled:{event_id}:{snapshot}"
            if self._store.get_observation_by_fingerprint(fingerprint) is None:
                observation = Observation(
                    id=str(uuid4()),
                    fingerprint=fingerprint,
                    source="calendar",
                    source_id=event_id,
                    observed_at=_iso(current),
                    source_timestamp=_iso(current),
                    category=ObservationCategory.CALENDAR_CANCELLED,
                    title="Calendar event no longer in horizon",
                    summary="Previously observed event is missing from the upcoming window.",
                    trusted=False,
                    authority=EXTERNAL,
                    confidence=0.8,
                )
                self._store.save_observation(observation)
                ingested.append(observation)
            snapshots.pop(event_id, None)
        self._store.set_checkpoint("calendar", {"snapshots": snapshots})
        return ingested

    def observe_knowledge(self, knowledge, *, now=None, bills_role="finance") -> list[Observation]:
        """Generic knowledge metadata cannot establish canonical financial facts."""
        return []

    def observe_finance(self, catalog, *, now=None) -> list[Observation]:
        current = _now(now)
        ingested = []
        for record, binding in catalog.tracked_instances():
            facts = record.candidate.facts
            fingerprint = f"finance-instance:{record.id}:{record.version}:{binding}"
            if self._store.get_observation_by_fingerprint(fingerprint):
                continue
            observation = Observation(
                id=str(uuid4()),
                fingerprint=fingerprint,
                source="finance",
                source_id=record.id,
                observed_at=_iso(current),
                source_timestamp=facts.due_date,
                category=ObservationCategory.KNOWLEDGE_OBLIGATION,
                title=f"{facts.stage.capitalize()} obligation {record.id[-8:]}",
                summary=f"Owner-certified {facts.stage} obligation occurrence; review only.",
                trusted=True,
                authority=KNOWLEDGE,
                confidence=1.0,
                related_knowledge_id=facts.definition,
                extra={
                    "finance_binding": binding,
                    "stage": facts.stage,
                    "amount": facts.amount,
                    "currency": facts.currency,
                },
            )
            self._store.save_observation(observation)
            ingested.append(observation)
        return ingested
