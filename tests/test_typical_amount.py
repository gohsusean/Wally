"""Isolated advisory reviews: mocked Notion, Telegram and bank; no live effects."""

import json
from dataclasses import asdict, replace
from decimal import Decimal, localcontext

import pytest
import yaml

from tests.finance_fixture import ATTEST, PROOF
from tests.test_finance_catalog import NotionFixture
from tests.test_supervised_payments import FastBrowser
from wally.finance.config import load_sources
from wally.finance.models import FinanceError, Kind, Scope, digest
from wally.finance.notion import FieldMapping
from wally.finance.payment_demo import build_demo, prepare_card
from wally.finance.payments import CertifiedPaymentSource
from wally.finance.typical_amount import TypicalAmount, comparable_period
from wally.models.ops import ExecutionStatus, ProposalStatus


@pytest.mark.parametrize(
    "amount,warning,variance",
    [
        ("100.00", False, "equal to typical"),
        ("109.99", False, "9.99% above"),
        ("90.01", False, "9.99% below"),
        ("110.00", True, "10.00% above"),
        ("90.00", True, "10.00% below"),
        ("110.01", True, "10.01% above"),
        ("89.99", True, "10.01% below"),
    ],
)
def test_decimal_threshold_includes_exactly_ten_percent(amount, warning, variance):
    with localcontext() as context:
        context.prec = 3  # Unrelated caller precision cannot change the decision.
        notice = TypicalAmount("100.00", "MYR").notice(amount, "MYR")
    assert ("⚠" in notice) is warning
    assert variance in notice
    assert "Typical Amount: MYR 100.00" in notice
    assert f"Proposed amount: MYR {amount}" in notice


def test_blank_skips_comparison():
    assert TypicalAmount().notice("100.00", "MYR") == ""


@pytest.mark.parametrize("amount,direction", [("0.33", "above"), ("0.27", "below")])
def test_subunit_decimal_threshold_is_exact(amount, direction):
    notice = TypicalAmount("0.30", "MYR").notice(amount, "MYR")
    assert "⚠" in notice
    assert f"10.00% {direction}" in notice


@pytest.mark.parametrize("baseline", ["0", "-1", "invalid", "NaN", "Infinity", "1e999999"])
def test_invalid_baselines_are_unavailable(baseline):
    notice = TypicalAmount(baseline, "MYR").notice("100.00", "MYR")
    assert "comparison unavailable" in notice
    assert "⚠" not in notice
    assert baseline not in notice


def test_currency_mismatch_is_unavailable():
    assert "comparison unavailable" in TypicalAmount("100", "USD").notice("100", "MYR")


@pytest.mark.parametrize(
    "frequency,start,end,expected",
    [
        ("quarterly", "2026-10-01", "2026-12-31", True),
        ("quarterly", "2026-10-01", "2026-10-31", False),
        ("quarterly", "2026-10-01", "2026-11-30", False),
        ("quarterly", "2026-11-01", "2027-01-31", False),
        ("monthly", "2027-01-01", "2027-01-31", True),
        ("monthly", "2027-01-01", "2027-03-31", False),
        ("annual", "2026-01-01", "2026-12-31", True),
    ],
)
def test_typical_amount_requires_comparable_billing_periods(frequency, start, end, expected):
    assert comparable_period(frequency, start, end) is expected
    if not expected:
        notice = TypicalAmount("960.96", "MYR", comparable=False).notice("320.32", "MYR")
        assert "periods are not comparable" in notice
        assert "warning" not in notice


@pytest.fixture
def world(tmp_path):
    state = build_demo(tmp_path, browser=FastBrowser())
    yield state
    state.notion.client.close()


@pytest.mark.parametrize(
    "baseline,expected",
    [
        ("90.00", "11.11% above"),
        ("125.00", "20.00% below"),
        ("0", "comparison unavailable"),
        ("-1", "comparison unavailable"),
        ("invalid", "comparison unavailable"),
        ("", None),
    ],
)
def test_card_remains_approvable_and_pays_invoice_amount(world, baseline, expected):
    world.source.typical_amount = TypicalAmount(baseline, "MYR", "retained-source-binding")
    proposal, callback = prepare_card(world)
    card = world.transport.sent[0]
    body = str(card["text"])
    if expected:
        assert expected in body
    else:
        assert "Typical Amount" not in body
    if baseline in {"90.00", "125.00"}:
        assert "Amount variance warning (advisory only)" in body
        assert f"Typical Amount: MYR {baseline}" in body
        assert "Proposed amount: MYR 100.00" in body
    assert card["buttons"][0][0]["text"] == "Approve"
    _, spec, bill, quote = world.payments.load(proposal.id)
    assert bill.amount == "100.00"
    assert quote.bill_digest == digest(asdict(bill))
    assert spec["typical_amount_review"]["baseline"]["amount"] == baseline
    assert world.browser.submissions == 0
    world.telegram.handle_update(callback)
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.APPROVED
    assert world.store.list_executions()[0].status == ExecutionStatus.VERIFIED_SUCCESS
    world.telegram.handle_update({**callback, "update_id": 2})
    assert world.browser.submissions == world.notion.creates == 1


