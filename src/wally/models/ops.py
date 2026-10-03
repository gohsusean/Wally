"""Operational observation, matter, proposal, and execution models — Chief of Staff Phases 1-4."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from wally.models.principal import RequestProvenance


class ObservationCategory(StrEnum):
    EMAIL_RECEIVED = "email_received"
    EMAIL_REPLY = "email_reply"
    EMAIL_SENT = "email_sent"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    CALENDAR_UPCOMING = "calendar_upcoming"
    CALENDAR_CHANGED = "calendar_changed"
    CALENDAR_CANCELLED = "calendar_cancelled"
    KNOWLEDGE_OBLIGATION = "knowledge_obligation"
    DEADLINE = "deadline"
    OTHER = "other"


class MatterStatus(StrEnum):
    OPEN = "open"
    WATCHING = "watching"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class MatterDomain(StrEnum):
    FINANCE = "finance"
    ADMIN = "admin"
    CALENDAR = "calendar"
    COMMUNICATIONS = "communications"
    OTHER = "other"


@dataclass(frozen=True)
class Observation:
    """A fact Wally noticed. Not a proposed action."""

    id: str
    fingerprint: str
    source: str
    source_id: str
    observed_at: str
    source_timestamp: str
    category: ObservationCategory
    title: str
    summary: str
    trusted: bool
    authority: str
    confidence: float
    thread_id: str = ""
    related_knowledge_id: str = ""
    related_matter_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Matter:
    """An open loop Wally is tracking over time."""

    id: str
    fingerprint: str
    title: str
    domain: MatterDomain
    status: MatterStatus
    created_at: str
    updated_at: str
    summary: str
    open_reason: str
    last_change: str
    priority_score: int = 0
    priority_reasons: tuple[str, ...] = ()
    due_at: str = ""
    expected_by: str = ""
    observation_ids: tuple[str, ...] = ()
    knowledge_ids: tuple[str, ...] = ()
    thread_id: str = ""
    source: str = ""
    resolution_evidence: str = ""
    confidence: float = 1.0
    recurrence_key: str = ""


class ProposalIntent(StrEnum):
    """What a proposal is advising, independent of any provider or tool."""

    PREPARE_FOR_EVENT = "prepare_for_event"
    REVIEW_BILL = "review_bill"
    DELIVER_DOCUMENT = "deliver_document"


class ProposalStatus(StrEnum):
    """Proposal lifecycle.

    ``approved``, ``rejected``, and ``deferred`` record an explicit user decision.
    None of them executes anything, and none of them is an execution-time tool
    approval. Act & Verify runs only on a separate user ``execute`` request.
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    DISMISSED = "dismissed"


# Still awaiting a user decision, or already decided and not yet closed by the system.
OPEN_PROPOSAL_STATUSES = frozenset(
    {
        ProposalStatus.PROPOSED,
        ProposalStatus.APPROVED,
        ProposalStatus.DEFERRED,
    }
)

# Statuses only an explicit user command may enter.
USER_DECISION_STATUSES = frozenset(
    {
        ProposalStatus.APPROVED,
        ProposalStatus.REJECTED,
        ProposalStatus.DEFERRED,
    }
)

# System-closed rows that may be reopened when the same facts return.
# Superseded rows stay history: reverting to an older version must not resurrect it.
# Rejected and dismissed rows stay closed so unchanged evidence cannot undo a refusal.
REOPENABLE_PROPOSAL_STATUSES = frozenset(
    {
        ProposalStatus.INVALIDATED,
        ProposalStatus.EXPIRED,
    }
)


class ProposalProvenance(StrEnum):
    """How a proposal was authored."""

    DETERMINISTIC_RULES = "deterministic_rules"


