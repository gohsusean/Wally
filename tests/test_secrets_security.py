"""v0.11.1 secret-leakage and authorization tests. Uses canary values only."""

from __future__ import annotations

import json
import subprocess
import traceback
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_finance import _bill_knowledge, _MockKnowledge, _MockWorkflow, _settings
from wally.adapters.browser.recording import RecordingBrowserAdapter
from wally.adapters.finance.local import LocalFinanceAdapter
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.adapters.secrets.op_cli import OnePasswordCliAdapter
from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.models.actions import ActionClass, ToolCall
from wally.models.browser import BrowserAction, BrowserActionType
from wally.models.workflow import WorkflowDefinition, WorkflowTriggerResult
from wally.orchestrator.tools import ToolRegistry
from wally.runtime.browser_executor import GovernedBrowserExecutor
from wally.runtime.browser_safety import evaluate_browser_session_actions
from wally.runtime.principals import PrincipalAuthority
from wally.runtime.secret_resolver import GovernedSecretsResolver
from wally.runtime.secrets_safety import flatten_for_leak_search
from wally.safety.gates import ApprovalGate

CANARY_SECRET = "WALLY_CANARY_SECRET_9f3c2a1b"
CANARY_USER = "WALLY_CANARY_USER_7e4d1c0a"
USERNAME_REF = "op://Personal/Canary/username"
PASSWORD_REF = "op://Personal/Canary/password"
OTP_REF = "op://Personal/Canary/otp"


def assert_no_canary(*parts: object, canary: str = CANARY_SECRET) -> None:
    blob = " ".join(flatten_for_leak_search(part) for part in parts)
    assert canary not in blob
    assert CANARY_USER not in blob or canary == CANARY_USER


def _portal_bill(**extra) -> dict:
    bill = {
        "provider": "Streaming Co",
        "asset_id": "bill-1",
        "payment_method": "card_portal",
        "payment_portal_url": "https://pay.example.com/streaming",
        "amount": "19.99",
        "portal_username_ref": USERNAME_REF,
        "portal_password_ref": PASSWORD_REF,
        "login_username_selector": "#email",
        "login_password_selector": "#password",
        "login_submit_selector": "button[type=submit]",
        "auth_success_selector": "#dashboard",
    }
    bill.update(extra)
    return bill


def _card_portal_workflow() -> _MockWorkflow:
    return _MockWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-card-portal",
                description="Card portal",
                webhook_path="pay-bill-card-portal",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="card_portal",
                execution_backend="browser",
            ),
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
            ),
        ]
    )


class _EchoWorkflow(_MockWorkflow):
    def trigger(self, workflow: str, parameters: dict | None = None) -> WorkflowTriggerResult:
        self.triggered.append((workflow, parameters))
        return WorkflowTriggerResult(
            workflow=workflow,
            status="triggered",
            response={"echo": parameters},
        )


class _DenyApproval:
    def request_approval(self, summary: str, *, action_class: str) -> bool:
        return False


class _AllowApproval:
    last_summary: str = ""

    def request_approval(self, summary: str, *, action_class: str) -> bool:
        self.last_summary = summary
        return True


def _finance_registry(
    tmp_path: Path,
    *,
    secrets: MemorySecretsProvider,
    approval,
    dry_run: bool = False,
):
    recording = RecordingBrowserAdapter()
    resolver = GovernedSecretsResolver(secrets, audit=AuditLogger(tmp_path / "audit"))
    finance = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_bill_knowledge(_portal_bill()),
        workflow=_card_portal_workflow(),
        browser_executor=GovernedBrowserExecutor(recording, secrets=resolver),
        secrets=resolver,
    )
    authority = PrincipalAuthority()
    registry = ToolRegistry(
        providers={"finance": finance},
        knowledge=None,
        finance=finance,
        finance_bills_role="finance",
        browser_executor=finance._browser_executor,
        gate=ApprovalGate(require_approval=("financial",), dry_run=dry_run),
        approval=approval,
        audit=AuditLogger(tmp_path / "audit-tools"),
        dry_run=dry_run,
        authority=authority,
    )
    return registry, secrets, recording, resolver


def test_secret_unavailable_before_authorization() -> None:
    provider = MemorySecretsProvider({PASSWORD_REF: CANARY_SECRET})
    resolver = GovernedSecretsResolver(provider)
    with pytest.raises(ProviderUnavailableError):
        resolver.resolve(PASSWORD_REF, authorized=False)
    assert provider.resolve_calls == []