@pytest.mark.parametrize(
    "before,after",
    [
        (TypicalAmount(), TypicalAmount("90", "MYR", "new")),
        (TypicalAmount("90", "MYR", "old"), TypicalAmount()),
        (TypicalAmount("90", "MYR", "old"), TypicalAmount("91", "MYR", "new")),
        # Same warning/rounded percentage is insufficient; exact baseline is bound.
        (TypicalAmount("90", "MYR", "old"), TypicalAmount("90.001", "MYR", "new")),
        (TypicalAmount("90", "MYR", "old"), TypicalAmount("90", "USD", "new")),
        (TypicalAmount("90", "MYR", "old"), TypicalAmount("90", "MYR", "new-mapping")),
        (TypicalAmount("invalid", "MYR", "old"), TypicalAmount("invalid", "MYR", "new")),
    ],
)
def test_baseline_drift_requires_fresh_review_before_secrets(world, before, after):
    world.source.typical_amount = before
    proposal, callback = prepare_card(world)
    world.source.typical_amount = after
    world.telegram.handle_update(callback)
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.PROPOSED
    assert not world.secrets.resolve_calls
    assert not world.store.list_executions()
    assert world.browser.submissions == 0


def test_baseline_drift_after_login_stops_submission(world):
    world.source.typical_amount = TypicalAmount("90", "MYR", "old")
    world.browser.on_fill = lambda: setattr(
        world.source, "typical_amount", TypicalAmount("100", "MYR", "new")
    )
    _, callback = prepare_card(world)
    world.telegram.handle_update(callback)
    assert world.browser.submissions == 0
    assert world.store.list_executions()[0].outcome == "not_submitted"


def test_rejected_stale_card_can_be_replaced_with_a_fresh_review(world):
    proposal, callback = prepare_card(world)
    world.source.typical_amount = TypicalAmount("90", "MYR", "new")
    callback["callback_query"]["data"] = callback["callback_query"]["data"][:-2] + ".r"
    world.telegram.handle_update(callback)
    assert world.store.get_proposal(proposal.id).status == ProposalStatus.REJECTED
    new = world.payments.prepare(
        world.source.bill.instance_id, context=world.authority.issue("cli")
    )
    assert new.fingerprint != proposal.fingerprint
    world.payments._current(new.id)
    with pytest.raises(FinanceError, match="changed"):
        world.payments._current(proposal.id)
    assert world.browser.submissions == 0


def test_warning_is_bound_to_review_fingerprint(world):
    proposal, _ = prepare_card(world)
    with world.store._connect() as connection:
        spec = json.loads(connection.execute("SELECT spec FROM payment_specs").fetchone()[0])
        spec["typical_amount_review"]["notice"] = "No warning"
        connection.execute("UPDATE payment_specs SET spec=?", (json.dumps(spec),))
    with pytest.raises(FinanceError, match="modified"):
        world.payments.load(proposal.id)


