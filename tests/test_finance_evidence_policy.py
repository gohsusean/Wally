"""Evidence certification is independent of action authorization (ADR-050)."""

import copy
import hashlib

import pytest

from tests.test_finance_catalog import NotionFixture
from tests.test_finance_coverage import review
from tests.test_finance_isolation import record, set_text
from wally.finance.coverage import coverage
from wally.finance.models import FinanceError, Kind, ReadIssue, Scope
from wally.finance.notion import ReadSnapshot


def documented_review(item, catalog, tmp_path):
    path = tmp_path / "primary.txt"
    path.write_text("Isolated primary fixture evidence")
    result = copy.deepcopy(review(item, catalog.store))
    result["evidence"][0] = {
        "source": "primary fixture document",
        "reference": str(path),
        "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "kind": "account_document",
    }
    return result


def test_complete_evidence_certifies_without_owner_or_dependency_certificates(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    account = record(catalog, fixture.page(Kind.ACCOUNT))
    entry = documented_review(account, catalog, tmp_path)
    catalog.approval.answer = False
    report = coverage(catalog, {account.id: entry})
    assert report["totals"]["certified"] == 1
    certificate = catalog.store.certificate(account.id, Scope.IDENTITY)
    assert certificate["attestations"] == {}
    assert certificate["provenance"]["policy"] == "ADR-050"
    assert not catalog.approval.prompts
    # Independent record certification does not make an uncertified chain usable.
    with pytest.raises(FinanceError):
        catalog.binding(account.id)
    assert catalog.store.certificate(account.id, Scope.PORTAL_REVIEW) is None


def test_outage_preserves_certificate_but_blocks_actions_and_material_edit_invalidates(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    account = record(catalog, fixture.page(Kind.ACCOUNT))
    entry = documented_review(account, catalog, tmp_path)
    coverage(catalog, {account.id: entry})
    certificate = catalog.store.certificate(account.id, Scope.IDENTITY)
    original = catalog.reader.read_all
    catalog.reader.read_all = lambda *args: ReadSnapshot(
        [], [ReadIssue(s.kind, s.key, reason="source_invalid") for s in fixture.sources]
    )
    assert coverage(catalog)["totals"]["certified"] == 1
    assert catalog.store.certificate(account.id, Scope.IDENTITY) == certificate
    with pytest.raises(FinanceError):
        catalog.binding(account.id)
    catalog.reader.read_all = original
    assert coverage(catalog)["totals"]["certified"] == 1
    set_text(fixture.page(Kind.ACCOUNT), "identifier", "changed-identifier")
    assert coverage(catalog, {account.id: entry})["totals"]["certified"] == 0
    assert catalog.store.certificate(account.id, Scope.IDENTITY) is None
    assert not catalog.approval.prompts


def test_incomplete_stale_or_changed_evidence_cannot_certify(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    item = record(catalog, fixture.page(Kind.PROPERTY))
    entry = documented_review(item, catalog, tmp_path)
    entry["checks"].pop("unit")
    assert coverage(catalog, {item.id: entry})["totals"]["certified"] == 0
    entry["checks"]["unit"] = [0]
    entry["fingerprint"] = "wrong"
    assert coverage(catalog, {item.id: entry})["totals"]["certified"] == 0
    entry["fingerprint"] = item.candidate.fingerprint
    (tmp_path / "primary.txt").write_text("Changed evidence")
    assert coverage(catalog, {item.id: entry})["totals"]["certified"] == 0


def test_partial_provider_outage_does_not_erase_independently_verified_accounts(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    account = record(catalog, fixture.page(Kind.ACCOUNT))
    entry = documented_review(account, catalog, tmp_path)
    coverage(catalog, {account.id: entry})
    cert = catalog.store.certificate(account.id, Scope.IDENTITY)
    request = catalog.reader.request
    provider_source = fixture.sources[1]

    def unavailable(method, path, **kwargs):
        if path == f"/data_sources/{provider_source.data_source_id}/query":
            raise FinanceError("Temporary source outage")
        return request(method, path, **kwargs)

    catalog.reader.request = unavailable
    assert coverage(catalog)["totals"]["certified"] == 1
    assert catalog.store.certificate(account.id, Scope.IDENTITY) == cert
    with pytest.raises(FinanceError):
        catalog.binding(account.id)
    catalog.reader.request = request
    assert coverage(catalog)["totals"]["certified"] == 1


def test_changed_retained_evidence_revokes_current_verification_and_report(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    item = record(catalog, fixture.page(Kind.PROVIDER))
    entry = documented_review(item, catalog, tmp_path)
    coverage(catalog, {item.id: entry})
    assert catalog.store.certificate(item.id, Scope.IDENTITY)
    (tmp_path / "primary.txt").write_text("Contradictory replacement")
    assert coverage(catalog, {item.id: entry})["totals"]["certified"] == 0
    assert catalog.store.certificate(item.id, Scope.IDENTITY) is None


def test_exception_preserves_evidence_but_keeps_source_unavailable_for_actions(tmp_path):
    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    item = record(catalog, fixture.page(Kind.PROVIDER))
    entry = documented_review(item, catalog, tmp_path)
    coverage(catalog, {item.id: entry})
    cert = catalog.store.certificate(item.id, Scope.IDENTITY)
    read = catalog.reader.read_all

    def unavailable(*args):
        raise FinanceError("Temporary transport failure")

    catalog.reader.read_all = unavailable
    with pytest.raises(FinanceError):
        coverage(catalog)
    assert catalog.store.certificate(item.id, Scope.IDENTITY) == cert
    with pytest.raises(FinanceError):
        catalog.binding(item.id)
    catalog.reader.read_all = read
    assert coverage(catalog)["totals"]["certified"] == 1


def test_individual_relation_read_failure_preserves_verification(tmp_path):
    from wally.finance.notion import SourceUnavailable

    fixture = NotionFixture()
    catalog = fixture.catalog(tmp_path)
    catalog.refresh()
    account = record(catalog, fixture.page(Kind.ACCOUNT))
    entry = documented_review(account, catalog, tmp_path)
    coverage(catalog, {account.id: entry})
    cert = catalog.store.certificate(account.id, Scope.IDENTITY)
    request = catalog.reader.request
    provider_page = fixture.page(Kind.PROVIDER)["id"]

    def unavailable(method, path, **kwargs):
        if path == f"/pages/{provider_page}":
            raise SourceUnavailable("Individual relation unavailable")
        return request(method, path, **kwargs)

    catalog.reader.request = unavailable
    assert coverage(catalog)["totals"]["certified"] == 1
    assert catalog.store.certificate(account.id, Scope.IDENTITY) == cert
    with pytest.raises(FinanceError):
        catalog.binding(account.id)
    catalog.reader.request = request
    assert coverage(catalog)["totals"]["certified"] == 1
