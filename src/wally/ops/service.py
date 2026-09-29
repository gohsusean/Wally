"""Observe → reconcile → brief. Read-only against external systems."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from wally.audit.logger import AuditLogger
from wally.models.ops import Matter, Observation, OperationalBrief
from wally.ops.brief import format_brief, generate_brief
from wally.ops.observe import SourceObserver
from wally.ops.proposal_reconcile import ProposalReconciler, ProposalReconciliation
from wally.ops.reconcile import MatterReconciler
from wally.ops.store import OperationsStore
from wally.ops.text import resolve_display_tz
from wally.providers.communications import CommunicationsProvider
from wally.providers.knowledge import KnowledgeProvider

# Used when a deduplicated fingerprint has no stored observation to name.
UNRESOLVED_OBSERVATION = "unresolved-observation"


class ObserveBriefService:
    def __init__(
        self,
        store: OperationsStore,
        *,
        audit: AuditLogger | None = None,
        communications: CommunicationsProvider | None = None,
        knowledge: KnowledgeProvider | None = None,
        calendar_horizon_days: int = 14,
        preparation_hours: int = 48,
        email_lookback_days: int = 14,
        bills_role: str = "finance",
        display_timezone: str | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._communications = communications
        self._knowledge = knowledge
        self._calendar_horizon_days = calendar_horizon_days
        self._email_lookback_days = email_lookback_days
        self._bills_role = bills_role
        self._display_tz = resolve_display_tz(display_timezone)
        self._observer = SourceObserver(store)
        self._reconciler = MatterReconciler(store, preparation_hours=preparation_hours)
        self._proposals = ProposalReconciler(store)

    @property
    def store(self) -> OperationsStore:
        return self._store

    def refresh(self, *, now: datetime | None = None) -> list[Observation]:
        self._observer.skipped_fingerprints.clear()
        ingested: list[Observation] = []
        if self._communications is not None:
            ingested.extend(
                self._observer.observe_email(
                    self._communications,
                    now=now,
                    lookback_days=self._email_lookback_days,
                )
            )
            ingested.extend(
                self._observer.observe_calendar(
                    self._communications,
                    now=now,
                    horizon_days=self._calendar_horizon_days,
                )
            )
        if self._knowledge is not None:
            ingested.extend(
                self._observer.observe_knowledge(
                    self._knowledge,
                    now=now,
                    bills_role=self._bills_role,
                )
            )
        for fingerprint in self._observer.skipped_fingerprints:
            # Fingerprints embed source text (a calendar fingerprint carries the event
            # summary), so the audit records the opaque id of the stored observation.
            existing = self._store.get_observation_by_fingerprint(fingerprint)
            if existing is None:
                self._log("observation_deduplicated", UNRESOLVED_OBSERVATION, "ops")
            else:
                self._log("observation_deduplicated", existing.id, existing.source)
        for observation in ingested:
            self._log("observation_ingested", observation.id, observation.source)
            previous = self._store.get_matter_by_fingerprint(
                self._reconciler.fingerprint_for(observation)
            )
            matter = self._reconciler.apply(observation, now=now)
            if matter is None:
                continue
            self._audit_matter(previous, matter)
        self._reconciler.refresh_priorities(now=now)
        return ingested

    def brief(
        self,
        *,
        refresh: bool = True,
        now: datetime | None = None,
        since: str | None = None,
    ) -> OperationalBrief:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if refresh:
            self.refresh(now=current)
        # Runs on every brief, including --no-refresh, so stored proposals still
        # expire and resolved matters still withdraw their suggestions.
        self._audit_proposals(self._proposals.reconcile(now=current))
        result = generate_brief(self._store, now=current, since=since)
        self._log("brief_generated", result.generated_at, "ops")
        return result

    def render(
        self,
        *,
        refresh: bool = True,
        now: datetime | None = None,
        since: str | None = None,
        as_json: bool = False,
    ) -> str:
        result = self.brief(refresh=refresh, now=now, since=since)
        if as_json:
            import json

            return json.dumps(asdict(result), indent=2)
        return format_brief(result, display_tz=self._display_tz)

    def _audit_matter(self, previous: Matter | None, matter: Matter) -> None:
        if previous is None:
            self._log("matter_created", matter.id, matter.domain.value)
            return
        if previous.status != matter.status and matter.status.value == "resolved":
            self._log("matter_resolved", matter.id, matter.resolution_evidence)
        elif previous.status != matter.status and previous.status.value == "resolved":
            self._log("matter_reopened", matter.id, matter.status.value)
        else:
            self._log("matter_updated", matter.id, matter.last_change)
        if abs(previous.priority_score - matter.priority_score) >= 20:
            self._log("priority_changed", matter.id, str(matter.priority_score))

    def _audit_proposals(self, outcome: ProposalReconciliation) -> None:
        """Log real lifecycle transitions only, by proposal id.

        Unchanged proposals are silent, so a repeated brief adds no audit noise, and
        no suggestion prose, matter text, or reference identifier is recorded.
        """
        for event_type, proposal_ids in (
            ("proposal_created", outcome.created),
            ("proposal_superseded", outcome.superseded),
            ("proposal_invalidated", outcome.invalidated),
            ("proposal_expired", outcome.expired),
        ):
            for proposal_id in proposal_ids:
                self._log(event_type, proposal_id, "proposals")

    def _log(self, event_type: str, subject: str, detail: str) -> None:
        if self._audit is None:
            return
        self._audit.log_simple(
            event_type=event_type,
            session_id="ops",
            outcome="success",
            provider="ops",
            parameters={"subject": subject, "detail": detail[:300]},
        )