class ProposalRisk(StrEnum):
    """How much scrutiny the advised course of action deserves from the user."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ProposedAction:
    """Durable advice addressed to the user. Never an authorization to act.

    Carries intent plus reference identifiers and display-only prose. It holds no
    tool name, provider arguments, or executable payload, so it cannot be
    dispatched. A user decision recorded on this row authorizes nothing by itself.
    Translating an intent into something executable is a later phase's explicit,
    reviewable step.
    """

    id: str
    fingerprint: str
    matter_id: str
    intent: ProposalIntent
    status: ProposalStatus
    provenance: ProposalProvenance
    risk: ProposalRisk
    created_at: str
    updated_at: str
    title: str
    rationale: str
    suggestion: str
    confidence: float = 1.0
    content_hash: str = ""
    expires_at: str = ""
    status_reason: str = ""
    superseded_by: str = ""
    observation_ids: tuple[str, ...] = ()
    knowledge_ids: tuple[str, ...] = ()
    event_id: str = ""
    thread_id: str = ""
    decision: str = ""
    decided_at: str = ""
    # Channel the decision came through (``cli``, ``repl``; ``user_cli`` on v0.14 rows).
    decision_origin: str = ""
    decision_note: str = ""
    defer_until: str = ""
    decision_fingerprint: str = ""
    decision_principal: str = ""
    decision_correlation_id: str = ""
    # Where the request that produced this proposal came from. Audit data only:
    # excluded from the fingerprint and never consulted for authorization.
    request_provenance: RequestProvenance = field(default_factory=RequestProvenance)


class ExecutionStatus(StrEnum):
    """Lifecycle of one user-requested execution attempt.

    ``pending`` is written only after runtime authorization, and the executor is
    reached only after a compare-and-set to ``running``. A ``pending`` row therefore
    means "known not executed"; a ``running`` or ``executed_unverified`` row means
    the outcome is uncertain and must be reviewed, never retried automatically.
    """

    PENDING = "pending"
    PREFLIGHT_FAILED = "preflight_failed"
    AUTHORIZATION_DENIED = "authorization_denied"
    RUNNING = "running"
    EXECUTED_UNVERIFIED = "executed_unverified"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    FAILED = "failed"


# At most one row per proposal fingerprint may hold one of these at a time.
BLOCKING_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.PENDING,
        ExecutionStatus.RUNNING,
        ExecutionStatus.EXECUTED_UNVERIFIED,
        ExecutionStatus.VERIFIED_SUCCESS,
    }
)

# The executor may have acted; only verification or the user can settle these.
UNCERTAIN_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.RUNNING,
        ExecutionStatus.EXECUTED_UNVERIFIED,
    }
)


class VerificationOutcome(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    INCONCLUSIVE = "inconclusive"


@dataclass
class ProposalExecution:
    """Durable record of one execution attempt for an approved proposal.

    Holds identifiers, status, and short Wally-authored outcome text only. Secret
    values, page contents, and source text are never stored here.
    """

    id: str
    proposal_id: str
    proposal_fingerprint: str
    matter_id: str
    intent: ProposalIntent
    status: ExecutionStatus
    # Channel the execute request came through.
    origin: str
    created_at: str
    updated_at: str
    executor: str = ""
    plan_digest: str = ""
    preflight: str = ""
    authorization: str = ""
    started_at: str = ""
    finished_at: str = ""
    verification: str = ""
    verification_method: str = ""
    verified_at: str = ""
    outcome: str = ""
    failure_category: str = ""
    evidence: dict[str, str] = field(default_factory=dict)
    principal: str = ""
    correlation_id: str = ""
    request_provenance: RequestProvenance = field(default_factory=RequestProvenance)
    verification_provenance: RequestProvenance = field(default_factory=RequestProvenance)


@dataclass(frozen=True)
class BriefItem:
    matter_id: str
    title: str
    state: str
    why: str
    deadline: str
    confidence: float
    priority_score: int
    priority_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class BriefProposal:
    """Display projection of a ProposedAction.

    Deliberately narrower than the stored record: no fingerprint, content hash,
    provenance, status reason, supersession link, or reference identifier reaches a
    brief consumer, so nothing here can be replayed or dispatched.
    """

    proposal_id: str
    matter_id: str
    intent: ProposalIntent
    title: str
    rationale: str
    suggestion: str
    risk: ProposalRisk
    confidence: float = 1.0
    expires_at: str = ""


@dataclass(frozen=True)
class OperationalBrief:
    generated_at: str
    needs_attention: tuple[BriefItem, ...] = ()
    upcoming: tuple[BriefItem, ...] = ()
    waiting: tuple[BriefItem, ...] = ()
    recently_resolved: tuple[BriefItem, ...] = ()
    fyi: tuple[BriefItem, ...] = ()
    notes: tuple[str, ...] = ()
    proposals: tuple[BriefProposal, ...] = ()
