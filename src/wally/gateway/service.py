"""Authenticated Gateway dispatch. The runtime issues every principal.

An adapter is registered to one channel. The credential authenticates that
adapter. Fields in the caller's payload cannot choose a channel, a principal,
a grant, or a capability. The HMAC key stays inside ``PrincipalAuthority``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from wally.exceptions import GatewayError, WallyError
from wally.models.gateway import (
    TITLE_MAX,
    ActiveMatter,
    EvidenceItem,
    GatewayRequestRecord,
    HandleVisibility,
    cap_text,
    claim_keys,
    parse_evidence,
)
from wally.models.ops import Matter, ProposalExecution, ProposedAction, VerificationOutcome
from wally.models.principal import Capability, RequestContext, RequestProvenance, clean_ref
from wally.ops.act import ActVerifyService, ExecutionLifecycle, ExecutionReport
from wally.ops.decisions import UserDecision
from wally.ops.notion_edits import DecisionSelection, NotionEditService
from wally.ops.request_propose import (
    GroundingError,
    TrustedRecord,
    attach_delivery,
    ground_delivery,
)
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.runtime.principals import PrincipalAuthority, new_correlation_id

GATEWAY_INGRESS = "gateway_ingress"
_LOCAL_CHANNELS = frozenset({"cli", "repl"})
_AUTH_FAILURE = "Gateway authentication failed."
_CLAIM_FAILURE = "External payloads cannot set channel, principal, subject, grant, or capabilities."
CHATGPT_CHANNEL = "chatgpt"
TELEGRAM_CHANNEL = "telegram"
_NO_APPROVAL = "Execution requires an approval adapter for this channel. Nothing ran."

_OPS: dict[str, Capability] = {
    "reconcile_notion_record": Capability.READ_CONTEXT,
    "get_notion_edit_status": Capability.READ_CONTEXT,
    "inspect_notion_execution": Capability.VERIFY_NOTION_EDIT,
    "propose_notion_edit": Capability.SUBMIT_REQUEST,
    "get_notion_edit": Capability.READ_CONTEXT,
    "decide_notion_edits": Capability.DECIDE_PROPOSAL,
    "execute_notion_edit": Capability.EXECUTE_NOTION_EDIT,
    "verify_notion_edit": Capability.VERIFY_NOTION_EDIT,
    "submit_request": Capability.SUBMIT_REQUEST,
    "get_context": Capability.READ_CONTEXT,
    "list_proposals": Capability.READ_CONTEXT,
    "list_active": Capability.READ_CONTEXT,
    "lifecycle": Capability.READ_CONTEXT,
    "decide": Capability.DECIDE_PROPOSAL,
    "execute": Capability.EXECUTE_PROPOSAL,
    "verify": Capability.VERIFY_EXECUTION,
    "open_active": Capability.LINK_CHANNEL,
    "link_channel": Capability.LINK_CHANNEL,
    "set_visibility": Capability.LINK_CHANNEL,
}


@dataclass(frozen=True)
class AdapterRegistration:
    """One authenticated ingress, fixed to a single registered channel."""

    adapter_id: str
    channel: str
    credential: str
    approval_adapter: bool = False


@dataclass
class GatewayResult:
    ok: bool
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "error": self.error, "data": self.data}


@dataclass(frozen=True)
class GatewayHost:
    """Identity the MCP host supplied, after the connection itself authenticated.

    A subject string in a tool argument never populates this object.
    """

    connection_authenticated: bool = False
    subject: str = ""
    organization: str = ""
    session: str = ""


_HOST: ContextVar[GatewayHost | None] = ContextVar("wally_gateway_host", default=None)


class GatewayRuntime:
    """Dispatch Gateway calls into the existing runtime services."""

    def __init__(
        self,
        store: OperationsStore,
        authority: PrincipalAuthority,
        adapters: tuple[AdapterRegistration, ...] = (),
        *,
        ops: ObserveBriefService | None = None,
        act: ActVerifyService | None = None,
        notion_edits: NotionEditService | None = None,
        audit: Any = None,
        trusted_records: tuple[TrustedRecord, ...] = (),
        owner_subjects: frozenset[str] = frozenset(),
        owner_organizations: frozenset[str] = frozenset(),
    ) -> None:
        self._store = store
        self._authority = authority
        self._ops = ops
        self._act = act
        self._notion_edits = notion_edits
        if notion_edits is not None and (
            notion_edits.authority is not authority or notion_edits.store is not store
        ):
            raise GatewayError("Notion edits must use the Gateway authority and store.")
        self._audit = audit
        self._records = trusted_records
        self._owner_subjects = owner_subjects
        self._owner_orgs = owner_organizations
        self._adapters = _registrations(authority, adapters)

    def dispatch(
        self,
        *,
        adapter_id: str,
        credential: str,
        op: str,
        body: dict | None = None,
        now: datetime | None = None,
        host: GatewayHost | None = None,
    ) -> GatewayResult:
        payload = body if isinstance(body, dict) else None
        token = _HOST.set(host)
        try:
            return self._dispatch_body(
                adapter_id=adapter_id,
                credential=credential,
                op=op,
                payload=payload,
                now=now,
            )
        finally:
            _HOST.reset(token)

    def _dispatch_body(
        self,
        *,
        adapter_id: str,
        credential: str,
        op: str,
        payload: dict | None,
        now: datetime | None,
    ) -> GatewayResult:
        if payload is None:
            return self._denied("", "", op, (), "Request body must be an object.")
        adapter = self._authenticate(adapter_id, credential)
        if adapter is None:
            self._audit_call(False, "", "", "", op, ())
            return GatewayResult(ok=False, error=_AUTH_FAILURE)
        if claim_keys(payload):
            return self._denied(adapter.channel, "", op, (), _CLAIM_FAILURE)
        capability = _OPS.get(op)
        if capability is None:
            return self._denied(adapter.channel, "", op, (), f"Unknown gateway operation: {op}")
        current = _clock(now)
        try:
            return self._handle(adapter, capability, op, payload, current)
        except WallyError as exc:
            return self._denied(adapter.channel, "", op, (), str(exc))

    def _handle(
        self,
        adapter: AdapterRegistration,
        capability: Capability,
        op: str,
        body: dict,
        now: datetime,
    ) -> GatewayResult:
        handler: dict[str, Callable[[AdapterRegistration, dict, datetime], GatewayResult]] = {
            "reconcile_notion_record": self._reconcile_notion_record,
            "get_notion_edit_status": self._get_notion_edit_status,
            "inspect_notion_execution": self._inspect_notion_execution,
            "propose_notion_edit": self._propose_notion_edit,
            "get_notion_edit": self._get_notion_edit,
            "decide_notion_edits": self._decide_notion_edits,
            "execute_notion_edit": self._execute_notion_edit,
            "verify_notion_edit": self._verify_notion_edit,
            "submit_request": self._submit,
            "get_context": self._context,
            "list_proposals": self._proposals,
            "list_active": self._active,
            "lifecycle": self._lifecycle,
            "decide": self._decide,
            "execute": self._execute,
            "verify": self._verify,
            "open_active": self._open_active,
            "link_channel": self._link,
            "set_visibility": self._visibility,
        }
        context = self._issue(adapter)
        self._authority.authorize(context, capability)
        return handler[op](adapter, body, now)

    def _edits(self) -> NotionEditService:
        if self._notion_edits is None:
            raise GatewayError("Scoped Notion edits are not configured; no writes are enabled.")
        return self._notion_edits

    @staticmethod
    def _edit_fields(body, required):
        allowed = set(required) | {"external_session_ref", "external_request_ref"}
        if set(body) - allowed or not set(required) <= set(body):
            raise GatewayError("Invalid scoped Notion request fields.")

    def _propose_notion_edit(self, adapter, body, now):
        self._edit_fields(body, ("target_key", "replacements"))
        if not isinstance(body["target_key"], str) or not isinstance(body["replacements"], dict):
            raise GatewayError("Target and replacements are required.")
        proposal = self._edits().propose(
            body["target_key"],
            body["replacements"],
            context=self._issued_for_body(adapter, body),
        )
        return GatewayResult(ok=True, data=_public_proposal(proposal))

    def _reconcile_notion_record(self, adapter, body, now):
        self._edit_fields(body, ("target_key",))
        return GatewayResult(
            ok=True,
            data=self._edits().reconcile(
                clean_ref(body["target_key"]), context=self._issued_for_body(adapter, body)
            ),
        )

    def _get_notion_edit_status(self, adapter, body, now):
        self._edit_fields(body, ())
        return GatewayResult(
            ok=True, data=self._edits().status(context=self._issued_for_body(adapter, body))
        )

    def _inspect_notion_execution(self, adapter, body, now):
        self._edit_fields(body, ("execution_id",))
        return GatewayResult(
            ok=True,
            data=self._edits().inspect_execution(
                clean_ref(body["execution_id"]), context=self._issued_for_body(adapter, body)
            ),
        )

    def _get_notion_edit(self, adapter, body, now):
        self._edit_fields(body, ("proposal_id",))
        return GatewayResult(
            ok=True,
            data=self._edits().review(
                clean_ref(body["proposal_id"]),
                context=self._issued_for_body(adapter, body),
            ),
        )

    def _decide_notion_edits(self, adapter, body, now):
        self._edit_fields(body, ("selections",))
        raw = body["selections"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
            raise GatewayError("Select 1–20 exact proposals.")
        selections = []
        try:
            for item in raw:
                if not isinstance(item, dict) or set(item) - {
                    "proposal_id",
                    "fingerprint",
                    "decision",
                    "defer_until",
                }:
                    raise ValueError
                selections.append(
                    DecisionSelection(
                        item["proposal_id"],
                        item["fingerprint"],
                        UserDecision(item["decision"]),
                        item.get("defer_until", ""),
                    )
                )
                if any(not isinstance(value, str) for value in item.values()):
                    raise ValueError
        except (ValueError, KeyError, TypeError):
            raise GatewayError("Invalid exact proposal selection.") from None
        decided = self._edits().decide(
            tuple(selections),
            context=self._issued_for_body(adapter, body),
        )
        return GatewayResult(ok=True, data={"proposals": [_public_proposal(p) for p in decided]})

    def _execute_notion_edit(self, adapter, body, now):
        self._edit_fields(body, ("proposal_id",))
        if not adapter.approval_adapter:
            raise GatewayError(_NO_APPROVAL)
        execution = self._edits().execute(
            clean_ref(body["proposal_id"]),
            context=self._issued_for_body(adapter, body),
        )
        return GatewayResult(ok=True, data=_public_execution(execution))

    def _verify_notion_edit(self, adapter, body, now):
        self._edit_fields(body, ("execution_id",))
        execution = self._edits().verify(
            clean_ref(body["execution_id"]),
            context=self._issued_for_body(adapter, body),
        )
        return GatewayResult(ok=True, data=_public_execution(execution))

    def _submit(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        try:
            evidence = parse_evidence(body.get("evidence"))
        except ValueError as exc:
            raise GatewayError(str(exc)) from exc
        active_id = clean_ref(body.get("active_matter_id"))
        continued = clean_ref(body.get("continue_correlation_id"))
        session_ref = clean_ref(body.get("external_session_ref"))
        request_ref = clean_ref(body.get("external_request_ref"))
        host = _HOST.get()
        if host is not None and host.session:
            session_ref = clean_ref(host.session)
        if request_ref:
            prior = self._store.get_gateway_request_by_external(adapter.channel, request_ref)
            if prior is not None:
                return self._replayed_submit(prior)
        correlation_id = self._correlation(continued, active_id)
        if active_id and self._store.get_active_matter(active_id) is None:
            raise GatewayError(f"Unknown active matter: {active_id}")
        context = self._issue(
            adapter,
            correlation_id=correlation_id,
            session_ref=session_ref,
            request_ref=request_ref,
        )
        self._authority.authorize(context, Capability.SUBMIT_REQUEST)
        proposal, chosen_handle = self._proposal_for_submit(
            body,
            active_id=active_id,
            provenance=context.provenance(),
            now=now,
        )
        if proposal is not None and not active_id:
            active_id = chosen_handle or self._handle_for_matter(proposal.matter_id, now)
        stamp = _iso(now)
        record = GatewayRequestRecord(
            id=f"gw_{uuid4().hex[:16]}",
            correlation_id=correlation_id,
            active_matter_id=active_id,
            channel=context.principal.channel,
            principal=context.principal.subject,
            external_session_ref=session_ref,
            external_request_ref=request_ref,
            created_at=stamp,
            evidence=evidence,
        )
        self._store.save_gateway_request(record)
        if active_id:
            self._store.link_correlation(active_id, correlation_id, linked_at=stamp)
            if session_ref:
                self._store.touch_session(
                    active_id,
                    channel=context.principal.channel,
                    external_session_ref=session_ref,
                    seen_at=stamp,
                )
        self._audit_call(
            True,
            record.channel,
            record.principal,
            correlation_id,
            "submit_request",
            evidence,
        )
        return GatewayResult(
            ok=True,
            data={
                "request_id": record.id,
                "correlation_id": correlation_id,
                "active_matter_id": active_id,
                "channel": record.channel,
                "evidence_count": len(evidence),
                "evidence_hashes": [item.content_hash for item in evidence],
                "proposal": _public_proposal(proposal) if proposal is not None else None,
            },
        )

    def _replayed_submit(self, prior: GatewayRequestRecord) -> GatewayResult:
        """Return the request already stored for this ingress id.

        Long polling can redeliver an update. The second pass must not open
        another request, Matter, or proposal.
        """
        proposals, _executions = self._store.correlated(prior.correlation_id)
        proposal = proposals[-1] if proposals else None
        return GatewayResult(
            ok=True,
            data={
                "request_id": prior.id,
                "correlation_id": prior.correlation_id,
                "active_matter_id": prior.active_matter_id,
                "channel": prior.channel,
                "evidence_count": len(prior.evidence),
                "evidence_hashes": [item.content_hash for item in prior.evidence],
                "proposal": _public_proposal(proposal) if proposal is not None else None,
                "replayed": True,
            },
        )

    def _proposal_for_submit(
        self,
        body: dict,
        *,
        active_id: str,
        provenance: RequestProvenance,
        now: datetime,
    ) -> tuple[ProposedAction | None, str]:
        document_hint = cap_text(body.get("document_hint"), TITLE_MAX)
        recipient_hint = cap_text(body.get("recipient_hint"), TITLE_MAX)
        if not document_hint and not recipient_hint:
            return None, ""
        try:
            grounding = ground_delivery(
                self._records,
                document_hint=document_hint,
                recipient_hint=recipient_hint,
            )
            proposal, chosen = attach_delivery(
                self._store,
                grounding,
                active_matter_id=active_id,
                provenance=provenance,
                now=now,
            )
        except GroundingError as exc:
            raise GatewayError(str(exc)) from exc
        return proposal, chosen

    def _handle_for_matter(self, matter_id: str, now: datetime) -> str:
        matter = self._store.get_matter(matter_id)
        title = matter.title if matter is not None else "Delivery"
        stamp = _iso(now)
        handle = ActiveMatter(
            id=f"am_{uuid4().hex[:16]}",
            matter_id=matter_id,
            title=title,
            visibility=HandleVisibility.ACTIVE,
            created_at=stamp,
            updated_at=stamp,
        )
        self._store.save_active_matter(handle)
        return handle.id

    def _require_chatgpt_decision(
        self, host: GatewayHost | None, expected: str, proposal_id: str
    ) -> None:
        if host is None or not host.connection_authenticated:
            raise GatewayError("Decision requires an authenticated ChatGPT connection.")
        subject = host.subject.strip()
        if not subject or subject not in self._owner_subjects:
            raise GatewayError("This ChatGPT identity is not the Wally owner.")
        if self._owner_orgs and host.organization.strip() not in self._owner_orgs:
            raise GatewayError("This ChatGPT workspace is not allowed to decide.")
        if not expected:
            raise GatewayError("A decision needs the exact proposal fingerprint.")
        self._match_fingerprint(proposal_id, expected)
        raise GatewayError(
            "Hosted ChatGPT decisions are disabled: no verified action-specific human confirmation."
        )

    def _match_fingerprint(self, proposal_id: str, expected: str) -> None:
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None or not _same_text(proposal.fingerprint, expected):
            raise GatewayError("The proposal version does not match.")

    def _correlation(self, continued: str, active_id: str) -> str:
        if not continued:
            return new_correlation_id()
        if not self._store.correlation_exists(continued):
            raise GatewayError("That correlation is not an existing request lineage.")
        linked = self._store.active_matter_ids_for_correlation(continued)
        if active_id and linked and active_id not in linked:
            raise GatewayError("That correlation belongs to a different active matter.")
        return continued

    def _context(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        del adapter, now
        matter_id = clean_ref(body.get("matter_id"))
        correlation_id = clean_ref(body.get("correlation_id"))
        active_id = clean_ref(body.get("active_matter_id"))
        matter = self._store.get_matter(matter_id) if matter_id else None
        handle = self._store.get_active_matter(active_id) if active_id else None
        proposals: list[ProposedAction] = []
        executions: list[ProposalExecution] = []
        if correlation_id:
            found, executions = self._store.correlated(correlation_id)
            proposals.extend(found)
        if matter_id:
            proposals.extend(self._store.list_proposals(matter_id=matter_id))
            if matter is None:
                matter = self._store.get_matter(matter_id)
        if handle is not None and handle.matter_id and matter is None:
            matter = self._store.get_matter(handle.matter_id)
        self._audit_call(True, "", "", correlation_id, "get_context", ())
        return GatewayResult(
            ok=True,
            data={
                "matter": _public_matter(matter),
                "handle": _public_handle(handle),
                "proposals": [_public_proposal(item) for item in _unique_proposals(proposals)],
                "executions": [_public_execution(item) for item in executions],
            },
        )

    def _proposals(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        del adapter, now
        matter_id = clean_ref(body.get("matter_id")) or None
        proposals = self._store.list_proposals(matter_id=matter_id)
        self._audit_call(True, "", "", "", "list_proposals", ())
        return GatewayResult(
            ok=True,
            data={"proposals": [_public_proposal(item) for item in proposals]},
        )

    def _active(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        del adapter, now
        raw = clean_ref(body.get("visibility"))
        visibility = None
        if raw:
            try:
                visibility = HandleVisibility(raw)
            except ValueError as exc:
                raise GatewayError("visibility must be active or archived.") from exc
        handles = self._store.list_active_matters(visibility=visibility)
        self._audit_call(True, "", "", "", "list_active", ())
        return GatewayResult(
            ok=True,
            data={"active_matters": [_public_handle(item) for item in handles]},
        )

    def _lifecycle(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        del adapter, now
        execution_id = clean_ref(body.get("execution_id"))
        correlation_id = clean_ref(body.get("correlation_id"))
        if execution_id:
            if self._act is None:
                raise GatewayError("Lifecycle is unavailable.")
            lifecycle = self._act.lifecycle(execution_id)
            self._audit_call(True, "", "", correlation_id, "lifecycle", ())
            return GatewayResult(ok=True, data=_public_lifecycle(lifecycle))
        if not correlation_id:
            raise GatewayError("lifecycle needs an execution_id or correlation_id.")
        proposals, executions = self._store.correlated(correlation_id)
        requests = self._store.list_gateway_requests(correlation_id=correlation_id)
        self._audit_call(True, "", "", correlation_id, "lifecycle", ())
        return GatewayResult(
            ok=True,
            data={
                "correlation_id": correlation_id,
                "requests": [_public_request(item) for item in requests],
                "proposals": [_public_proposal(item) for item in proposals],
                "executions": [_public_execution(item) for item in executions],
            },
        )

    def _decide(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        if self._ops is None:
            raise GatewayError("Decisions are unavailable.")
        proposal_id = clean_ref(body.get("proposal_id"))
        if not proposal_id:
            raise GatewayError("decide needs a proposal_id.")
        try:
            decision = UserDecision(str(body.get("decision") or ""))
        except ValueError as exc:
            raise GatewayError("decision must be approve, reject, or defer.") from exc
        host = _HOST.get()
        expected = body.get("expected_fingerprint")
        expected_text = expected if isinstance(expected, str) else ""
        if adapter.channel == CHATGPT_CHANNEL:
            if decision == UserDecision.DEFER:
                raise GatewayError("ChatGPT decisions are approve or reject.")
            self._require_chatgpt_decision(host, expected_text, proposal_id)
        elif expected_text:
            self._match_fingerprint(proposal_id, expected_text)
        context = self._issued_for_body(adapter, body)
        stored = self._ops.decide(
            proposal_id,
            decision=decision,
            context=context,
            note=cap_text(body.get("note"), 280),
            defer_until=clean_ref(body.get("defer_until")),
            now=now,
        )
        self._audit_call(
            True,
            context.principal.channel,
            context.principal.subject,
            context.correlation_id,
            "decide",
            (),
            external_subject="" if host is None else host.subject,
        )
        return GatewayResult(ok=True, data=_public_proposal(stored))

    def _execute(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        if not adapter.approval_adapter:
            return self._denied(adapter.channel, "", "execute", (), _NO_APPROVAL)
        if self._act is None:
            raise GatewayError("Execution is unavailable.")
        proposal_id = clean_ref(body.get("proposal_id"))
        if not proposal_id:
            raise GatewayError("execute needs a proposal_id.")
        context = self._issued_for_body(adapter, body)
        report = self._act.execute(proposal_id, context=context, now=now)
        self._audit_call(
            True,
            context.principal.channel,
            context.principal.subject,
            context.correlation_id,
            "execute",
            (),
        )
        return GatewayResult(ok=True, data=_public_report(report))

    def _verify(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        if self._act is None:
            raise GatewayError("Verification is unavailable.")
        execution_id = clean_ref(body.get("execution_id"))
        if not execution_id:
            raise GatewayError("verify needs an execution_id.")
        confirm = _confirm(body.get("confirm"))
        context = self._issued_for_body(adapter, body)
        report = self._act.verify(execution_id, context=context, confirm=confirm, now=now)
        self._audit_call(
            True,
            context.principal.channel,
            context.principal.subject,
            context.correlation_id,
            "verify",
            (),
        )
        return GatewayResult(ok=True, data=_public_report(report))

    def _open_active(
        self, adapter: AdapterRegistration, body: dict, now: datetime
    ) -> GatewayResult:
        del adapter
        title = cap_text(body.get("title"), TITLE_MAX)
        if not title:
            raise GatewayError("An active matter needs a title.")
        matter_id = clean_ref(body.get("matter_id"))
        if matter_id and self._store.get_matter(matter_id) is None:
            raise GatewayError(f"Unknown matter: {matter_id}")
        stamp = _iso(now)
        handle = ActiveMatter(
            id=f"am_{uuid4().hex[:16]}",
            matter_id=matter_id,
            title=title,
            visibility=HandleVisibility.ACTIVE,
            created_at=stamp,
            updated_at=stamp,
        )
        self._store.save_active_matter(handle)
        self._audit_call(True, "", "", "", "open_active", ())
        return GatewayResult(ok=True, data=_public_handle(handle))

    def _link(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        active_id = clean_ref(body.get("active_matter_id"))
        session_ref = clean_ref(body.get("external_session_ref"))
        if not active_id or not session_ref:
            raise GatewayError("link_channel needs an active matter and a session ref.")
        if self._store.get_active_matter(active_id) is None:
            raise GatewayError(f"Unknown active matter: {active_id}")
        stamp = _iso(now)
        self._store.touch_session(
            active_id,
            channel=adapter.channel,
            external_session_ref=session_ref,
            seen_at=stamp,
        )
        handle = self._store.get_active_matter(active_id)
        self._audit_call(True, adapter.channel, "", "", "link_channel", ())
        return GatewayResult(ok=True, data=_public_handle(handle))

    def _visibility(self, adapter: AdapterRegistration, body: dict, now: datetime) -> GatewayResult:
        del adapter
        active_id = clean_ref(body.get("active_matter_id"))
        try:
            visibility = HandleVisibility(str(body.get("visibility") or ""))
        except ValueError as exc:
            raise GatewayError("visibility must be active or archived.") from exc
        if not self._store.set_handle_visibility(active_id, visibility, updated_at=_iso(now)):
            raise GatewayError(f"Unknown active matter: {active_id}")
        handle = self._store.get_active_matter(active_id)
        self._audit_call(True, "", "", "", "set_visibility", ())
        return GatewayResult(ok=True, data=_public_handle(handle))

    def _issued_for_body(self, adapter: AdapterRegistration, body: dict) -> RequestContext:
        context = self._issue(
            adapter,
            correlation_id=clean_ref(body.get("correlation_id")),
            session_ref=clean_ref(body.get("external_session_ref")),
            request_ref=clean_ref(body.get("external_request_ref")),
        )
        return context

    def _issue(
        self,
        adapter: AdapterRegistration,
        *,
        correlation_id: str = "",
        session_ref: str = "",
        request_ref: str = "",
    ) -> RequestContext:
        return self._authority.issue(
            adapter.channel,
            external_session_ref=session_ref,
            external_request_ref=request_ref,
            correlation_id=correlation_id,
        )

    def _authenticate(self, adapter_id: str, credential: str) -> AdapterRegistration | None:
        adapter = self._adapters.get(adapter_id)
        if adapter is None or not credential:
            return None
        if not _same_secret(credential, adapter.credential):
            return None
        return adapter

    def _denied(
        self,
        channel: str,
        principal: str,
        op: str,
        evidence: tuple[EvidenceItem, ...],
        error: str,
    ) -> GatewayResult:
        self._audit_call(False, channel, principal, "", op, evidence)
        return GatewayResult(ok=False, error=error)

    def _audit_call(
        self,
        ok: bool,
        channel: str,
        principal: str,
        correlation_id: str,
        op: str,
        evidence: tuple[EvidenceItem, ...],
        external_subject: str = "",
    ) -> None:
        if self._audit is None:
            return
        hashes = [item.content_hash for item in evidence]
        parameters: dict[str, Any] = {
            "op": op,
            "channel": channel,
            "principal": principal,
            "correlation_id": correlation_id,
            "evidence_count": len(evidence),
            "evidence_hashes": hashes,
        }
        if external_subject:
            parameters["external_subject"] = external_subject
        self._audit.log_simple(
            event_type="gateway_request_accepted" if ok else "gateway_request_denied",
            session_id="gateway",
            outcome="success" if ok else "denied",
            provider="gateway",
            parameters=parameters,
        )


def _same_text(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left.encode(), right.encode())


def _registrations(
    authority: PrincipalAuthority,
    adapters: tuple[AdapterRegistration, ...],
) -> dict[str, AdapterRegistration]:
    known = set(authority.channels())
    by_id: dict[str, AdapterRegistration] = {}
    secrets: set[str] = set()
    for adapter in adapters:
        if adapter.channel in _LOCAL_CHANNELS:
            raise GatewayError("The local terminal is not a Gateway adapter.")
        if adapter.channel not in known:
            raise GatewayError(f"Channel {adapter.channel!r} is not registered with Wally.")
        if not adapter.adapter_id or not adapter.credential:
            raise GatewayError("Adapter registration is incomplete.")
        if adapter.adapter_id in by_id:
            raise GatewayError(f"Duplicate adapter id: {adapter.adapter_id}")
        digest = hashlib.sha256(adapter.credential.encode()).hexdigest()
        if digest in secrets:
            raise GatewayError("Adapter credentials must be distinct.")
        secrets.add(digest)
        by_id[adapter.adapter_id] = adapter
    return by_id


def _same_secret(given: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(given.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _clock(now: datetime | None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _confirm(value: object) -> VerificationOutcome | None:
    if value is None or value == "":
        return None
    mapping = {
        "success": VerificationOutcome.VERIFIED_SUCCESS,
        "failure": VerificationOutcome.VERIFIED_FAILURE,
        "inconclusive": VerificationOutcome.INCONCLUSIVE,
    }
    outcome = mapping.get(str(value))
    if outcome is None:
        raise GatewayError("confirm must be success, failure, or inconclusive.")
    return outcome


def _unique_proposals(proposals: list[ProposedAction]) -> list[ProposedAction]:
    seen: set[str] = set()
    ordered: list[ProposedAction] = []
    for proposal in proposals:
        if proposal.id in seen:
            continue
        seen.add(proposal.id)
        ordered.append(proposal)
    return ordered


def _public_matter(matter: Matter | None) -> dict[str, str] | None:
    if matter is None:
        return None
    return {
        "id": matter.id,
        "status": matter.status.value,
        "domain": matter.domain.value,
        "title": matter.title,
    }


def _public_proposal(proposal: ProposedAction) -> dict[str, str]:
    return {
        "id": proposal.id,
        "matter_id": proposal.matter_id,
        "intent": proposal.intent.value,
        "status": proposal.status.value,
        "fingerprint": proposal.fingerprint,
        "decision": proposal.decision,
    }


def _public_execution(execution: ProposalExecution) -> dict[str, str]:
    return {
        "id": execution.id,
        "proposal_id": execution.proposal_id,
        "status": execution.status.value,
        "origin": execution.origin,
        "correlation_id": execution.correlation_id,
        "verification": execution.verification,
    }


def _public_request(record: GatewayRequestRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "correlation_id": record.correlation_id,
        "active_matter_id": record.active_matter_id,
        "channel": record.channel,
        "evidence_count": len(record.evidence),
        "evidence_hashes": [item.content_hash for item in record.evidence],
    }


def _public_handle(handle: ActiveMatter | None) -> dict[str, Any] | None:
    if handle is None:
        return None
    return {
        "id": handle.id,
        "matter_id": handle.matter_id,
        "title": handle.title,
        "visibility": handle.visibility.value,
        "correlation_ids": list(handle.correlation_ids),
        "sessions": [
            {
                "channel": session.channel,
                "external_session_ref": session.external_session_ref,
                "first_seen_at": session.first_seen_at,
                "last_seen_at": session.last_seen_at,
            }
            for session in handle.sessions
        ],
    }


def _public_lifecycle(lifecycle: ExecutionLifecycle) -> dict[str, dict[str, str]]:
    return {
        "proposal_request": lifecycle.proposal_request.as_dict(),
        "approval": lifecycle.approval.as_dict(),
        "execution": lifecycle.execution.as_dict(),
        "verification": lifecycle.verification.as_dict(),
    }


def _public_report(report: ExecutionReport) -> dict[str, Any]:
    execution = report.execution
    return {
        "blocked": report.blocked,
        "message": report.message,
        "execution": _public_execution(execution) if execution is not None else None,
    }
