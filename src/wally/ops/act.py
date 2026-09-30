"""Act & Verify: execute one approved proposal on explicit user request, then verify.

``ActVerifyService`` is interface-neutral. A channel adapter (CLI and REPL today)
asks the principal authority for a request context and calls ``execute``; the
service, not the adapter, checks that context for ``EXECUTE_PROPOSAL``. No Observe
pass, brief, inbox, model output, or source content calls it.

The proposal never selects an executor, tool, URL, or credential. The code maps a
supported intent to one typed plan built from trusted Knowledge, asks the user
again through the existing ApprovalGate and ApprovalProvider, rechecks the
approval immediately before the executor runs, and records every stage. Outcomes
count only when an independent read-only check confirms them.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from wally.audit.logger import AuditLogger
from wally.exceptions import (
    AuthorizationError,
    ExecutionNotStartedError,
    ExecutionRequestError,
)
from wally.models.actions import ActionClass, PlannedAction
from wally.models.browser import BrowserStepStatus
from wally.models.knowledge import KnowledgeClass
from wally.models.ops import (
    UNCERTAIN_EXECUTION_STATUSES,
    ExecutionStatus,
    MatterStatus,
    ProposalExecution,
    ProposalIntent,
    ProposedAction,
    VerificationOutcome,
)
from wally.models.principal import Capability, RequestContext, RequestProvenance
from wally.ops.execution import (
    NO_TRUSTED_TARGET,
    STALE_APPROVAL,
    UNSUPPORTED_ACTION,
    UNSUPPORTED_EXECUTION_MESSAGE,
    approval_problem,
    support_problem,
)
from wally.ops.priority import parse_time
from wally.ops.propose import propose_for_matter
from wally.ops.store import OperationsStore
from wally.ops.text import clean_title
from wally.providers.approval import ApprovalProvider
from wally.providers.knowledge import KnowledgeProvider
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.browser_safety import resolve_trusted_portal_url
from wally.runtime.principals import PrincipalAuthority
from wally.runtime.secrets_safety import (
    auth_success_selector,
    auth_success_url_contains,
    evaluate_secret_reference,
    portal_login_refs,
    portal_login_selectors,
)
from wally.safety.gates import ApprovalGate, GateResult

PORTAL_REVIEW_EXECUTOR = "browser.portal_review_login"
PORTAL_REVIEW_ACTION = PlannedAction(
    provider="browser",
    action="portal_review_login",
    parameters={},
    action_class=ActionClass.REVERSIBLE,
)

EXECUTED_UNVERIFIED_MESSAGE = "Executed, but verification could not confirm completion."
REVIEW_SUCCESS_OUTCOME = (
    "Portal login verified. No payment was made; the bill stays open until "
    "payment evidence arrives."
)
REVIEW_FAILURE_OUTCOME = "Portal login did not verify. Nothing was paid."
DUPLICATE_MESSAGE = "This approved proposal already has an execution attempt."
USER_CONFIRMED = "user_confirmation"
VERIFY_AUTH_METHOD = "browser_verify_auth"

# Knowledge metadata keys a portal review may use. Amounts, payees, and payment
# fields are deliberately absent: a review never carries payment data.
_REVIEW_KNOWLEDGE_KEYS = (
    "provider",
    "payment_portal_url",
    "portal_url",
    "portal_username_ref",
    "login_username_ref",
    "portal_password_ref",
    "login_password_ref",
    "login_username_selector",
    "login_password_selector",
    "login_submit_selector",
    "auth_success_selector",
    "auth_success_url_contains",
)


@dataclass(frozen=True)
class PortalReviewPlan:
    """Typed plan for logging in to a trusted bill portal. Holds refs, never values."""

    proposal_id: str
    fingerprint: str
    matter_id: str
    knowledge_id: str
    provider_label: str
    portal_host: str
    knowledge: dict[str, str]
    digest: str

    def authorization_summary(self) -> str:
        return (
            f"Execute approved proposal {self.proposal_id}: log in to the "
            f"{self.provider_label} portal at {self.portal_host} to review a bill. "
            "Wally will check the login and stop. No payment will be made. "
            "Portal credentials are read only if you approve."
        )


@dataclass(frozen=True)
class ExecutionReport:
    message: str
    execution: ProposalExecution | None = None
    blocked: bool = False


@dataclass(frozen=True)
class ExecutionLifecycle:
    """Who asked, and under which correlation id, at each stage of one execution."""

    proposal_request: RequestProvenance
    approval: RequestProvenance
    execution: RequestProvenance
    verification: RequestProvenance


class ActVerifyService:
    """Interface-neutral execution service. Channel adapters only build contexts."""

    def __init__(
        self,
        store: OperationsStore,
        *,
        authority: PrincipalAuthority,
        reconcile: Callable[[datetime], object],
        knowledge: KnowledgeProvider | None,
        browser_executor: GovernedBrowserExecutor | None,
        gate: ApprovalGate,
        approval: ApprovalProvider,
        audit: AuditLogger | None = None,
        bills_role: str = "finance",
    ) -> None:
        self._store = store
        self._authority = authority
        self._reconcile = reconcile
        self._knowledge = knowledge
        self._browser = browser_executor
        self._gate = gate
        self._approval = approval
        self._audit = audit
        self._bills_role = bills_role

    @property
    def store(self) -> OperationsStore:
        return self._store

    def execute(
        self,
        proposal_id: str,
        *,
        context: RequestContext,
        now: datetime | None = None,
    ) -> ExecutionReport:
        """Execute one approved proposal for an authenticated caller.

        Every channel adapter calls this same method. The adapter is not the
        authorization boundary: this service asks the principal authority, and only
        a context it issued with ``EXECUTE_PROPOSAL`` proceeds.
        """
        self._require(context, Capability.EXECUTE_PROPOSAL, "Execution")
        current = _clock(now)
        if self._store.get_proposal(proposal_id) is None:
            raise ExecutionRequestError(f"Unknown proposal: {proposal_id}")
        self._log("execution_requested", proposal_id=proposal_id, **_trace(context))

        self._reconcile(current)
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            raise ExecutionRequestError(f"Unknown proposal: {proposal_id}")

        problem = approval_problem(proposal) or self._matter_problem(proposal, current)
        if problem is not None:
            return self._preflight_failed(proposal, context, current, problem)

        unsupported = support_problem(proposal)
        if unsupported is not None:
            return self._preflight_failed(proposal, context, current, unsupported)

        existing = self._recover_pending(proposal, current)
        if existing is not None:
            return self._duplicate(existing)

        plan, plan_problem = self._build_plan(proposal)
        if plan is None:
            return self._preflight_failed(proposal, context, current, plan_problem)
        self._log(
            "execution_preflight_passed",
            proposal_id=proposal.id,
            fingerprint=proposal.fingerprint,
            executor=PORTAL_REVIEW_EXECUTOR,
        )

        authorization = self._authorize(plan)
        if authorization != "granted":
            return self._record_terminal(
                proposal,
                context,
                current,
                status=ExecutionStatus.AUTHORIZATION_DENIED,
                plan=plan,
                authorization=authorization,
                failure_category=authorization,
                outcome="Execution was not authorized. Nothing ran.",
                event="execution_authorization_denied",
            )

        # The user may have taken a while at the prompt; approval, Matter, evidence,
        # and the trusted target must all be unchanged before anything runs.
        final_check = self._final_check(proposal.id, plan, current)
        if final_check is not None:
            return self._record_terminal(
                proposal,
                context,
                current,
                status=ExecutionStatus.PREFLIGHT_FAILED,
                plan=plan,
                authorization=authorization,
                failure_category=final_check,
                outcome=_preflight_message(final_check),
                event=_preflight_event(final_check),
            )

        execution = _new_execution(
            proposal,
            context,
            current,
            status=ExecutionStatus.PENDING,
            executor=PORTAL_REVIEW_EXECUTOR,
            plan_digest=plan.digest,
            preflight="passed",
            authorization=authorization,
        )
        if not self._store.insert_execution(execution):
            blocking = self._store.blocking_execution(proposal.fingerprint)
            return self._duplicate(blocking)

        running = replace(
            execution,
            status=ExecutionStatus.RUNNING,
            started_at=_iso(current),
            updated_at=_iso(current),
        )
        if not self._store.transition_execution(running, expected=ExecutionStatus.PENDING):
            return ExecutionReport(
                "Execution was interrupted before it started. Nothing ran.",
                self._store.get_execution(execution.id),
                blocked=True,
            )
        self._log_execution("execution_started", running)
        return self._run_portal_review(running, plan, current)

    def verify(
        self,
        execution_id: str,
        *,
        context: RequestContext,
        confirm: VerificationOutcome | None = None,
        now: datetime | None = None,
    ) -> ExecutionReport:
        """Re-assess stored evidence, or record the user's own check. Runs no action.

        Only a context holding ``VERIFY_EXECUTION`` may record an outcome, so no
        channel can claim success merely by saying so.
        """
        self._require(context, Capability.VERIFY_EXECUTION, "Verification")
        current = _clock(now)
        execution = self._store.get_execution(execution_id)
        if execution is None:
            raise ExecutionRequestError(f"Unknown execution: {execution_id}")
        verifier = context.provenance()
        self._log_execution("execution_verify_requested", execution, context=verifier)
        if execution.status not in UNCERTAIN_EXECUTION_STATUSES:
            return ExecutionReport(
                f"Execution is {execution.status.value}; nothing to verify.", execution
            )
        if confirm is not None and confirm != VerificationOutcome.INCONCLUSIVE:
            confirmed = replace(
                execution,
                status=_status_for(confirm),
                verification=confirm.value,
                verification_method=USER_CONFIRMED,
                verified_at=_iso(current),
                finished_at=execution.finished_at or _iso(current),
                updated_at=_iso(current),
                outcome=(
                    "You confirmed the portal review completed. No payment was made."
                    if confirm == VerificationOutcome.VERIFIED_SUCCESS
                    else "You confirmed the portal review did not complete."
                ),
                verification_provenance=verifier,
            )
            if not self._store.transition_execution(confirmed, expected=execution.status):
                raise ExecutionRequestError("Execution changed while recording confirmation.")
            self._log_execution("execution_user_confirmed", confirmed, context=verifier)
            self._log_matter_unchanged(confirmed)
            return ExecutionReport(
                confirmed.outcome,
                confirmed,
                blocked=confirm != VerificationOutcome.VERIFIED_SUCCESS,
            )

        outcome = assess_portal_review(execution.evidence)
        if outcome == VerificationOutcome.INCONCLUSIVE:
            self._log_execution(
                "execution_verification_inconclusive", execution, context=verifier
            )
            return ExecutionReport(
                f"{EXECUTED_UNVERIFIED_MESSAGE} Check the portal yourself, then run "
                f"`wally verify {execution.id} --confirm success` or `--confirm failure`.",
                execution,
                blocked=True,
            )
        return self._settle(
            execution, outcome, current, method=VERIFY_AUTH_METHOD, verifier=verifier
        )

    def lifecycle(self, execution_id: str) -> ExecutionLifecycle:
        """Correlation ids for request → proposal → approval → execution → verification."""
        execution = self._store.get_execution(execution_id)
        if execution is None:
            raise ExecutionRequestError(f"Unknown execution: {execution_id}")
        proposal = self._store.get_proposal(execution.proposal_id)
        return ExecutionLifecycle(
            proposal_request=(
                proposal.request_provenance if proposal is not None else RequestProvenance()
            ),
            approval=RequestProvenance(
                channel=proposal.decision_origin if proposal is not None else "",
                principal=proposal.decision_principal if proposal is not None else "",
                correlation_id=(
                    proposal.decision_correlation_id if proposal is not None else ""
                ),
            ),
            execution=execution.request_provenance,
            verification=execution.verification_provenance,
        )

    def list_executions(self, *, proposal_id: str | None = None) -> list[ProposalExecution]:
        executions = self._store.list_executions(proposal_id=proposal_id)
        self._log("executions_listed", count=str(len(executions)))
        return executions

    def get_execution(self, execution_id: str) -> ProposalExecution:
        execution = self._store.get_execution(execution_id)
        if execution is None:
            raise ExecutionRequestError(f"Unknown execution: {execution_id}")
        self._log_execution("execution_viewed", execution)
        return execution

    def _run_portal_review(
        self, execution: ProposalExecution, plan: PortalReviewPlan, now: datetime
    ) -> ExecutionReport:
        assert self._browser is not None
        try:
            login = self._browser.run_portal_review_login(
                bill=dict(plan.knowledge), authorized=True
            )
        except ExecutionNotStartedError as exc:
            failed = replace(
                execution,
                status=ExecutionStatus.FAILED,
                failure_category=exc.category,
                finished_at=_iso(now),
                updated_at=_iso(now),
                outcome="Execution stopped before the portal login was submitted. Nothing ran.",
            )
            self._store.transition_execution(failed, expected=ExecutionStatus.RUNNING)
            self._log_execution("execution_not_started", failed)
            return ExecutionReport(failed.outcome, failed, blocked=True)
        except Exception:
            # The login may or may not have been submitted. Never retry this blindly.
            uncertain = replace(
                execution,
                status=ExecutionStatus.EXECUTED_UNVERIFIED,
                failure_category="outcome_uncertain",
                verification=VerificationOutcome.INCONCLUSIVE.value,
                finished_at=_iso(now),
                updated_at=_iso(now),
                outcome=EXECUTED_UNVERIFIED_MESSAGE,
            )
            self._store.transition_execution(uncertain, expected=ExecutionStatus.RUNNING)
            self._log_execution("execution_outcome_uncertain", uncertain)
            return ExecutionReport(
                f"{EXECUTED_UNVERIFIED_MESSAGE} The executor stopped with an error, so "
                f"Wally will not retry. Review with `wally verify {uncertain.id}`.",
                uncertain,
                blocked=True,
            )

        executed = replace(
            execution,
            status=ExecutionStatus.EXECUTED_UNVERIFIED,
            finished_at=_iso(now),
            updated_at=_iso(now),
            outcome=EXECUTED_UNVERIFIED_MESSAGE,
            evidence={"login_status": login.status.value},
        )
        self._store.transition_execution(executed, expected=ExecutionStatus.RUNNING)
        self._log_execution("execution_adapter_returned", executed)

        try:
            check = self._browser.verify_portal_review(
                bill=dict(plan.knowledge), session_id=login.session_id
            )
            evidence = {
                **executed.evidence,
                "verify_status": check.status.value,
                "authenticated": _flag(check.authenticated),
            }
        except Exception:
            evidence = {**executed.evidence, "verify_status": "error", "authenticated": ""}
        finally:
            with contextlib.suppress(Exception):
                self._browser.close_session(login.session_id)

        with_evidence = replace(executed, evidence=evidence)
        self._store.transition_execution(
            with_evidence, expected=ExecutionStatus.EXECUTED_UNVERIFIED
        )
        outcome = assess_portal_review(evidence)
        if outcome == VerificationOutcome.INCONCLUSIVE:
            self._log_execution("execution_verification_inconclusive", with_evidence)
            return ExecutionReport(
                f"{EXECUTED_UNVERIFIED_MESSAGE} Review with `wally verify {with_evidence.id}`.",
                with_evidence,
                blocked=True,
            )
        return self._settle(
            with_evidence,
            outcome,
            now,
            method=VERIFY_AUTH_METHOD,
            verifier=with_evidence.request_provenance,
        )

    def _settle(
        self,
        execution: ProposalExecution,
        outcome: VerificationOutcome,
        now: datetime,
        *,
        method: str,
        verifier: RequestProvenance,
    ) -> ExecutionReport:
        settled = replace(
            execution,
            status=_status_for(outcome),
            verification=outcome.value,
            verification_method=method,
            verified_at=_iso(now),
            updated_at=_iso(now),
            outcome=(
                REVIEW_SUCCESS_OUTCOME
                if outcome == VerificationOutcome.VERIFIED_SUCCESS
                else REVIEW_FAILURE_OUTCOME
            ),
            verification_provenance=verifier,
        )
        if not self._store.transition_execution(settled, expected=execution.status):
            raise ExecutionRequestError("Execution changed while recording verification.")
        self._log_execution(f"execution_{outcome.value}", settled, context=verifier)
        self._log_matter_unchanged(settled)
        return ExecutionReport(
            settled.outcome,
            settled,
            blocked=outcome != VerificationOutcome.VERIFIED_SUCCESS,
        )

    def _authorize(self, plan: PortalReviewPlan) -> str:
        action = replace(
            PORTAL_REVIEW_ACTION,
            parameters={"proposal_id": plan.proposal_id, "portal_host": plan.portal_host},
        )
        gate = self._gate.evaluate(action, action.action_class)
        if gate == GateResult.DENY_DRY_RUN:
            return "dry_run"
        # Retained on purpose: approving a proposal is not approving this run.
        self._log(
            "execution_authorization_requested",
            proposal_id=plan.proposal_id,
            fingerprint=plan.fingerprint,
            executor=PORTAL_REVIEW_EXECUTOR,
        )
        try:
            granted = self._approval.request_approval(
                plan.authorization_summary(), action_class=action.action_class.value
            )
        except Exception:
            granted = False
        if granted is not True:
            return "denied"
        self._log(
            "execution_authorized",
            proposal_id=plan.proposal_id,
            fingerprint=plan.fingerprint,
            executor=PORTAL_REVIEW_EXECUTOR,
        )
        return "granted"

    def _final_check(
        self, proposal_id: str, plan: PortalReviewPlan, now: datetime
    ) -> str | None:
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            return STALE_APPROVAL
        problem = approval_problem(proposal) or self._matter_problem(proposal, now)
        if problem is not None:
            return problem
        if proposal.fingerprint != plan.fingerprint:
            return STALE_APPROVAL
        rebuilt, rebuilt_problem = self._build_plan(proposal)
        if rebuilt is None:
            return rebuilt_problem
        if rebuilt.digest != plan.digest:
            return "target_changed"
        return None

    def _matter_problem(self, proposal: ProposedAction, now: datetime) -> str | None:
        """Recompute the proposal from current evidence; any drift is a stale approval."""
        matter = self._store.get_matter(proposal.matter_id)
        if matter is None or matter.status != MatterStatus.OPEN:
            return STALE_APPROVAL
        expires = parse_time(proposal.expires_at) if proposal.expires_at else None
        if expires is not None and expires <= now:
            return STALE_APPROVAL
        observations = {item.id: item for item in self._store.list_observations()}
        support = [
            observations[observation_id]
            for observation_id in matter.observation_ids
            if observation_id in observations
        ]
        candidate = propose_for_matter(matter, support, now=now)
        if candidate is None or candidate.fingerprint != proposal.fingerprint:
            return STALE_APPROVAL
        return None

    def _build_plan(
        self, proposal: ProposedAction
    ) -> tuple[PortalReviewPlan | None, str]:
        if proposal.intent != ProposalIntent.REVIEW_BILL or len(proposal.knowledge_ids) != 1:
            return None, NO_TRUSTED_TARGET
        if self._browser is None:
            return None, "browser_unavailable"
        if self._knowledge is None:
            return None, "knowledge_unavailable"
        knowledge_id = proposal.knowledge_ids[0]
        try:
            asset = self._knowledge.get(knowledge_id)
        except Exception:
            return None, "knowledge_unavailable"
        if asset.knowledge_class == KnowledgeClass.PENDING or asset.role != self._bills_role:
            return None, "untrusted_target"
        meta = {str(key).lower(): str(value) for key, value in asset.metadata.items()}
        trusted = {key: meta[key].strip() for key in _REVIEW_KNOWLEDGE_KEYS if meta.get(key)}
        url, policy = resolve_trusted_portal_url(trusted)
        if not policy.allowed or not url:
            return None, NO_TRUSTED_TARGET
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            return None, "untrusted_target"
        username_ref, password_ref = portal_login_refs(trusted)
        user_selector, password_selector, _ = portal_login_selectors(trusted)
        if not username_ref or not password_ref or not user_selector or not password_selector:
            return None, "missing_login_config"
        for reference in (username_ref, password_ref):
            if not evaluate_secret_reference(reference).allowed:
                return None, "invalid_secret_reference"
        if not auth_success_selector(trusted) and not auth_success_url_contains(trusted):
            return None, "missing_verification_config"
        label = clean_title(trusted.get("provider") or asset.title) or "bill"
        digest = hashlib.sha256(
            json.dumps({"knowledge_id": knowledge_id, **trusted}, sort_keys=True).encode()
        ).hexdigest()
        return (
            PortalReviewPlan(
                proposal_id=proposal.id,
                fingerprint=proposal.fingerprint,
                matter_id=proposal.matter_id,
                knowledge_id=knowledge_id,
                provider_label=label[:80],
                portal_host=parsed.hostname,
                knowledge=trusted,
                digest=digest,
            ),
            "",
        )

    def _recover_pending(
        self, proposal: ProposedAction, now: datetime
    ) -> ProposalExecution | None:
        """Return a blocking attempt, first retiring a pending row that never ran.

        The executor is reached only after pending → running succeeds, so a pending
        row is known not executed. Marking it failed also makes any concurrent owner
        of that row fail its own pending → running step.
        """
        existing = self._store.blocking_execution(proposal.fingerprint)
        if existing is None or existing.status != ExecutionStatus.PENDING:
            return existing
        interrupted = replace(
            existing,
            status=ExecutionStatus.FAILED,
            failure_category="interrupted_before_start",
            updated_at=_iso(now),
            finished_at=_iso(now),
            outcome="Interrupted before the executor started. Nothing ran.",
        )
        if self._store.transition_execution(interrupted, expected=ExecutionStatus.PENDING):
            self._log_execution("execution_interrupted_before_start", interrupted)
        return self._store.blocking_execution(proposal.fingerprint)

    def _duplicate(self, existing: ProposalExecution | None) -> ExecutionReport:
        if existing is None:
            return ExecutionReport(DUPLICATE_MESSAGE, None, blocked=True)
        self._log_execution("execution_duplicate_blocked", existing)
        if existing.status == ExecutionStatus.VERIFIED_SUCCESS:
            message = f"Already executed and verified as {existing.id}. Nothing was repeated."
        elif existing.status in UNCERTAIN_EXECUTION_STATUSES:
            message = (
                f"{EXECUTED_UNVERIFIED_MESSAGE} Attempt {existing.id} has an uncertain "
                f"outcome, so Wally will not run it again. Review with "
                f"`wally verify {existing.id}`."
            )
        else:
            message = f"{DUPLICATE_MESSAGE} ({existing.id}, {existing.status.value})"
        return ExecutionReport(message, existing, blocked=True)

    def _preflight_failed(
        self,
        proposal: ProposedAction,
        context: RequestContext,
        now: datetime,
        category: str,
    ) -> ExecutionReport:
        return self._record_terminal(
            proposal,
            context,
            now,
            status=ExecutionStatus.PREFLIGHT_FAILED,
            plan=None,
            authorization="",
            failure_category=category,
            outcome=_preflight_message(category),
            event=_preflight_event(category),
        )

    def _record_terminal(
        self,
        proposal: ProposedAction,
        context: RequestContext,
        now: datetime,
        *,
        status: ExecutionStatus,
        plan: PortalReviewPlan | None,
        authorization: str,
        failure_category: str,
        outcome: str,
        event: str,
    ) -> ExecutionReport:
        execution = _new_execution(
            proposal,
            context,
            now,
            status=status,
            executor=PORTAL_REVIEW_EXECUTOR if plan is not None else "",
            plan_digest=plan.digest if plan is not None else "",
            preflight="passed" if plan is not None else "failed",
            authorization=authorization,
            finished_at=_iso(now),
            outcome=outcome,
            failure_category=failure_category,
        )
        self._store.insert_execution(execution)
        self._log_execution(event, execution)
        return ExecutionReport(outcome, execution, blocked=True)

    def _log_matter_unchanged(self, execution: ProposalExecution) -> None:
        # A portal review is not payment evidence. Only Observe may resolve the Matter.
        self._log(
            "matter_unchanged_after_execution",
            execution_id=execution.id,
            matter_id=execution.matter_id,
            status=execution.status.value,
        )

    def _log_execution(
        self,
        event_type: str,
        execution: ProposalExecution,
        *,
        context: RequestProvenance | None = None,
    ) -> None:
        caller = context or execution.request_provenance
        self._log(
            event_type,
            execution_id=execution.id,
            proposal_id=execution.proposal_id,
            fingerprint=execution.proposal_fingerprint,
            status=execution.status.value,
            category=execution.failure_category,
            executor=execution.executor,
            origin=caller.channel or execution.origin,
            principal=caller.principal or execution.principal,
            correlation_id=caller.correlation_id or execution.correlation_id,
            execution_correlation_id=execution.correlation_id,
        )

    def _require(self, context: object, capability: Capability, action: str) -> None:
        try:
            self._authority.authorize(context, capability)
        except AuthorizationError as exc:
            raise ExecutionRequestError(
                f"{action} requires an authenticated user request. {exc}"
            ) from exc

    def _log(self, event_type: str, **parameters: str) -> None:
        if self._audit is None:
            return
        self._audit.log_simple(
            event_type=event_type,
            session_id="ops",
            outcome="success",
            provider="ops",
            parameters={key: value for key, value in parameters.items() if value},
        )


def _new_execution(
    proposal: ProposedAction,
    context: RequestContext,
    now: datetime,
    **fields: Any,
) -> ProposalExecution:
    provenance = context.provenance()
    return ProposalExecution(
        id=f"ex_{uuid4().hex[:12]}",
        proposal_id=proposal.id,
        proposal_fingerprint=proposal.fingerprint,
        matter_id=proposal.matter_id,
        intent=proposal.intent,
        origin=provenance.channel,
        created_at=_iso(now),
        updated_at=_iso(now),
        principal=provenance.principal,
        correlation_id=provenance.correlation_id,
        request_provenance=provenance,
        **fields,
    )


def _trace(context: RequestContext) -> dict[str, str]:
    provenance = context.provenance()
    return {
        "origin": provenance.channel,
        "principal": provenance.principal,
        "correlation_id": provenance.correlation_id,
    }


def assess_portal_review(evidence: dict[str, Any]) -> VerificationOutcome:
    """Decide the outcome from the read-only login check alone.

    The login step returning is not success. Only an explicit authenticated result
    from the configured success condition counts either way.
    """
    authenticated = evidence.get("authenticated", "")
    if authenticated == "true" and evidence.get("verify_status") == BrowserStepStatus.COMPLETED:
        return VerificationOutcome.VERIFIED_SUCCESS
    if authenticated == "false":
        return VerificationOutcome.VERIFIED_FAILURE
    return VerificationOutcome.INCONCLUSIVE


def _preflight_message(category: str) -> str:
    if category in {UNSUPPORTED_ACTION, NO_TRUSTED_TARGET}:
        return UNSUPPORTED_EXECUTION_MESSAGE
    if category == STALE_APPROVAL:
        return (
            "Approval no longer matches the current facts. Nothing ran. "
            "Review the proposal again with `wally approvals`."
        )
    if category == "target_changed":
        return "The trusted portal configuration changed during authorization. Nothing ran."
    if category == "not_approved":
        return "This proposal is not approved. Nothing ran."
    if category == "untrusted_decision":
        return "The recorded approval did not come from a trusted user command. Nothing ran."
    return f"Execution preflight failed ({category}). Nothing ran."


def _preflight_event(category: str) -> str:
    if category in {STALE_APPROVAL, "target_changed"}:
        return "execution_blocked_stale_approval"
    if category in {UNSUPPORTED_ACTION, NO_TRUSTED_TARGET}:
        return "execution_unsupported"
    return "execution_preflight_failed"


def _status_for(outcome: VerificationOutcome) -> ExecutionStatus:
    if outcome == VerificationOutcome.VERIFIED_SUCCESS:
        return ExecutionStatus.VERIFIED_SUCCESS
    return ExecutionStatus.VERIFIED_FAILURE


def _flag(value: bool | None) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return ""


def _clock(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def format_execution(execution: ProposalExecution) -> str:
    lines = [
        f"Execution {execution.id}",
        f"   Proposal: {execution.proposal_id} ({execution.intent.value})",
        f"   Status: {execution.status.value}",
        f"   Requested: {execution.created_at} via {execution.origin}",
    ]
    if execution.correlation_id:
        lines.append(f"   Correlation: {execution.correlation_id}")
    if execution.executor:
        lines.append(f"   Executor: {execution.executor}")
    if execution.authorization:
        lines.append(f"   Authorization: {execution.authorization}")
    if execution.verification:
        method = f" ({execution.verification_method})" if execution.verification_method else ""
        lines.append(f"   Verification: {execution.verification}{method}")
    if execution.failure_category:
        lines.append(f"   Reason: {execution.failure_category}")
    if execution.outcome:
        lines.append(f"   Outcome: {execution.outcome}")
    return "\n".join(lines)


def format_executions(executions: list[ProposalExecution]) -> str:
    if not executions:
        return "No executions recorded."
    return "\n\n".join(format_execution(item) for item in executions)
