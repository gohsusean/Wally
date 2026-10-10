"""Presentation tests use synthetic records only; transport tests never send messages."""

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from tests.test_telegram_notion import callback, cards, propose, world
from wally.models.ops import ExecutionStatus
from wally.presentation import human_time
from wally.telegram.client import BotClient
from wally.telegram.notion_approvals import execution_outcome


@pytest.mark.parametrize(
    "instant,expected",
    [
        ("2026-10-10T17:22:45.123456+00:00", "11 Oct 2026, 1:22 am"),
        ("2026-10-10T15:59:59.999999+00:00", "10 Oct 2026, 11:59 pm"),
        ("2026-10-10T16:00:00+00:00", "11 Oct 2026, 12:00 am"),
        ("2026-10-11T04:00:00+00:00", "11 Oct 2026, 12:00 pm"),
    ],
)
def test_myt_conversion_preserves_input_precision(instant, expected):
    parsed = datetime.fromisoformat(instant)
    assert human_time(parsed) == expected
    assert datetime.fromisoformat(instant) == parsed


def test_relative_expiry_uses_local_day_and_rejects_ambiguous_naive_time():
    now = datetime(2026, 10, 10, 15, 59, tzinfo=UTC)
    assert human_time("2026-10-10T16:01:00+00:00", now=now) == "tomorrow at 12:01 am"
    now = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
    assert human_time("2026-10-10T17:22:00+00:00", now=now) == "today at 1:22 am"
    with pytest.raises(ValueError):
        human_time(datetime(2026, 10, 11))


def test_explicit_other_timezone_keeps_its_label():
    instant = "2026-10-10T17:22:45.123456+00:00"
    assert human_time(instant, timezone=ZoneInfo("UTC")) == "10 Oct 2026, 5:22 pm UTC"
    assert human_time(instant, timezone=ZoneInfo("Asia/Kuching")) == "11 Oct 2026, 1:22 am"


def test_simple_card_buttons_and_no_visible_ids(tmp_path):
    state = world(tmp_path)
    proposal = propose(state)
    card = cards(state)[0]
    assert card["text"].startswith("📝 Notion update\n\nSandbox 0\n\nAmount policy")
    assert "Fixed contract → From source" in card["text"]
    assert "Expires today at" in card["text"] or "Expires tomorrow at" in card["text"]
    assert "MYT" not in card["text"]
    assert all(
        value not in card["text"]
        for value in [
            proposal.id,
            proposal.fingerprint,
            state[4][0].targets[0].page_id,
            "property_id",
            "UTC",
            "authoriz",
            "certif",
            "2026-",
        ]
    )
    assert [len(row) for row in card["buttons"]] == [2, 2]
    assert max(len(button["text"]) for row in card["buttons"] for button in row) <= 14
    assert card["buttons"][1][1]["url"].startswith("https://www.notion.so/")


def test_complete_batch_and_long_names_and_values_are_never_abbreviated(tmp_path):
    state = world(tmp_path)
    first = replace(state[4][0].targets[0], key="Long record name " * 40)
    state[4][0] = replace(
        state[4][0],
        targets=(first, state[4][0].targets[1]),
        telegram_targets=(first.key, "Sandbox 1"),
    )
    propose(state, 0)
    propose(state, 1)
    batch = next(card for card in cards(state) if card["text"].startswith("📝 Notion updates"))
    assert first.key in batch["text"] and "Sandbox 1" in batch["text"]
    assert batch["text"].count("Fixed contract → From source") == 2
    text = state[3]._text(
        [
            {
                "target": first.key,
                "changes": [{"field": "frequency", "before": "old " * 100, "after": "new " * 100}],
            }
        ]
    )
    assert "old " * 100 in text and "new " * 100 in text


def test_financial_warning_is_material_and_sandbox_has_no_disclaimer(tmp_path):
    state = world(tmp_path)
    propose(state)
    assert "recertification" not in cards(state)[0]["text"]
    target = replace(state[4][0].targets[0], finance_id="synthetic-finance")
    state[4][0] = replace(state[4][0], targets=(target,))
    scope = [
        {
            "target": target.key,
            "changes": [{"field": "frequency", "before": "monthly", "after": "quarterly"}],
        }
    ]
    assert "⚠️ This change will require recertification." in state[3]._text(scope)


def test_bot_client_serializes_multiple_keyboard_rows_and_flat_legacy_buttons():
    client = BotClient("synthetic")
    payloads = []
    client._post = lambda method, payload: payloads.append(payload) or {"result": {"message_id": 1}}
    rows = [
        [{"text": "Apply", "callback_data": "synthetic.e"}],
        [{"text": "View", "url": "https://www.notion.so/synthetic"}],
    ]
    client.send_message("42", "Synthetic", rows)
    assert payloads[-1]["reply_markup"]["inline_keyboard"] == rows
    client.send_message("42", "Legacy", rows[0])
    assert payloads[-1]["reply_markup"]["inline_keyboard"] == [rows[0]]


def test_verified_result_and_awaiting_execution_are_distinct(tmp_path):
    state = world(tmp_path)
    proposal = propose(state)
    assert "Awaiting execution" in state[3].completion(proposal.id)
    state[1].handle_update(callback(state))
    result = next(
        message["text"]
        for message in state[2].sent
        if message["text"].startswith("✅ Notion updated")
    )
    assert "Sandbox 0" in result and "Verified successfully." in result
    assert proposal.id not in result and proposal.fingerprint not in result
    assert "Fixed contract → From source" in result


@pytest.mark.parametrize(
    "category", ["patch_outcome_uncertain", "verification_mismatch", "verification_unavailable"]
)
def test_uncertain_outcome_never_success_or_raw_error(category):
    from types import SimpleNamespace

    heading, body = execution_outcome(
        SimpleNamespace(status=ExecutionStatus.EXECUTED_UNVERIFIED, failure_category=category)
    )
    assert "uncertain" in heading and "Do not apply it again" in body
    assert category not in body and "success" not in body
