"""Owner-certified, isolated D03 fixtures; never uses live Notion or secrets."""

from dataclasses import asdict

from wally.finance.models import Kind, ReviewProfile, Scope
from wally.finance.service import ATTESTATIONS, FinanceService
from wally.finance.store import FinanceStore
from wally.knowledge.registry import KnowledgeRegistry

PROOF = [
    {
        "source": "primary_document",
        "reference": "fixture:primary:1",
        "content_hash": "a" * 64,
        "kind": "contract",
    }
]
ATTEST = dict.fromkeys(ATTESTATIONS, True)


class Approve:
    def __init__(self):
        self.prompts = []
        self.on_prompt = None
        self.answer = True

    def request_approval(self, summary, *, action_class):
        self.prompts.append(summary)
        if self.on_prompt:
            self.on_prompt()
        return self.answer


def catalog_fixture(tmp_path, authority, *, portal=True):
    profiles = {
        "utility": ReviewProfile(
            "utility",
            "1",
            ("https://portal.acme.example",),
            "#user",
            "#pass",
            "#submit",
            "#account-summary",
        )
    }
    catalog = FinanceService(
        FinanceStore(tmp_path / "finance.db"),
        authority=authority,
        approval=Approve(),
        registry=KnowledgeRegistry(tmp_path / "registry.db"),
        profiles=lambda: profiles,
    )
    context = authority.issue("cli")

    def add(kind, facts):
        record = catalog.register_local(kind, facts, context=context)
        catalog.certify(
            record.id, Scope.IDENTITY, evidence=PROOF, attestations=ATTEST, context=context
        )
        return record

    prop = add(Kind.PROPERTY, {"address": "Fixture property", "unit": "001"})
    provider = add(
        Kind.PROVIDER,
        {
            "issuer": "acme-power",
            "portal_url": "https://portal.acme.example/login",
            "review_profile": "utility",
        },
    )
    account = add(
        Kind.ACCOUNT,
        {
            "provider": provider.id,
            "subject": prop.id,
            "subject_kind": "property",
            "namespace": "electricity-customer",
            "identifier": "000123456789",
            "responsibility": "owner",
            "username_ref": "op://Personal/AcmePower/username",
            "password_ref": "op://Personal/AcmePower/password",
        },
    )
    definition = add(
        Kind.DEFINITION,
        {
            "account": account.id,
            "charge_key": "electricity",
            "obligation_type": "utility",
            "currency": "MYR",
            "amount_policy": "source_defined",
            "frequency": "monthly",
            "evidence_mode": "issued_bill",
            "due_mode": "from_document",
            "active": True,
        },
    )
    catalog.enable(definition.id, Scope.IDENTITY, context=context)
    instance = add(
        Kind.INSTANCE,
        {
            "definition": definition.id,
            "occurrence": "2026-08",
            "stage": "issued",
            "currency": "MYR",
            "amount": "100.00",
            "due_date": "2026-08-20",
            "invoice_reference": "INV001",
            "amount_basis": "invoice_total",
            "evidence": [{**PROOF[0], "kind": "invoice"}],
        },
    )
    if portal:
        catalog.certify(
            definition.id, Scope.PORTAL_REVIEW, evidence=PROOF, attestations=ATTEST, context=context
        )
        # Existing Act tests start after acceptance. Dedicated D03 tests exercise real acceptance.
        binding = catalog.binding(definition.id, Scope.PORTAL_REVIEW)
        catalog.store.accept_auth(definition.id, binding, "fixture-auth-check")
        catalog.enable(definition.id, Scope.PORTAL_REVIEW, context=context)
    return (
        catalog,
        {
            "property": prop.id,
            "provider": provider.id,
            "account": account.id,
            "definition": definition.id,
            "instance": instance.id,
        },
        profiles,
    )


def revise(catalog, object_id, **changes):
    record = catalog.store.get(object_id)
    facts = {**asdict(record.candidate.facts), **changes}
    return catalog.register_local(
        record.candidate.kind, facts, object_id=object_id, context=catalog.authority.issue("cli")
    )
