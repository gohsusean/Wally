"""Local stdio integration, scoped capabilities and disabled production defaults."""

import io
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import yaml

from tests.test_notion_edits import harness, propose
from wally.codex.__main__ import build_adapter, serve_stdio
from wally.codex.config import load_config
from wally.codex.service import CODEX_POLICY, CodexEditAdapter, tools
from wally.exceptions import AuthorizationError
from wally.gateway.service import AdapterRegistration, GatewayRuntime
from wally.models.principal import Capability
from wally.ops.notion_edits import EditError


def test_local_tools_use_gateway_and_do_not_accept_owner_assertions(tmp_path):
    service, human, ctx = harness(tmp_path)
    registration = AdapterRegistration("local", "codex", "fixture-session", True)
    gateway = GatewayRuntime(
        service.store, service.authority, (registration,), notion_edits=service
    )
    adapter = CodexEditAdapter(gateway, registration)
    proposal = propose(service, ctx)
    args = {
        "selections": [
            {
                "proposal_id": proposal.id,
                "fingerprint": proposal.fingerprint,
                "decision": "approve",
            }
        ]
    }

    def call(arguments):
        return adapter.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "decide_notion_edits",
                    "arguments": arguments,
                },
            }
        )["result"]

    assert call({**args, "human_approved": True})["isError"]
    assert call({**args, "principal": "owner"})["isError"]
    assert human.reviews == []
    assert not call(args)["isError"]
    assert len(human.reviews) == 1
    assert (
        not {
            Capability.EXECUTE_PROPOSAL,
            Capability.VERIFY_EXECUTION,
            Capability.CERTIFY_FINANCIAL_DATA,
        }
        & CODEX_POLICY.capabilities
    )
    assert {t["name"] for t in tools()} == {
        "reconcile_notion_record",
        "get_notion_edit_status",
        "inspect_notion_execution",
        "propose_notion_edit",
        "get_notion_edit",
        "decide_notion_edits",
        "execute_notion_edit",
        "verify_notion_edit",
    }


def test_stdio_notifications_framing_and_errors(tmp_path):
    service, _, _ = harness(tmp_path)
    registration = AdapterRegistration("local", "codex", "fixture-session", True)
    adapter = CodexEditAdapter(
        GatewayRuntime(
            service.store,
            service.authority,
            (registration,),
            notion_edits=service,
        ),
        registration,
    )
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps({"method": "notifications/initialized"}),
                json.dumps({"id": 1, "method": "initialize"}),
                "invalid JSON",
                json.dumps({"id": 2, "method": "tools/list"}),
                json.dumps({"id": 3, "method": "tools/call", "params": {"name": "pay_bill"}}),
            ]
        )
        + "\n"
    )
    output = io.StringIO()
    serve_stdio(adapter, input_stream, output)
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(messages) == 4
    assert messages[0]["result"]["serverInfo"]["name"] == "wally-scoped-edits"
    assert messages[1]["error"]["code"] == -32700
    assert len(messages[2]["result"]["tools"]) == 8
    assert "native review" in messages[0]["result"]["instructions"]
    assert messages[3]["error"]
    with pytest.raises(EditError, match="size"):
        serve_stdio(adapter, io.StringIO("x" * 70000), output)


def test_empty_default_runtime_never_contacts_a_provider(tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("Disabled runtime must not contact a provider")

    monkeypatch.setattr("wally.codex.__main__.httpx.Client", forbid)
    settings = SimpleNamespace(
        project_root=tmp_path,
        ops_database=tmp_path / "ops.db",
        knowledge_registry_database=tmp_path / "registry.db",
        audit_directory=tmp_path / "audit",
        dry_run=False,
    )
    adapter = build_adapter(settings)
    result = adapter.handle(
        {
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "propose_notion_edit",
                "arguments": {
                    "target_key": "tnb",
                    "replacements": {"policy": "source_defined"},
                },
            },
        }
    )["result"]
    assert result["isError"]
    assert "not registered" in result["structuredContent"]["error"]
    assert not load_config(tmp_path / "missing.yaml").writes_enabled


def test_cli_isolation_keeps_production_state_paths_untouched(tmp_path, monkeypatch):
    from wally.codex.__main__ import main

    @dataclass
    class Settings:
        ops_database: object
        knowledge_registry_database: object
        audit_directory: object

    production = tmp_path / "production.db"
    production.write_bytes(b"existing state must survive")
    settings = Settings(production, production, tmp_path / "production-audit")
    state = tmp_path / "lab"
    policy = tmp_path / "lab-policy.yaml"
    captured = []
    monkeypatch.setattr("wally.codex.__main__.load_settings", lambda **kw: settings)
    monkeypatch.setattr(
        "wally.codex.__main__.build_adapter",
        lambda settings, **kw: captured.append((settings, kw)),
    )
    monkeypatch.setattr("wally.codex.__main__.serve_stdio", lambda *a: None)
    monkeypatch.setattr(
        "sys.argv", ["wally.codex", "--state-dir", str(state), "--edit-policy", str(policy)]
    )
    assert main() == 0
    isolated, options = captured[0]
    assert isolated.ops_database == state / "operations.db"
    assert isolated.knowledge_registry_database == state / "registry.db"
    assert isolated.audit_directory == state / "audit"
    assert options == {"policy_path": policy}
    assert production.read_bytes() == b"existing state must survive"


def test_status_reports_only_registered_scope_and_authenticated_principal(tmp_path):
    service, _, ctx = harness(tmp_path)
    status = service.status(context=ctx)
    assert status["channel"] == "codex"
    assert status["principal"] == ctx.principal.subject
    assert {t["key"] for t in status["targets"]} == {"tnb", "maybank"}
    assert status["human_confirmation_required"]
    assert status["execution_confirmation_is_separate"]
    with pytest.raises(AuthorizationError):
        service.status(context=None)


@pytest.mark.parametrize(
    "bad",
    [
        {"writes_enabled": "true"},
        {"unknown": "field"},
        {"macos_confirmation": {"helper": "relative", "owner_uid": 501, "helper_sha256": "a" * 64}},
        {
            "macos_confirmation": {
                "helper": "/tmp/helper",
                "owner_uid": True,
                "helper_sha256": "a" * 64,
            }
        },
        {
            "macos_confirmation": {
                "helper": "/tmp/helper",
                "owner_uid": 501,
                "helper_sha256": "g" * 64,
            }
        },
        {"targets": [{"key": "test", "page_id": "bad", "properties": []}]},
    ],
)
def test_invalid_config_fails_closed(tmp_path, bad):
    path = tmp_path / "edits.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, **bad}))
    with pytest.raises(EditError, match="Invalid reviewed"):
        load_config(path)
