"""Governed browser executor and Playwright adapter policy tests."""

import pytest

from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.adapters.browser.recording import RecordingBrowserAdapter
from wally.exceptions import ProviderUnavailableError
from wally.models.browser import BrowserAction, BrowserActionType
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.browser_safety import MISSING_TRUSTED_PORTAL_MESSAGE


def _bill_with_portal() -> dict:
    return {
        "provider": "Streaming Co",
        "payment_method": "card_portal",
        "payment_portal_url": "https://pay.example.com/streaming",
        "amount": "19.99",
    }


def test_governed_executor_opens_only_trusted_portal() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    session = executor.open_trusted_session(_bill_with_portal())
    assert session.url == "https://pay.example.com/streaming"
    assert recording.opened_urls == ["https://pay.example.com/streaming"]


def test_governed_executor_rejects_untrusted_navigation_url() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    session = executor.open_trusted_session(_bill_with_portal())
    with pytest.raises(ProviderUnavailableError) as exc:
        executor.run_governed_actions(
            knowledge=_bill_with_portal(),
            session_id=session.session_id,
            actions=(
                BrowserAction(
                    BrowserActionType.NAVIGATE,
                    {"url": "https://evil.example.com/phish"},
                ),
            ),
        )
    assert "does not match" in str(exc.value.reason).lower()


def test_governed_executor_blocks_missing_trusted_portal() -> None:
    executor = GovernedBrowserExecutor(RecordingBrowserAdapter())
    with pytest.raises(ProviderUnavailableError) as exc:
        executor.open_trusted_session({"provider": "Streaming Co"})
    assert MISSING_TRUSTED_PORTAL_MESSAGE in str(exc.value.reason)


def test_card_portal_payment_waits_for_user() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(recording)
    result = executor.run_card_portal_payment(
        bill=_bill_with_portal(), parameters={"amount": 19.99}
    )
    assert result["execution"] == "browser"
    assert result["waiting_for_user"] is True
    assert result["resumable"] is True
    assert result["next_step"] == "resume"
    assert recording.is_session_open(result["session_id"])


def test_playwright_adapter_reports_unhealthy_without_package() -> None:
    adapter = PlaywrightBrowserAdapter()
    try:
        import playwright  # noqa: F401

        assert adapter.is_healthy() is True
    except ImportError:
        assert adapter.is_healthy() is False


def test_finance_card_portal_blocked_without_portal_url(tmp_path) -> None:
    from tests.test_finance import _bill_knowledge, _MockWorkflow, _settings
    from wally.adapters.finance.local import LocalFinanceAdapter
    from wally.audit.logger import AuditLogger
    from wally.models.actions import ActionClass, ToolCall
    from wally.models.workflow import WorkflowDefinition
    from wally.orchestrator.tools import ToolRegistry
    from wally.runtime.browser_executor import GovernedBrowserExecutor
    from wally.safety.gates import ApprovalGate

    recording = RecordingBrowserAdapter()
    finance = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_bill_knowledge(
            {"provider": "Streaming Co", "payment_method": "card_portal", "amount": "19.99"}
        ),
        workflow=_MockWorkflow(
            workflows=[
                WorkflowDefinition(
                    name="pay-bill-card-portal",
                    description="Card portal",
                    webhook_path="pay-bill-card-portal",
                    action_class=ActionClass.FINANCIAL,
                    capability_domain="payment",
                    capability="card_portal",
                    execution_backend="browser",
                )
            ]
        ),
        browser_executor=GovernedBrowserExecutor(recording),
    )
    from wally.runtime.principals import PrincipalAuthority

    authority = PrincipalAuthority()
    registry = ToolRegistry(
        providers={"finance": finance},
        knowledge=None,
        finance=finance,
        finance_bills_role="finance",
        browser_executor=GovernedBrowserExecutor(recording),
        gate=ApprovalGate(require_approval=("financial",), dry_run=False),
        approval=_CapturingApproval(),
        audit=AuditLogger(tmp_path / "audit"),
        dry_run=False,
        authority=authority,
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="finance_trigger_payment",
            arguments={
                "bill": {
                    "asset_id": "bill-1",
                    "provider": "Streaming Co",
                    "payment_method": "card_portal",
                    "amount": "19.99",
                },
                "statement": {
                    "provider": "Streaming Co",
                    "amount": "19.99",
                    "payment_portal_url": "https://evil.example.com",
                },
            },
        ),
        context=authority.issue("repl"),
    )
    assert result.denied
    assert "browser_policy_denied" in (result.action_taken or "")
    assert not recording.opened_urls


class _CapturingApproval:
    def request_approval(self, summary: str, *, action_class: str) -> bool:
        return True