def test_denied_approval_never_resolves_secret(tmp_path: Path) -> None:
    secrets = MemorySecretsProvider(
        {USERNAME_REF: CANARY_USER, PASSWORD_REF: CANARY_SECRET}
    )
    registry, provider, recording, _ = _finance_registry(
        tmp_path, secrets=secrets, approval=_DenyApproval()
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="finance_trigger_payment",
            arguments={
                "bill": _portal_bill(),
                "statement": {
                    "provider": "Streaming Co",
                    "amount": "19.99",
                    "payment_portal_url": "https://pay.example.com/streaming",
                },
            },
        ),
        context=registry._authority.issue("repl"),
    )
    assert result.denied
    assert provider.resolve_calls == []
    assert not recording.opened_urls
    assert_no_canary(result.output, result.action_taken)


def test_dry_run_never_resolves_secret(tmp_path: Path) -> None:
    secrets = MemorySecretsProvider({PASSWORD_REF: CANARY_SECRET, USERNAME_REF: CANARY_USER})
    registry, provider, _, _ = _finance_registry(
        tmp_path, secrets=secrets, approval=_AllowApproval(), dry_run=True
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="finance_trigger_payment",
            arguments={
                "bill": _portal_bill(),
                "statement": {
                    "provider": "Streaming Co",
                    "amount": "19.99",
                    "payment_portal_url": "https://pay.example.com/streaming",
                },
            },
        ),
        context=registry._authority.issue("repl"),
    )
    assert result.denied
    assert provider.resolve_calls == []
    assert_no_canary(result.output)


def test_authorized_execution_resolves_and_hides_values(tmp_path: Path) -> None:
    secrets = MemorySecretsProvider(
        {USERNAME_REF: CANARY_USER, PASSWORD_REF: CANARY_SECRET}
    )
    approval = _AllowApproval()
    registry, provider, recording, _ = _finance_registry(
        tmp_path, secrets=secrets, approval=approval
    )
    result = registry.execute(
        ToolCall(
            call_id="c1",
            name="finance_trigger_payment",
            arguments={
                "bill": _portal_bill(),
                "statement": {
                    "provider": "Streaming Co",
                    "amount": "19.99",
                    "payment_portal_url": "https://pay.example.com/streaming",
                },
            },
        ),
        context=registry._authority.issue("repl"),
    )
    assert not result.denied
    assert PASSWORD_REF in provider.resolve_calls
    payload = json.loads(result.output)
    assert payload.get("authenticated") is True
    assert "page_text" not in payload
    assert_no_canary(result.output, result.action_taken, approval.last_summary, payload)
    assert_no_canary(recording.session_actions(payload["session_id"]))
    assert PASSWORD_REF in flatten_for_leak_search(_portal_bill())


def test_planning_structures_contain_refs_not_values() -> None:
    bill = _portal_bill()
    blob = flatten_for_leak_search(bill)
    assert PASSWORD_REF in blob
    assert CANARY_SECRET not in blob


def test_audit_contains_reference_not_value(tmp_path: Path) -> None:
    provider = MemorySecretsProvider({PASSWORD_REF: CANARY_SECRET})
    audit = AuditLogger(tmp_path)
    resolver = GovernedSecretsResolver(provider, audit=audit)
    resolver.resolve(PASSWORD_REF, authorized=True, session_id="s1", purpose="portal_login")
    payload = (tmp_path / next(tmp_path.glob("*.jsonl")).name).read_text(encoding="utf-8")
    assert PASSWORD_REF in payload
    assert "portal_login" in payload
    assert_no_canary(payload)


def test_op_cli_exceptions_do_not_leak_secret() -> None:
    adapter = OnePasswordCliAdapter()
    expired = subprocess.TimeoutExpired(
        cmd=["op", "read", PASSWORD_REF],
        timeout=1,
        output=CANARY_SECRET.encode(),
        stderr=CANARY_SECRET.encode(),
    )
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/usr/bin/op"),
        patch("wally.adapters.secrets.op_cli.subprocess.run", side_effect=expired),
        pytest.raises(ProviderUnavailableError) as exc,
    ):
        adapter.resolve(PASSWORD_REF)
    formatted = "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert_no_canary(str(exc.value), formatted, exc.value.reason)


