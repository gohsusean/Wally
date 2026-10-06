"""Local-owner certification and the sole trusted financial projection.

Candidates, certificates and provenance are data. Only the runtime authority and
fresh owner approval permit certification; no model-facing tool calls this service.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, replace
from uuid import uuid4

from wally.exceptions import AuthorizationError
from wally.finance.models import (
    SCHEMA_VERSION,
    Account,
    Candidate,
    Definition,
    Evidence,
    FinanceError,
    Instance,
    Kind,
    Locator,
    ReviewProfile,
    Scope,
    dependencies,
    digest,
    origin,
    parse_facts,
)
from wally.finance.notion import NotionFinanceReader, SourceMapping
from wally.finance.store import FinanceStore, Record
from wally.knowledge.registry import KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass
from wally.models.principal import Capability, RequestContext
from wally.providers.approval import ApprovalProvider
from wally.runtime.principals import LOCAL_TERMINAL, PrincipalAuthority

ATTESTATIONS = ("primary_evidence_reviewed", "identity_and_scope_confirmed", "no_secret_values")


class FinanceService:
    def __init__(
        self,
        store: FinanceStore,
        *,
        authority: PrincipalAuthority,
        approval: ApprovalProvider,
        registry: KnowledgeRegistry,
        reader: NotionFinanceReader | None = None,
        sources: Callable[[], tuple[SourceMapping, ...]] = lambda: (),
        profiles: Callable[[], dict[str, ReviewProfile]] = lambda: {},
        dry_run: bool = False,
    ):
        self.store = store
        self.authority = authority
        self.approval = approval
        self.registry = registry
        self.reader = reader
        self.sources = sources
        self.profiles = profiles
        self.dry_run = dry_run

    def _owner(self, context: object) -> RequestContext:
        ctx = self.authority.authorize(context, Capability.CERTIFY_FINANCIAL_DATA)
        if ctx.principal.authentication != LOCAL_TERMINAL or ctx.principal.channel not in {
            "cli",
            "repl",
        }:
            raise AuthorizationError(
                "Financial certification requires the authenticated local owner."
            )
        if self.dry_run:
            raise FinanceError("Financial certification is disabled in dry-run mode.")
        return ctx

    def _confirm(self, operation: str, binding: str) -> None:
        if (
            self.approval.request_approval(
                f"{operation}. Exact reviewed fingerprint: {binding}. Confirm this version only.",
                action_class="write",
            )
            is not True
        ):
            raise FinanceError("Owner confirmation denied.")

    def _source_gate(self, source: SourceMapping) -> None:
        source.validate()
        record = self.registry.find_by_key_or_id(source.database_id)
        if (
            record is None
            or record.classification != KnowledgeClass.OPERATIONAL
            or not record.approved_by
            or record.role != "finance"
            or not self.store.designated(source.key, source.fingerprint)
        ):
            raise FinanceError("Finance source is not approved, designated and mapped.")

    def designate(self, source_key: str, *, context: RequestContext) -> None:
        ctx = self._owner(context)
        matches = [s for s in self.sources() if s.key == source_key]
        if len(matches) != 1 or self.reader is None:
            raise FinanceError("Unknown or ambiguous finance source.")
        source = matches[0]
        mapping_fingerprint = source.fingerprint
        record = self.registry.find_by_key_or_id(source.database_id)
        if (
            record is None
            or record.classification != KnowledgeClass.OPERATIONAL
            or not record.approved_by
        ):
            raise FinanceError("Only an approved operational source may be designated.")
        self.reader.validate_schema(source)
        binding = digest(
            (source.fingerprint, record.classification, record.approved_at.isoformat())
        )
        self._confirm(f"Designate {source.kind.value} source for restricted finance reads", binding)
        current = self.registry.find_by_key_or_id(source.database_id)
        if current != record or [s.fingerprint for s in self.sources() if s.key == source_key] != [
            mapping_fingerprint
        ]:
            raise FinanceError("Source designation changed during confirmation.")
        self.reader.validate_schema(source)
        self.registry.set_finance_role(record.database_id)
        self.store.designate(source.key, mapping_fingerprint, ctx.provenance().as_dict())

    def refresh(self) -> list[Record]:
        """Completely enumerate before publishing anything; invalidate on any partial read."""
        previous = [r for r in self.store.records() if r.candidate.locator.system == "notion"]
        try:
            sources = self.sources()
            for source in sources:
                self._source_gate(source)
            if sources and self.reader is None:
                raise FinanceError("Finance source reader unavailable.")
            candidates = self.reader.read_all(sources) if sources else []
            by_locator = {c.locator.key: c for c in candidates}
            if len(by_locator) != len(candidates):
                raise FinanceError("Ambiguous financial locators.")
            order = {
                k: i
                for i, k in enumerate(
                    (Kind.PROPERTY, Kind.ENTITY, Kind.PROVIDER, Kind.ACCOUNT, Kind.DEFINITION)
                )
            }
            canonical: dict[str, str] = {}
            records = []
            for candidate in sorted(candidates, key=lambda c: order[c.kind]):
                facts = asdict(candidate.facts)
                relation_fields = {
                    Kind.ACCOUNT: ("provider", "subject"),
                    Kind.DEFINITION: ("account", "redundant_property"),
                }.get(candidate.kind, ())
                for name in relation_fields:
                    if facts.get(name):
                        if (
                            name
                            not in next(
                                s
                                for s in sources
                                if s.key
                                == (
                                    f"notion:{candidate.locator.database}:"
                                    + candidate.locator.data_source
                                )
                            ).fields
                        ):
                            if (
                                name != "subject"
                                or self.store.get(facts[name]).candidate.kind != Kind.ENTITY
                            ):
                                raise FinanceError(
                                    "Only explicit local entity subjects may be constant relations."
                                )
                            continue
                        if facts[name] not in canonical:
                            raise FinanceError("Missing or incorrectly ordered finance dependency.")
                        facts[name] = canonical[facts[name]]
                candidate = replace(candidate, facts=parse_facts(candidate.kind, facts))
                self._graph(candidate)
                record = self.store.register(candidate)
                canonical[candidate.locator.key] = record.id
                records.append(record)
            seen = set(canonical)
            for old in previous:
                if old.candidate.locator.key not in seen:
                    self.store.invalidate(old.id, "source_missing")
            return records
        except Exception:
            for old in previous:
                self.store.invalidate(old.id, "source_incomplete")
            for record in self.store.records():
                if record.candidate.kind == Kind.DEFINITION:
                    self.store.invalidate_scope(
                        record.id, Scope.PORTAL_REVIEW, "catalog_contract_invalid"
                    )
            raise FinanceError("Finance catalog incomplete or source contract invalid.") from None

    def _graph(self, candidate: Candidate) -> None:
        facts = candidate.facts
        if isinstance(facts, Account):
            provider = self.store.get(facts.provider)
            subject = self.store.get(facts.subject)
            subject_kind = Kind.PROPERTY if facts.subject_kind == "property" else Kind.ENTITY
            if provider.candidate.kind != Kind.PROVIDER or subject.candidate.kind != subject_kind:
                raise FinanceError("Incorrect account dependency kinds.")
        elif isinstance(facts, Definition):
            account = self.store.get(facts.account)
            if account.candidate.kind != Kind.ACCOUNT:
                raise FinanceError("Definition requires a bill account.")
            if facts.redundant_property and (
                account.candidate.facts.subject_kind != "property"
                or facts.redundant_property != account.candidate.facts.subject
            ):
                raise FinanceError("Redundant property conflicts with account scope.")
        elif isinstance(facts, Instance):
            definition = self.store.get(facts.definition)
            if (
                definition.candidate.kind != Kind.DEFINITION
                or facts.currency != definition.candidate.facts.currency
            ):
                raise FinanceError("Instance definition/currency mismatch.")
            if (
                facts.stage == "issued"
                and definition.candidate.facts.amount_policy == "fixed_contract"
                and facts.amount != definition.candidate.facts.fixed_amount
            ):
                raise FinanceError("Fixed contract amount mismatch requires definition review.")

    def _base(self, record: Record) -> None:
        current = self.store.get(record.id)
        if (
            current.invalid
            or current.version != record.version
            or record.candidate.schema_version != SCHEMA_VERSION
        ):
            raise FinanceError("Financial record needs attention.")
        if self.store.duplicates(record):
            raise FinanceError("Duplicate canonical financial identity.")
        if record.candidate.locator.system == "notion":
            matches = [
                s
                for s in self.sources()
                if s.key
                == (
                    f"notion:{record.candidate.locator.database}:{record.candidate.locator.data_source}"
                )
                and s.kind == record.candidate.kind
            ]
            if len(matches) != 1:
                raise FinanceError("Missing financial source contract.")
            self._source_gate(matches[0])
        elif record.candidate.locator.system != "local_owner":
            raise FinanceError("Unknown financial source.")
        self._graph(record.candidate)
        self.store.check_reference(record.candidate, record.id)

    def _deps(
        self, record: Record, scope: Scope, visited: frozenset[str] = frozenset()
    ) -> dict[str, str]:
        if record.id in visited:
            raise FinanceError("Cyclic financial dependencies.")
        visited = visited | {record.id}
        result = {}
        for dep in dependencies(record.candidate.facts):
            result[dep] = self._certificate(self.store.get(dep), Scope.IDENTITY, visited)["id"]
        if scope == Scope.PORTAL_REVIEW:
            if record.candidate.kind != Kind.DEFINITION:
                raise FinanceError("Portal certification applies to obligation definitions only.")
            result[record.id] = self._certificate(record, Scope.IDENTITY, visited - {record.id})[
                "id"
            ]
            _, profile = self._portal(record)
            result["profile"] = profile.fingerprint
        return result

    def _certificate(
        self, record: Record, scope: Scope, visited: frozenset[str] = frozenset()
    ) -> dict:
        try:
            self._base(record)
        except FinanceError:
            self.store.invalidate_scope(record.id, Scope.IDENTITY, "record_contract_invalid")
            self.store.invalidate_scope(record.id, Scope.PORTAL_REVIEW, "record_contract_invalid")
            raise
        certificate = self.store.certificate(record.id, scope)
        if (
            certificate is None
            or certificate["version"] != record.version
            or certificate["fingerprint"] != record.candidate.fingerprint
        ):
            raise FinanceError("Current financial certification required.")
        try:
            current_dependencies = self._deps(record, scope, visited)
        except FinanceError:
            self.store.invalidate_scope(record.id, scope, "dependency_unavailable")
            raise
        if (
            certificate["schema_version"] != SCHEMA_VERSION
            or certificate["dependencies"] != current_dependencies
        ):
            self.store.invalidate_scope(record.id, scope, "dependency_changed")
            raise FinanceError("Financial certificate dependencies changed.")
        return certificate

    def binding(self, object_id: str, scope: Scope = Scope.IDENTITY) -> str:
        record = self.store.get(object_id)
        cert = self._certificate(record, scope)
        return digest(
            (
                record.id,
                record.version,
                record.candidate.fingerprint,
                cert["id"],
                cert["dependencies"],
            )
        )

    def certify(
        self,
        object_id: str,
        scope: Scope,
        *,
        evidence: list[dict],
        attestations: dict[str, bool],
        context: RequestContext,
    ) -> str:
        ctx = self._owner(context)
        self.refresh()
        record = self.store.get(object_id)
        self._base(record)
        proofs = [asdict(Evidence.parse(e)) for e in evidence]
        if (
            not proofs
            or set(attestations) != set(ATTESTATIONS)
            or any(v is not True for v in attestations.values())
        ):
            raise FinanceError("Primary evidence and all owner attestations are required.")
        deps = self._deps(record, scope)
        binding = digest(
            (
                record.id,
                record.version,
                record.candidate.fingerprint,
                scope,
                deps,
                proofs,
                attestations,
            )
        )
        self._confirm(
            f"Certify {record.candidate.kind.value} {record.id} v{record.version} "
            f"for {scope.value}; owner reviewed primary evidence, identity/scope and no secrets. "
            + json.dumps(self.reviewed_summary(record), sort_keys=True),
            binding,
        )
        self.refresh()
        after = self.store.get(object_id)
        self._base(after)
        if after != record or self._deps(after, scope) != deps:
            raise FinanceError("Candidate or dependencies changed during certification.")
        return self.store.certify(
            record, scope, deps, proofs, attestations, ctx.provenance().as_dict()
        )

    def revoke(self, object_id: str, scope: Scope, *, context: RequestContext) -> None:
        ctx = self._owner(context)
        binding = self.binding(object_id, scope)
        self._confirm(f"Revoke certification for {object_id} {scope.value}", binding)
        if self.binding(object_id, scope) != binding:
            raise FinanceError("Certification changed during revocation.")
        self.store.invalidate_scope(object_id, scope, "owner_revoked", ctx.provenance().as_dict())

    def enable(self, definition_id: str, scope: Scope, *, context: RequestContext) -> None:
        ctx = self._owner(context)
        self.refresh()
        record = self.store.get(definition_id)
        if record.candidate.kind != Kind.DEFINITION or not record.candidate.facts.active:
            raise FinanceError("Only an active obligation chain can be enabled.")
        binding = self.binding(definition_id, scope)
        if scope == Scope.PORTAL_REVIEW and not self.store.accepted(definition_id, binding):
            raise FinanceError(
                "Exact-version auth-only acceptance is required before portal enablement."
            )
        self._confirm(f"Enable {definition_id} for {scope.value}", binding)
        self.refresh()
        if self.binding(definition_id, scope) != binding:
            raise FinanceError("Chain changed during enablement.")
        self.store.enable(definition_id, scope, binding, ctx.provenance().as_dict())

    def register_local(
        self, kind: Kind, facts: dict, *, context: RequestContext, object_id: str = ""
    ) -> Record:
        """Owner intake. Registration stays provisional until separately certified.

        A revision must name the immutable ID. Reissues retain an occurrence ID;
        unrelated evidence cannot silently claim the existing invoice reference.
        """
        ctx = self._owner(context)
        self.refresh()
        parsed = parse_facts(kind, facts)
        if object_id:
            old = self.store.get(object_id)
            if old.candidate.locator.system != "local_owner" or old.candidate.kind != kind:
                raise FinanceError("Only local intake objects may be revised here.")
            locator = old.candidate.locator
            if isinstance(parsed, Instance):
                prior = old.candidate.facts
                if (parsed.definition, parsed.occurrence) != (prior.definition, prior.occurrence):
                    raise FinanceError("Instance identity is immutable across revisions.")
                if (
                    prior.invoice_reference
                    and parsed.invoice_reference != prior.invoice_reference
                    and parsed.replaces_reference != prior.invoice_reference
                ):
                    raise FinanceError(
                        "Reissued evidence must explicitly replace the prior invoice reference."
                    )
        else:
            locator = Locator("local_owner", "", "", uuid4().hex)
        candidate = Candidate(kind, locator, parsed)
        self._graph(candidate)
        deps = self._deps(Record(object_id or "new", 0, candidate), Scope.IDENTITY)
        if isinstance(parsed, Instance):
            if not self.store.enabled(
                parsed.definition, Scope.IDENTITY, self.binding(parsed.definition)
            ):
                raise FinanceError("Instance intake requires an enabled certified definition.")
            self.store.check_reference(candidate, object_id)
        binding = digest((candidate.fingerprint, deps))
        self._confirm(
            f"Register provisional {kind.value} facts from owner-reviewed primary evidence; "
            "no payment or receipt binding",
            binding,
        )
        self.refresh()
        if self._deps(Record(object_id or "new", 0, candidate), Scope.IDENTITY) != deps:
            raise FinanceError("Intake dependencies changed during confirmation.")
        if object_id and self.store.get(object_id) != old:
            raise FinanceError("Intake revision changed during confirmation.")
        self.store.check_reference(candidate, object_id)
        record = self.store.register(candidate)
        self.store.audit(record.id, "owner_intake", ctx.provenance().as_dict())
        return record

    def _portal(self, record: Record) -> tuple[dict[str, str], ReviewProfile]:
        definition = record.candidate.facts
        account = self.store.get(definition.account).candidate.facts
        provider = self.store.get(account.provider).candidate.facts
        profile = self.profiles().get(provider.review_profile)
        if profile is None:
            raise FinanceError("Reviewed portal profile required.")
        profile.validate()
        if (
            origin(provider.portal_url) not in profile.allowed_origins
            or not account.username_ref
            or not account.password_ref
        ):
            raise FinanceError("Portal origin or credential references incomplete.")
        return {
            "portal_url": provider.portal_url,
            "portal_username_ref": account.username_ref,
            "portal_password_ref": account.password_ref,
            "login_username_selector": profile.login_username_selector,
            "login_password_selector": profile.login_password_selector,
            "login_submit_selector": profile.login_submit_selector,
            "auth_success_selector": profile.auth_success_selector,
            "auth_success_url_contains": profile.auth_success_url_contains,
            "allowed_origins": ",".join(profile.allowed_origins),
        }, profile

    def review_plan(
        self, definition_id: str, *, acceptance: bool = False
    ) -> tuple[dict[str, str], str]:
        self.refresh()
        record = self.store.get(definition_id)
        binding = self.binding(definition_id, Scope.PORTAL_REVIEW)
        if not record.candidate.facts.active or not self.store.enabled(
            definition_id, Scope.IDENTITY, self.binding(definition_id)
        ):
            raise FinanceError("Financial tracking chain disabled.")
        if not acceptance and (
            not self.store.enabled(definition_id, Scope.PORTAL_REVIEW, binding)
            or not self.store.accepted(definition_id, binding)
        ):
            raise FinanceError("Portal chain not accepted and enabled.")
        data, _ = self._portal(record)
        return data, binding

    def accept_auth(self, definition_id: str, binding: str, execution_id: str) -> None:
        # Internal call only after governed Act & Verify succeeds, including origin checks.
        _, current = self.review_plan(definition_id, acceptance=True)
        if current != binding:
            raise FinanceError("Portal chain changed during authentication acceptance.")
        self.store.accept_auth(definition_id, binding, execution_id)

    def tracked_instances(self) -> list[tuple[Record, str]]:
        self.refresh()
        result = []
        for record in self.store.records():
            if record.candidate.kind != Kind.INSTANCE:
                continue
            try:
                binding = self.binding(record.id)
                definition_id = record.candidate.facts.definition
                definition = self.store.get(definition_id)
                if definition.candidate.facts.active and self.store.enabled(
                    definition_id, Scope.IDENTITY, self.binding(definition_id)
                ):
                    result.append((record, binding))
            except FinanceError:
                continue
        return result

    @staticmethod
    def reviewed_summary(record: Record) -> dict:
        facts = asdict(record.candidate.facts)
        for name in ("identifier", "invoice_reference", "replaces_reference"):
            if name in facts:
                value = facts[name]
                facts[name] = "[masked]" if len(value) <= 4 else "…" + value[-4:]
        for name in ("username_ref", "password_ref", "portal_url"):
            if name in facts:
                facts[name] = "configured" if facts[name] else "absent"
        if "evidence" in facts:
            facts["evidence"] = [
                {"kind": e["kind"], "content_hash": e["content_hash"]} for e in facts["evidence"]
            ]
        return {
            "id": record.id,
            "kind": record.candidate.kind.value,
            "version": record.version,
            "fingerprint": record.candidate.fingerprint,
            "source": asdict(record.candidate.locator),
            "facts": facts,
        }

    def status(self) -> list[dict]:
        self.refresh()
        result = []
        for record in self.store.records():
            state = "draft"
            try:
                self.binding(record.id)
                state = "certified"
            except FinanceError:
                if record.version > 1 or record.invalid or self.store.has_history(record.id):
                    state = "needs_attention"
            result.append({**self.reviewed_summary(record), "state": state})
        return result
