"""Local stdio integration, scoped capabilities and disabled production defaults."""

import io
import json
from types import SimpleNamespace

import pytest
import yaml

from tests.test_notion_edits import harness, propose
from wally.codex.__main__ import build_adapter, serve_stdio
from wally.codex.config import load_config
from wally.codex.service import CODEX_POLICY, CodexEditAdapter, tools
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
    assert len(messages[2]["result"]["tools"]) == 5
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
