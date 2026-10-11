"""Evidence review worklists over the existing catalog; never certification authority.

A review is a field-to-primary-evidence index, bound to an exact candidate. It
contains no copied facts or document text. Only FinanceService.certify can certify.
"""

from __future__ import annotations

from dataclasses import asdict

from wally.finance.models import Account, Definition, Evidence, FinanceError, Kind, Scope
from wally.finance.store import Record

CHECKS = {
    Kind.PROPERTY: {"address", "unit"},
    Kind.ENTITY: {"entity_key"},
    Kind.PROVIDER: {"issuer"},
    Kind.ACCOUNT: {
        "provider",
        "subject",
        "subject_kind",
        "namespace",
        "identifier",
        "responsibility",
    },
    Kind.DEFINITION: {
        "account",
        "charge_key",
        "obligation_type",
        "currency",
        "amount_policy",
        "frequency",
        "evidence_mode",
        "due_mode",
        "active",
    },
}


def required_checks(record: Record, store) -> set[str]:
    facts = record.candidate.facts
    result = set(CHECKS.get(record.candidate.kind, ()))
    if record.candidate.review_fingerprint:
        result.add("source_review")
    # Optional material fields are required only when configured. Occurrences are
    # deliberately outside the recurring-baseline report.
    for key, value in asdict(facts).items():
        if value not in (None, "", (), []) and key not in {"timezone", "evidence"}:
            result.add(key)
    if isinstance(facts, Definition):
        result.add("timezone")
        account = store.get(facts.account).candidate.facts
        if isinstance(account, Account) and account.namespace == "credit_card_last4":
            result |= {"statement_cycle", "payment_arrangement"}
            # A source document can establish that arrangement is unspecified;
            # do not infer auto debit, minimum payment or full-balance selection.
    return result


def validate_review(record: Record, store, review: dict) -> dict:
    try:
        if not isinstance(review, dict) or set(review) != {
            "fingerprint",
            "evidence",
            "checks",
            "discrepancies",
            "owner_decisions",
        }:
            raise ValueError
        if review["fingerprint"] != record.candidate.fingerprint:
            raise FinanceError("Review is stale; compare current facts again.")
        proofs = [asdict(Evidence.parse(e)) for e in review["evidence"]]
        checks = review["checks"]
        required = required_checks(record, store)
        if not isinstance(checks, dict) or set(checks) - required:
            raise ValueError
        for indices in checks.values():
            if (
                not isinstance(indices, list)
                or not indices
                or len(set(indices)) != len(indices)
                or any(type(i) is not int or not 0 <= i < len(proofs) for i in indices)
            ):
                raise ValueError
        for key in ("discrepancies", "owner_decisions"):
            values = review[key]
            if not isinstance(values, list) or any(v not in required for v in values):
                raise ValueError
        return {
            "evidence": proofs,
            "missing_checks": sorted(required - checks.keys()),
            "discrepancies": sorted(set(review["discrepancies"])),
            "owner_decisions": sorted(set(review["owner_decisions"])),
        }
    except (TypeError, KeyError, ValueError) as error:
        if isinstance(error, FinanceError):
            raise
        raise FinanceError("Invalid field evidence review.") from None


def coverage(catalog, reviews: dict | None = None) -> dict:
    """Refresh first; retain invalid/unreadable rows and source-level exceptions.

    Reviews are advisory. A complete checklist is still uncertified until the
    existing authenticated owner service creates a current identity certificate.
    """
    return summarize_coverage(catalog.status(), catalog.store, reviews)


def summarize_coverage(statuses: list[dict], store, reviews: dict | None = None) -> dict:
    """Summarize an already refreshed snapshot without another provider read."""
    reviews = {} if reviews is None else reviews
    if not isinstance(reviews, dict):
        raise FinanceError("Reviews must be keyed by canonical object ID.")
    rows, exceptions = [], []
    for status in statuses:
        if status["kind"] not in {Kind.PROVIDER, Kind.ACCOUNT, Kind.DEFINITION}:
            continue
        if "id" not in status:
            if not isinstance(status["source"], dict):
                exceptions.append({"kind": status["kind"], "reason": status["reason"]})
                continue
            rows.append({**status, "coverage": "unresolved_discrepancy", "missing_evidence": True})
            continue
        record = store.get(status["id"])
        review = reviews.get(record.id)
        details = {
            "missing_checks": sorted(required_checks(record, store)),
            "discrepancies": [],
            "owner_decisions": [],
        }
        review_state = "not_reviewed"
        if review is not None:
            try:
                details = validate_review(record, store, review)
                review_state = "reviewed"
            except FinanceError:
                review_state = "stale_or_invalid"
        certified = status["state"] == "certified"
        if certified:
            certificate = store.certificate(record.id, Scope.IDENTITY)
            # Reuse existing certified evidence; don't force repeat checklists.
            missing = not bool(certificate and certificate["evidence"])
            details = {"missing_checks": [], "discrepancies": [], "owner_decisions": []}
            state = "certified_primary_evidence"
        else:
            missing = bool(details["missing_checks"])
            state = (
                "unresolved_discrepancy"
                if record.invalid or details["discrepancies"] or review_state == "stale_or_invalid"
                else "reviewed_not_certified"
                if review_state == "reviewed"
                else "missing_supporting_evidence"
            )
        rows.append(
            {
                **status,
                "coverage": state,
                "review_state": review_state,
                "missing_evidence": missing,
                **details,
            }
        )
    totals = {
        "total": len(rows),
        "certified": sum(r["coverage"] == "certified_primary_evidence" for r in rows),
        "missing_evidence": sum(r["missing_evidence"] for r in rows),
        "owner_decisions": sum(bool(r.get("owner_decisions")) for r in rows),
    }
    totals["uncertified"] = totals["total"] - totals["certified"]
    totals["complete"] = (
        not totals["uncertified"]
        and not totals["missing_evidence"]
        and not exceptions
        and bool(rows)
    )
    return {
        "totals": totals,
        "inventory_complete": not exceptions,
        "by_kind": {
            k.value: sum(r["kind"] == k for r in rows)
            for k in (Kind.PROVIDER, Kind.ACCOUNT, Kind.DEFINITION)
        },
        "exceptions": exceptions,
        "records": rows,
    }


def reviewed_proof(catalog, object_id: str, reviews: dict) -> dict:
    """Prepare inputs for the existing certify call; no approval or state mutation."""
    catalog.refresh()
    record = catalog.store.get(object_id)
    if record.candidate.kind not in CHECKS:
        raise FinanceError("Recurring-baseline review does not certify occurrences.")
    details = validate_review(record, catalog.store, reviews.get(object_id))
    if details["missing_checks"] or details["discrepancies"] or details["owner_decisions"]:
        raise FinanceError("Review has missing evidence or unresolved decisions.")
    return {"evidence": details["evidence"], "reviewed_fingerprint": record.candidate.fingerprint}
