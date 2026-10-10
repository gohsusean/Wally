"""Exact, inert Notion edit intentions and centrally authorized execution.

Opt-in service. Normal App/remote bootstrap does not install it or grant execution.
The operational proposal remains reference-only; immutable edit specifications
live separately and are translated to provider arguments only after confirmation.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Protocol
from urllib.parse import unquote
from uuid import UUID, uuid4

from wally.exceptions import WallyError
from wally.models.ops import (
    ExecutionStatus,
    Matter,
    MatterDomain,
    MatterStatus,
    ProposalExecution,
    ProposalIntent,
    ProposalProvenance,
    ProposalRisk,
    ProposalStatus,
    ProposedAction,
)
from wally.models.principal import Capability, RequestContext
from wally.ops.decisions import UserDecision, parse_defer_until
from wally.ops.execution import approval_problem
from wally.ops.store import OperationsStore
from wally.runtime.confirmation import digest
from wally.runtime.principals import PrincipalAuthority


class EditError(WallyError):
    pass


@dataclass(frozen=True)
class PropertyRule:
    field: str
    property_id: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class EditTarget:
    """Installed by reviewed runtime configuration, never by an ingress request."""

    key: str
    database_id: str
    data_source_id: str
    page_id: str
    properties: tuple[PropertyRule, ...]
    audit_property_ids: tuple[str, ...] = ()
    finance_id: str = ""

    def __post_init__(self):
        # All aliases for a Notion UUID must share the same durable record lock.
        for name in ("database_id", "data_source_id", "page_id"):
            object.__setattr__(self, name, str(UUID(getattr(self, name))))
        object.__setattr__(
            self,
            "properties",
            tuple(replace(rule, property_id=unquote(rule.property_id)) for rule in self.properties),
        )
        object.__setattr__(
            self, "audit_property_ids", tuple(unquote(key) for key in self.audit_property_ids)
        )

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))


@dataclass(frozen=True)
class RecordSnapshot:
    """Only enum values and hashes of protected business properties persist."""

    values: dict[str, str]
    hashes: dict[str, str]
    schema_digest: str
    finance_version: str = ""


class EditBackend(Protocol):
    def read(self, target: EditTarget) -> RecordSnapshot:
        """Check live classification, designation, target and complete schema/state."""

    def invalidate_certification(self, target: EditTarget) -> None:
        """Durably invalidate financial authority before a potentially material write."""

    def write(
        self,
        target: EditTarget,
        replacements: dict[str, str],
        expected: RecordSnapshot,
        revalidate: Callable[[], None],
    ) -> None:
        """Resolve action credentials here; patch only supplied, allowed properties."""


@dataclass(frozen=True)
class DecisionSelection:
    proposal_id: str
    fingerprint: str
    decision: UserDecision
    defer_until: str = ""


def _audited_request(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except BaseException as exc:
            context = kwargs.get("context")
            from wally.models.principal import RequestProvenance

            provenance = RequestProvenance()
            if self.authority.check(context, Capability.READ_CONTEXT) is None:
                provenance = context.provenance()
            self.store.note_edit_event(
                "edit_request_failed",
                {
                    "operation": function.__name__,
                    "category": type(exc).__name__,
                },
                provenance,
            )
            raise

    return call


class NotionEditService:
    def __init__(
        self,
        store: OperationsStore,
        authority: PrincipalAuthority,
        backend: EditBackend,
        targets: tuple[EditTarget, ...] = (),
        *,
        writes_enabled: bool = False,
        targets_provider: Callable[[], tuple[EditTarget, ...]] | None = None,
        write_gate: Callable[[], bool] | None = None,
    ):
        self.store = store
        self.authority = authority
        self.backend = backend
        self.targets = {t.key: t for t in targets}
        if len(self.targets) != len(targets):
            raise EditError("Ambiguous target registration.")
        self.writes_enabled = writes_enabled
        self._targets_provider = targets_provider
        self._write_gate = write_gate

    @_audited_request
    def propose(
        self,
        target_key: str,
        replacements: dict[str, str],
        *,
        context: RequestContext,
    ) -> ProposedAction:
        self.authority.authorize(context, Capability.SUBMIT_REQUEST)
        target = self._target(target_key)
        # Only finite, reviewed enum values are supported. No arbitrary text, amounts,
        # credentials, URLs, relations, classification authority or payment status.
        self._validate(target, replacements)
        before = self.backend.read(target)
        changes = [
            {"property_id": key, "before": before.values[key], "after": value}
            for key, value in sorted(replacements.items())
            if before.values.get(key) != value
        ]
        if not changes:
            raise EditError("No material edit requested.")
        spec = {
            "target": target.key,
            "target_digest": target.fingerprint,
            "page_id": target.page_id,
            "data_source_id": target.data_source_id,
            "before": asdict(before),
            "changes": changes,
        }
        material_digest = digest(spec)
        prior = self.store.latest_notion_edit(material_digest)
        if prior is not None and prior.status not in {
            ProposalStatus.EXPIRED,
            ProposalStatus.INVALIDATED,
        }:
            return prior  # Rejection/supersession is never resurrected by a replay.
        prior_spec = self.store.get_notion_edit(prior.id) if prior else None
        spec["material_digest"] = material_digest
        spec["review_revision"] = prior_spec["review_revision"] + 1 if prior_spec else 1
        fingerprint = digest(spec)
        now = datetime.now(UTC).isoformat()
        matter_id = "edit_" + digest(target.page_id)[:24]
        matter = self.store.get_matter(matter_id)
        if matter is None:
            self.store.save_matter(
                Matter(
                    id=matter_id,
                    fingerprint=matter_id,
                    title="Notion metadata review",
                    domain=MatterDomain.ADMIN,
                    status=MatterStatus.OPEN,
                    created_at=now,
                    updated_at=now,
                    summary="Review scoped metadata corrections.",
                    open_reason="Requested record correction",
                    last_change="Edit proposed",
                    knowledge_ids=(target.key,),
                    source="notion_edit",
                )
            )
        elif matter.status != MatterStatus.OPEN:
            raise EditError("Edit Matter is no longer open.")
        proposal = ProposedAction(
            id="pa_" + uuid4().hex[:16],
            fingerprint=fingerprint,
            matter_id=matter_id,
            intent=ProposalIntent.EDIT_NOTION_RECORD,
            status=ProposalStatus.PROPOSED,
            provenance=ProposalProvenance.DETERMINISTIC_RULES,
            risk=ProposalRisk.MEDIUM,
            created_at=now,
            updated_at=now,
            title="Review Notion metadata edit",
            rationale="Owner-requested record correction; certification is separate.",
            suggestion="Review the exact edit with get_notion_edit before deciding.",
            knowledge_ids=(target.key,),
            request_provenance=context.provenance(),
            expires_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
        self.store.insert_notion_edit(proposal, spec)
        return proposal

    def review(self, proposal_id: str, *, context: RequestContext) -> dict:
        self.authority.authorize(context, Capability.READ_CONTEXT)
        proposal, spec, _ = self._load(proposal_id)
        return {
            "proposal_id": proposal.id,
            "fingerprint": proposal.fingerprint,
            "status": proposal.status.value,
            "expires_at": proposal.expires_at,
            "target": spec["target"],
            "page_id": spec["page_id"],
            "changes": spec["changes"],
            "execution_is_separate": True,
        }

    @_audited_request
    def decide(
        self,
        selections: tuple[DecisionSelection, ...],
        *,
        context: RequestContext,
    ) -> list[ProposedAction]:
        self.authority.authorize(context, Capability.DECIDE_PROPOSAL)
        if not 1 <= len(selections) <= 20 or len({s.proposal_id for s in selections}) != len(
            selections
        ):
            raise EditError("Select 1–20 distinct exact proposal versions.")
        prepared = self._decisions(selections)
        presentation = {"operation": "decide", "selections": prepared}
        self.store.note_edit_event("decision_presented", presentation, context.provenance())
        confirmation = self.authority.confirm_human(
            context,
            Capability.DECIDE_PROPOSAL,
            presentation=presentation,
        )
        # Reads happen after the human interaction, with a fresh clock. No caller
        # fingerprint, old snapshot, model assertion or presentation can replace them.
        if prepared != self._decisions(selections):
            raise EditError("Review changed during confirmation.")
        if time.time() >= confirmation.expires_at:
            raise EditError("Confirmation expired during revalidation.")
        self.authority.consume_confirmation(
            confirmation,
            context,
            Capability.DECIDE_PROPOSAL,
            presentation=presentation,
        )
        self.store.record_edit_decisions(selections, confirmation, presentation)
        return [self.store.get_proposal(s.proposal_id) for s in selections]

    def _decisions(self, selections: tuple[DecisionSelection, ...]) -> list[dict]:
        result = []
        for selection in selections:
            proposal, spec, target = self._load(selection.proposal_id)
            if proposal.fingerprint != selection.fingerprint:
                raise EditError("Proposal version does not match the review.")
            if proposal.status != ProposalStatus.PROPOSED:
                raise EditError("Proposal is not awaiting a decision.")
            if not isinstance(selection.decision, UserDecision):
                raise EditError("Unknown decision.")
            if selection.decision == UserDecision.DEFER:
                parse_defer_until(selection.defer_until, now=datetime.now(UTC))
            elif selection.defer_until:
                raise EditError("Only defer accepts a date.")
            # Rejection remains possible when evidence is unavailable; approval
            # always requires current state. Expiry/Matter checks apply to all.
            if selection.decision == UserDecision.APPROVE:
                self._current(proposal, spec, target)
            result.append(
                {
                    "proposal_id": proposal.id,
                    "fingerprint": proposal.fingerprint,
                    "decision": selection.decision.value,
                    "defer_until": selection.defer_until,
                    "target": spec["target"],
                    "page_id": spec["page_id"],
                    "changes": spec["changes"],
                    "execution_is_separate": True,
                }
            )
        return result

    @_audited_request
    def execute(self, proposal_id: str, *, context: RequestContext) -> ProposalExecution:
        self.authority.authorize(context, Capability.EXECUTE_NOTION_EDIT)
        # Automatic independent verification is part of this scoped workflow;
        # do not let an execute-only channel acquire verify authority implicitly.
        self.authority.authorize(context, Capability.VERIFY_NOTION_EDIT)
        if not self._writes_allowed():
            raise EditError("Notion writes are disabled.")
        proposal, spec, target = self._load(proposal_id)
        if approval_problem(proposal):
            raise EditError("A current approval of this exact version is required.")
        if self.store.list_executions(proposal_id=proposal_id):
            # Conservative: no retries of any started attempt, including failures.
            raise EditError("An attempt already exists; reconcile it without repeating the write.")
        self._current(proposal, spec, target)
        presentation = {
            "operation": "execute_and_verify",
            "proposal_id": proposal.id,
            "fingerprint": proposal.fingerprint,
            "page_id": target.page_id,
            "target_digest": target.fingerprint,
            "changes": spec["changes"],
            "certification": "Invalidate financial certification; never certify or pay.",
        }
        self.store.note_edit_event("execution_presented", presentation, context.provenance())
        confirmation = self.authority.confirm_human(
            context,
            Capability.EXECUTE_NOTION_EDIT,
            presentation=presentation,
        )
        fresh, fresh_spec, fresh_target = self._load(proposal_id)
        if (
            approval_problem(fresh)
            or fresh != proposal
            or fresh_spec != spec
            or fresh_target != target
        ):
            raise EditError("Approval or target changed during confirmation.")
        self._current(fresh, fresh_spec, fresh_target)
        for capability in (
            Capability.EXECUTE_NOTION_EDIT,
            Capability.VERIFY_NOTION_EDIT,
        ):
            self.authority.authorize(context, capability)
        if not self._writes_allowed() or time.time() >= confirmation.expires_at:
            raise EditError("Write gate closed or confirmation expired.")
        now = datetime.now(UTC).isoformat()
        execution = ProposalExecution(
            id="ex_" + uuid4().hex[:16],
            proposal_id=proposal.id,
            proposal_fingerprint=proposal.fingerprint,
            matter_id=proposal.matter_id,
            intent=proposal.intent,
            status=ExecutionStatus.RUNNING,
            origin=context.principal.channel,
            created_at=now,
            updated_at=now,
            started_at=now,
            executor="notion.scoped_metadata_edit",
            plan_digest=digest(spec),
            preflight="passed",
            authorization=confirmation.id,
            principal=context.principal.subject,
            correlation_id=context.correlation_id,
            request_provenance=context.provenance(),
        )
        self.authority.consume_confirmation(
            confirmation,
            context,
            Capability.EXECUTE_NOTION_EDIT,
            presentation=presentation,
        )
        # Atomically claim both proposal and target before touching certification or
        # the provider. A crash from here is uncertain, including across processes.
        try:
            self.store.start_notion_edit(execution, target.page_id, confirmation, presentation)
        except sqlite3.IntegrityError:
            raise EditError(
                "This record has an unresolved execution; no further write may start."
            ) from None
        try:
            self.backend.invalidate_certification(target)
            # Check semantic source state once more after invalidation. Certificate
            # validity changes are expected here; material catalog versions are not.
            self._current(fresh, fresh_spec, fresh_target)

            def revalidate():
                current, current_spec, current_target = self._load(proposal.id)
                if (
                    approval_problem(current)
                    or current != fresh
                    or current_spec != spec
                    or current_target != target
                    or not self._writes_allowed()
                    or time.time() >= confirmation.expires_at
                ):
                    raise EditError("Execution authorization changed before dispatch.")
                self.authority.authorize(context, Capability.EXECUTE_NOTION_EDIT)
                self.authority.authorize(context, Capability.VERIFY_NOTION_EDIT)

            self.backend.write(
                target,
                {c["property_id"]: c["after"] for c in spec["changes"]},
                RecordSnapshot(**spec["before"]),
                revalidate,
            )
        except Exception:
            return self._uncertain(execution, "write_or_prewrite_interrupted")
        execution = replace(
            execution,
            status=ExecutionStatus.EXECUTED_UNVERIFIED,
            finished_at=datetime.now(UTC).isoformat(),
            outcome="Write returned; not yet verified.",
        )
        if not self.store.transition_execution(execution, expected=ExecutionStatus.RUNNING):
            raise EditError("Execution changed; manual review required.")
        return self.verify(execution.id, context=context)

    @_audited_request
    def verify(self, execution_id: str, *, context: RequestContext) -> ProposalExecution:
        self.authority.authorize(context, Capability.VERIFY_NOTION_EDIT)
        execution = self.store.get_execution(execution_id)
        if execution is None or execution.intent != ProposalIntent.EDIT_NOTION_RECORD:
            raise EditError("Unknown Notion edit execution.")
        if execution.status == ExecutionStatus.VERIFIED_SUCCESS:
            return execution
        if execution.status != ExecutionStatus.EXECUTED_UNVERIFIED:
            # A RUNNING process may still be in flight. Never release its target.
            raise EditError("Attempt may still be running; manual reconciliation required.")
        try:
            proposal, spec, target = self._load(execution.proposal_id, historical=True)
            if execution.plan_digest != digest(spec):
                raise EditError("Execution plan changed.")
            actual = self.backend.read(target)
            expected = dict(spec["before"]["hashes"])
            for change in spec["changes"]:
                expected[change["property_id"]] = digest(["select", change["after"]])
            success = (
                actual.hashes == expected
                and actual.schema_digest == spec["before"]["schema_digest"]
                and actual.finance_version == spec["before"]["finance_version"]
            )
        except Exception:
            return self._uncertain(execution, "verification_unavailable")
        if not success:
            return self._uncertain(execution, "verification_mismatch")
        now = datetime.now(UTC).isoformat()
        settled = replace(
            execution,
            status=ExecutionStatus.VERIFIED_SUCCESS,
            verification="verified_success",
            verification_method="independent_notion_read",
            verified_at=now,
            updated_at=now,
            failure_category="",
            outcome="Approved properties verified; protected properties unchanged. Not certified.",
            evidence={"expected_digest": digest(expected), "actual_digest": digest(actual.hashes)},
            verification_provenance=context.provenance(),
        )
        self.store.finish_notion_edit(settled, target.page_id)
        return settled

    def _uncertain(self, execution: ProposalExecution, category: str) -> ProposalExecution:
        result = replace(
            execution,
            status=ExecutionStatus.EXECUTED_UNVERIFIED,
            verification="inconclusive",
            failure_category=category,
            updated_at=datetime.now(UTC).isoformat(),
            outcome=(
                "Outcome unverified; target locked against further writes. Manual review required."
            ),
        )
        if not self.store.transition_execution(result, expected=execution.status):
            raise EditError("Execution changed; manual review required.")
        self.store.note_edit_event(category, {"execution_id": result.id}, result.request_provenance)
        return result

    def _load(self, proposal_id: str, *, historical: bool = False):
        proposal = self.store.get_proposal(proposal_id)
        spec = self.store.get_notion_edit(proposal_id)
        if proposal is None or proposal.intent != ProposalIntent.EDIT_NOTION_RECORD or spec is None:
            raise EditError("Unknown Notion edit proposal.")
        target = self._target(spec["target"])
        if (
            digest(spec) != proposal.fingerprint
            or target.fingerprint != spec["target_digest"]
            or proposal.knowledge_ids != (target.key,)
        ):
            raise EditError("Proposal or target contract changed.")
        self._validate(target, {c["property_id"]: c["after"] for c in spec["changes"]})
        if not historical:
            matter = self.store.get_matter(proposal.matter_id)
            if matter is None or matter.status != MatterStatus.OPEN:
                raise EditError("Matter is not open.")
            if not proposal.expires_at or datetime.fromisoformat(
                proposal.expires_at
            ) <= datetime.now(UTC):
                raise EditError("Proposal expired; a fresh review is required.")
        return proposal, spec, target

    def _current(self, proposal, spec, target):
        current = self.backend.read(target)
        if asdict(current) != spec["before"]:
            self.store.close_proposal(
                proposal.id,
                status=ProposalStatus.INVALIDATED,
                updated_at=datetime.now(UTC).isoformat(),
                status_reason="Notion semantic state changed",
            )
            self.store.note_edit_event(
                "edit_invalidated",
                {
                    "proposal_id": proposal.id,
                    "fingerprint": proposal.fingerprint,
                    "current_digest": digest(asdict(current)),
                },
                proposal.request_provenance,
            )
            raise EditError("Notion record changed; proposal invalidated.")

    def _writes_allowed(self) -> bool:
        return self.writes_enabled and (self._write_gate is None or self._write_gate() is True)

    def _target(self, key):
        targets = self.targets
        if self._targets_provider is not None:
            current = self._targets_provider()
            targets = {t.key: t for t in current}
            if len(targets) != len(current):
                raise EditError("Ambiguous target policy.")
        target = targets.get(key)
        if target is None:
            raise EditError("Target is not registered for scoped edits.")
        return target

    @staticmethod
    def _validate(target, replacements):
        rules = {r.property_id: r for r in target.properties}
        if (
            not isinstance(replacements, dict)
            or not replacements
            or len(rules) != len(target.properties)
            or set(rules) & set(target.audit_property_ids)
            or set(replacements) - rules.keys()
        ):
            raise EditError("Unapproved property scope.")
        for key, value in replacements.items():
            if type(value) is not str or value not in rules[key].values:
                raise EditError("Replacement is outside the approved enum contract.")