@pytest.fixture
def notion_source(tmp_path):
    fixture = NotionFixture()
    index = next(i for i, s in enumerate(fixture.sources) if s.kind == Kind.DEFINITION)
    source = fixture.sources[index]
    fixture.sources[index] = replace(
        source, payment_review_fields={"typical_amount": FieldMapping("qhcR", "number", True)}
    )
    schema = fixture.schemas[source.data_source_id]["properties"]
    schema["Renamed typical label"] = {"id": "qhcR", "type": "number"}
    page = fixture.page(Kind.DEFINITION)
    page["properties"]["Renamed typical label"] = {
        "id": "qhcR",
        "type": "number",
        "number": 90.01,
    }
    # A misleading display name must never win over the stable mapped ID.
    page["properties"]["Typical Amount"] = {"id": "unrelated", "type": "number", "number": 999}
    catalog = fixture.catalog(tmp_path)
    records = catalog.refresh()
    for record in records:
        catalog.certify(
            record.id,
            Scope.IDENTITY,
            evidence=PROOF,
            attestations=ATTEST,
            context=catalog.authority.issue("cli"),
        )
    definition = next(r for r in records if r.candidate.kind == Kind.DEFINITION)
    catalog.enable(definition.id, Scope.IDENTITY, context=catalog.authority.issue("cli"))
    instance = catalog.register_local(
        Kind.INSTANCE,
        {
            "definition": definition.id,
            "occurrence": "2027-01",
            "stage": "issued",
            "currency": "MYR",
            "amount": "100.00",
            "due_date": "2027-01-20",
            "invoice_reference": "INV2027",
            "amount_basis": "invoice_total",
            "evidence": [
                {
                    "source": "primary_document",
                    "reference": "fixture:invoice",
                    "content_hash": "a" * 64,
                    "kind": "invoice",
                }
            ],
        },
        context=catalog.authority.issue("cli"),
    )
    yield fixture, catalog, instance, source, page
    fixture.client.close()


def test_exact_instance_relation_and_stable_property_mapping(notion_source):
    fixture, catalog, instance, _, _ = notion_source
    baseline = CertifiedPaymentSource(catalog).read_typical_amount(instance.id)
    assert Decimal(baseline.amount) == Decimal("90.01")
    assert baseline.currency == "MYR"
    assert len(baseline.binding) == 64
    assert fixture.calls[-1][1].endswith(fixture.page(Kind.DEFINITION)["id"])


@pytest.mark.parametrize("value", [None, 0, -1, "bad", True, 109.99])
def test_notion_values_remain_advisory_without_invalidating_identity(notion_source, value):
    fixture, catalog, instance, _, page = notion_source
    before = catalog.store.get(instance.candidate.facts.definition).candidate.fingerprint
    page["properties"]["Renamed typical label"]["number"] = value
    source = CertifiedPaymentSource(catalog)
    baseline = source.read_typical_amount(instance.id)
    notice = baseline.notice("100.00", "MYR")
    if value is None:
        assert notice == ""
    elif type(value) not in (int, float) or value <= 0:
        assert "comparison unavailable" in notice
    else:
        assert "Typical Amount" in notice
    catalog.refresh()
    assert catalog.store.get(instance.candidate.facts.definition).candidate.fingerprint == before
    # Invalid -> different invalid also changes the review binding.
    page["properties"]["Renamed typical label"]["number"] = "other-invalid"
    assert source.read_typical_amount(instance.id).binding != baseline.binding


@pytest.mark.parametrize("drift", ["recreated", "type", "parent", "identity"])
def test_notion_structural_drift_fails_closed(notion_source, drift):
    fixture, catalog, instance, source, page = notion_source
    schema = fixture.schemas[source.data_source_id]["properties"]["Renamed typical label"]
    if drift == "recreated":
        schema["id"] = "new-id"
    elif drift == "type":
        schema["type"] = "rich_text"
    elif drift == "parent":
        page["parent"]["data_source_id"] = source.database_id
    else:
        request = catalog.reader.request
        catalog.reader.request = lambda method, path, **kwargs: (
            {**request(method, path, **kwargs), "id": source.database_id}
            if path.startswith("/pages/")
            else request(method, path, **kwargs)
        )
    with pytest.raises(FinanceError):
        CertifiedPaymentSource(catalog).read_typical_amount(instance.id)


def test_review_mapping_is_additive_and_validated(notion_source, tmp_path):
    fixture, _, _, original, _ = notion_source
    mapped = fixture.sources[-1]
    assert mapped.fingerprint == original.fingerprint
    mapped.validate()
    for bad in (
        replace(mapped, kind=Kind.ACCOUNT),
        replace(mapped, payment_review_fields={"typical_amount": FieldMapping("x", "rich_text")}),
        replace(
            mapped, payment_review_fields={"typical_amount": FieldMapping("id-account", "number")}
        ),
    ):
        with pytest.raises(FinanceError):
            bad.validate()
    path = tmp_path / "finance.yaml"
    raw = {**asdict(mapped), "kind": mapped.kind.value}
    path.write_text(yaml.safe_dump({"schema_version": 1, "sources": [raw]}))
    assert load_sources(path)[0] == mapped