def test_browser_action_repr_redacts_fill_value() -> None:
    action = BrowserAction(
        BrowserActionType.FILL,
        {"selector": "#password", "value": CANARY_SECRET},
    )
    assert_no_canary(repr(action))
    assert "[redacted]" in repr(action)


def test_unauthorized_portal_call_does_not_resolve() -> None:
    secrets = MemorySecretsProvider(
        {USERNAME_REF: CANARY_USER, PASSWORD_REF: CANARY_SECRET}
    )
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(
        recording,
        secrets=GovernedSecretsResolver(secrets),
    )
    result = executor.run_card_portal_payment(bill=_portal_bill(), authorized=False)
    assert result["waiting_for_user"] is True
    assert secrets.resolve_calls == []
    assert_no_canary(result)


def test_manual_login_fallback_without_refs() -> None:
    recording = RecordingBrowserAdapter()
    executor = GovernedBrowserExecutor(
        recording,
        secrets=GovernedSecretsResolver(
            MemorySecretsProvider({PASSWORD_REF: CANARY_SECRET})
        ),
    )
    result = executor.run_card_portal_payment(
        bill={
            "provider": "Streaming Co",
            "payment_portal_url": "https://pay.example.com/streaming",
        },
        authorized=True,
    )
    assert result["waiting_for_user"] is True
    assert result["resumable"] is True


def test_secrets_disabled_fails_closed_for_workflow_refs() -> None:
    from wally.runtime.secret_resolver import GovernedSecretsResolver

    workflow = _EchoWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
            )
        ]
    )
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=workflow,
        secrets=GovernedSecretsResolver(None),
    )
    with pytest.raises(ProviderUnavailableError):
        adapter._trigger_payment(
            "pay-bill-bank-transfer",
            parameters={"amount": "10"},
            bill={"workflow_secret_refs": {"otp": OTP_REF}},
            authorized=True,
        )
    assert workflow.triggered == []


def test_malformed_references_fail_closed() -> None:
    provider = MemorySecretsProvider({"not-a-ref": CANARY_SECRET})
    resolver = GovernedSecretsResolver(provider)
    with pytest.raises(ProviderUnavailableError, match="op://"):
        resolver.resolve("not-a-ref", authorized=True)
    assert provider.resolve_calls == []


def test_n8n_model_facing_result_scrubs_echoed_secrets() -> None:
    secrets = MemorySecretsProvider({OTP_REF: CANARY_SECRET})
    workflow = _EchoWorkflow(
        workflows=[
            WorkflowDefinition(
                name="pay-bill-bank-transfer",
                description="Pay",
                webhook_path="pay-bill-bank-transfer",
                action_class=ActionClass.FINANCIAL,
                capability_domain="payment",
                capability="bank_transfer",
            )
        ]
    )
    adapter = LocalFinanceAdapter(
        settings=_settings(),
        knowledge=_MockKnowledge(),
        workflow=workflow,
        secrets=GovernedSecretsResolver(secrets),
    )
    result = adapter._trigger_payment(
        "pay-bill-bank-transfer",
        parameters={"amount": "10"},
        bill={"workflow_secret_refs": {"otp": OTP_REF}},
        authorized=True,
    )
    assert workflow.triggered[0][1]["otp"] == CANARY_SECRET
    assert result["secret_parameter_names"] == ["otp"]
    assert_no_canary(result)


def test_screenshots_denied_by_policy() -> None:
    decision = evaluate_browser_session_actions(
        actions=(BrowserAction(BrowserActionType.SCREENSHOT, {"path": "/tmp/x.png"}),),
        knowledge={"payment_portal_url": "https://pay.example.com/streaming"},
    )
    assert decision.allowed is False
    assert "disabled" in (decision.reason or "").lower()


def test_cli_error_path_does_not_print_canary(capsys) -> None:
    from types import SimpleNamespace

    from wally.cli import _print_health

    app = SimpleNamespace(
        settings=SimpleNamespace(
            knowledge_enabled=False,
            workflow_enabled=False,
            communications_enabled=False,
            web_enabled=False,
            finance_enabled=False,
            secrets_enabled=True,
        ),
        knowledge_registry=None,
        orchestrator=SimpleNamespace(
            check_health=lambda: {"secrets": False, "openai": True}
        ),
    )
    _print_health(app)
    captured = capsys.readouterr().out
    assert_no_canary(captured)
    assert "1Password" in captured
