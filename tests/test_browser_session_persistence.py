"""Browser session persistence across WAIT_FOR_USER."""

from unittest.mock import patch

import pytest

from wally.adapters.browser.recording import RecordingBrowserAdapter
from wally.exceptions import ProviderUnavailableError
from wally.models.browser import BrowserActionType
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.browser_session_store import BrowserSessionStore


def _bill_with_portal() -> dict:
    return {
        "provider": "Streaming Co",
        "payment_method": "card_portal",
        "payment_portal_url": "https://pay.example.com/streaming",
        "amount": "19.99",
    }


def test_session_remains_open_across_wait_for_user() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    result = executor.run_card_portal_payment(bill=_bill_with_portal())
    session_id = result["session_id"]
    assert result["waiting_for_user"] is True
    assert result["resumable"] is True
    assert recording.is_session_open(session_id)
    assert executor.has_resumable_session(session_id)


def test_resume_continues_in_same_session() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    started = executor.run_card_portal_payment(bill=_bill_with_portal())
    session_id = started["session_id"]
    assert recording.session_run_count(session_id) == 1

    resumed = executor.resume_card_portal_session(session_id)
    assert resumed["status"] == "completed"
    assert resumed["page_text"] is not None
    assert recording.session_run_count(session_id) == 2
    actions = recording.session_actions(session_id)
    assert any(action.action_type == BrowserActionType.WAIT_FOR_USER for action in actions)
    assert any(action.action_type == BrowserActionType.READ_PAGE for action in actions)
    assert not recording.is_session_open(session_id)
    assert not executor.has_resumable_session(session_id)


def test_cancel_closes_session_safely() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    started = executor.run_card_portal_payment(bill=_bill_with_portal())
    session_id = started["session_id"]

    cancelled = executor.cancel_card_portal_session(session_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled"] is True
    assert not recording.is_session_open(session_id)
    assert not executor.has_resumable_session(session_id)

    with pytest.raises(ProviderUnavailableError):
        executor.resume_card_portal_session(session_id)


def test_resume_after_timeout_is_handled_cleanly() -> None:
    recording = RecordingBrowserAdapter()
    store = BrowserSessionStore()
    executor = GovernedBrowserExecutor(
        recording,
        session_timeout_seconds=60.0,
        session_store=store,
    )
    with patch("time.time", return_value=1000.0):
        started = executor.run_card_portal_payment(bill=_bill_with_portal())
    session_id = started["session_id"]
    assert recording.is_session_open(session_id)

    with patch("time.time", return_value=2000.0):
        timed_out = executor.resume_card_portal_session(session_id)

    assert timed_out["status"] == "timed_out"
    assert timed_out["timed_out"] is True
    assert not recording.is_session_open(session_id)
    assert not executor.has_resumable_session(session_id)


def test_expire_stale_sessions_closes_orphaned_sessions() -> None:
    recording = RecordingBrowserAdapter()
    store = BrowserSessionStore()
    executor = GovernedBrowserExecutor(
        recording,
        session_timeout_seconds=30.0,
        session_store=store,
    )
    with patch("time.time", return_value=1000.0):
        started = executor.run_card_portal_payment(bill=_bill_with_portal())
    session_id = started["session_id"]

    with patch("time.time", return_value=5000.0):
        expired = executor.expire_stale_sessions()

    assert len(expired) == 1
    assert expired[0]["session_id"] == session_id
    assert expired[0]["status"] == "timed_out"
    assert not recording.is_session_open(session_id)
