"""Owner payment selection is non-executable and cannot smuggle credentials."""

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from wally.finance.config import load_payment_preferences, load_profiles, load_sources
from wally.finance.models import FinanceError


def preferences():
    return {
        "default_transfer_bank": "maybank",
        "transfer_login_url": "https://www.maybank2u.com.my/home/m2u/common/login.do",
        "first_bill_account": "fin_account",
        "first_bill_definition": "fin_definition",
        "first_bill_period_start": "2026-10-01",
        "first_bill_period_end": "2026-12-31",
    }


def test_owner_choice_is_retained_without_rewriting_source_mappings():
    path = Path(__file__).resolve().parents[1] / "config" / "finance.yaml"
    selected = load_payment_preferences(path)
    assert selected.default_transfer_bank == "maybank"
    assert selected.first_bill_period_start == "2026-10-01"
    assert selected.first_bill_period_end == "2026-12-31"
    assert selected.first_bill_account == "fin_d692b4b201b84448b7badf9c7ccbe5f9"
    assert len(selected.fingerprint) == 64
    assert load_sources(path)
    assert load_profiles(path) == {}
    assert not {"amount", "recipient", "password", "execute", "approval"} & set(asdict(selected))


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "synthetic-canary"},
        {"execute": True},
        {"approved": True},
        {"default_transfer_bank": "unknown-bank"},
        {"transfer_login_url": "https://attacker.example/login"},
        {"transfer_login_url": "https://www.maybank2u.com.my/home/m2u/common/login.do?token=bad"},
        {"first_bill_period_end": "2026-09-30"},
        {"first_bill_period_start": "2026-99-99"},
    ],
)
def test_malformed_routing_intent_never_selects_another_route(tmp_path, changes):
    path = tmp_path / "finance.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "payment_preferences": {**preferences(), **changes}})
    )
    with pytest.raises(FinanceError, match="Invalid owner payment preferences"):
        load_payment_preferences(path)


def test_old_finance_configuration_remains_compatible(tmp_path):
    path = tmp_path / "finance.yaml"
    path.write_text("schema_version: 1\nsources: []\nprofiles: []\n")
    assert load_payment_preferences(path) is None
