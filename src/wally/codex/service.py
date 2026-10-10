"""Local Codex stdio requests reach the same Gateway/service authorization path.

The local process is connection authentication only. Decision/execute calls still
invoke the registered native human provider synchronously; the agent cannot send
approval proof. No HTTP listener, hosted bearer grant, browser executor or legacy
ToolRegistry is installed by this runtime.
"""

from __future__ import annotations

import secrets

from wally.gateway.service import AdapterRegistration, GatewayRuntime
from wally.models.principal import Capability
from wally.runtime.principals import ChannelPolicy

CODEX_CHANNEL = "codex_local"
CODEX_POLICY = ChannelPolicy(
    "local_stdio",
    frozenset(
        {
            Capability.SUBMIT_REQUEST,
            Capability.READ_CONTEXT,
            Capability.DECIDE_PROPOSAL,
            Capability.EXECUTE_NOTION_EDIT,
            Capability.VERIFY_NOTION_EDIT,
        }
    ),
)


def registration() -> AdapterRegistration:
    return AdapterRegistration("codex-local", CODEX_CHANNEL, secrets.token_urlsafe(32), True)


class CodexEditAdapter:
    def __init__(self, runtime: GatewayRuntime, adapter: AdapterRegistration):
        self.runtime = runtime
        self.adapter = adapter

    def handle(self, message: dict) -> dict | None:
        request_id = message.get("id")
        if request_id is None:  # MCP initialized/other notifications have no response.
            return None
        method = message.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "wally-scoped-edits", "version": "0.18.0"},
                "instructions": (
                    "Wally authorizes only exact registered proposal versions. First read "
                    "get_notion_edit_status. Show get_notion_edit's exact changes to the owner. "
                    "decide_notion_edits requires native review and fresh biometrics; it does "
                    "not execute. Ask for a separate execute request. Execution prompts again "
                    "and independently verifies. Only verified_success proves the result. "
                    "Inspect uncertain attempts without repeating writes. Never use direct "
                    "Notion connector writes on Wally-managed targets."
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools()}
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict):
                return _error(request_id, "Invalid tool request.")
            name, body = params.get("name"), params.get("arguments", {})
            if name not in {t["name"] for t in tools()} or not isinstance(body, dict):
                return _error(request_id, "Unknown tool or malformed arguments.")
            response = self.runtime.dispatch(
                adapter_id=self.adapter.adapter_id,
                credential=self.adapter.credential,
                op=name,
                body=body,
            )
            result = {
                "isError": not response.ok,
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Wally returned exact review/state; "
                            "only verified_success establishes the edit outcome."
                            if response.ok
                            else response.error
                        ),
                    }
                ],
                "structuredContent": response.data if response.ok else {"error": response.error},
            }
        else:
            return _error(request_id, "Unknown method.")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, text):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": text}}


def tools() -> list[dict]:
    selection = {
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string"},
            "fingerprint": {"type": "string"},
            "decision": {"type": "string", "enum": ["approve", "reject", "defer"]},
            "defer_until": {"type": "string"},
        },
        "required": ["proposal_id", "fingerprint", "decision"],
        "additionalProperties": False,
    }
    definitions = (
        (
            "get_notion_edit_status",
            "Read the runtime-issued principal, enabled gate and registered target/property scope.",
            {},
            True,
        ),
        (
            "inspect_notion_execution",
            "Read/reconcile an interrupted or uncertain attempt. Reports original, approved or "
            "unexpected state; never writes, releases a lock or authorizes a retry.",
            {"execution_id": {"type": "string"}},
            True,
        ),
        (
            "propose_notion_edit",
            "Stage inert advice for an explicitly registered metadata target.",
            {
                "target_key": {"type": "string"},
                "replacements": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            False,
        ),
        (
            "get_notion_edit",
            "Read the immutable proposal version and exact old/new values.",
            {
                "proposal_id": {"type": "string"},
            },
            True,
        ),
        (
            "decide_notion_edits",
            "Request native owner review of exactly these selections. "
            "Requires fresh biometrics; conversational approval cannot substitute for it.",
            {
                "selections": {"type": "array", "minItems": 1, "maxItems": 20, "items": selection},
            },
            False,
        ),
        (
            "execute_notion_edit",
            "Separately request native owner authorization of the approved "
            "version, then execute and independently verify. No payment or certification.",
            {
                "proposal_id": {"type": "string"},
            },
            False,
        ),
        (
            "verify_notion_edit",
            "Re-read an uncertain edit without repeating the write. "
            "A still-running attempt needs manual reconciliation.",
            {
                "execution_id": {"type": "string"},
            },
            False,
        ),
    )
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": not read_only,
                "openWorldHint": False,
            },
        }
        for name, description, properties, read_only in definitions
    ]
