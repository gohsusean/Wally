"""Translate ChatGPT tool calls into authenticated Gateway operations.

Connection authentication is a bearer token issued only after the local owner
secret completes an OAuth-style grant. ``openai/subject`` is checked as well
for decisions. A subject string alone never becomes the Wally owner.

``record_decision`` is omitted unless the operator has confirmed that the
ChatGPT host prompts before that write. Model invocation is not that prompt.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field

from wally.gateway.service import (
    CHATGPT_CHANNEL,
    GATEWAY_INGRESS,
    AdapterRegistration,
    GatewayHost,
    GatewayResult,
    GatewayRuntime,
)
from wally.models.gateway import FORBIDDEN_CLAIM_KEYS, EvidenceKind
from wally.models.principal import Capability
from wally.runtime.principals import ChannelPolicy

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
}
_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "openWorldHint": False,
}
_DECISION = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "openWorldHint": False,
}


@dataclass
class ChatGPTConfig:
    """Capability switches. Plan names are not an input."""

    owner_secret: str
    gateway_credential: str
    allowed_subjects: frozenset[str] = frozenset()
    allowed_organizations: frozenset[str] = frozenset()
    write_enabled: bool = False
    decision_confirmation: bool = False

    @property
    def decisions_enabled(self) -> bool:
        return bool(
            self.write_enabled
            and self.decision_confirmation
            and self.allowed_subjects
            and self.owner_secret
            and self.gateway_credential
        )


@dataclass
class ConnectionAuthority:
    """Issues access tokens after the owner secret is presented.

    The OpenAI subject is not accepted as this secret.
    """

    owner_secret: str
    _tokens: set[str] = field(default_factory=set)
    _codes: dict[str, str] = field(default_factory=dict)

    def grant(self, owner_secret: str, code_challenge: str) -> str | None:
        if not self.owner_secret or not _same_secret(owner_secret, self.owner_secret):
            return None
        if not code_challenge:
            return None
        code = secrets.token_urlsafe(24)
        self._codes[code] = code_challenge
        return code

    def exchange(self, code: str, verifier: str) -> str | None:
        challenge = self._codes.pop(code, None)
        if challenge is None or not verifier:
            return None
        if _s256(verifier) != challenge:
            return None
        token = secrets.token_urlsafe(32)
        self._tokens.add(hashlib.sha256(token.encode()).hexdigest())
        return token

    def authenticated(self, authorization: str) -> bool:
        token = _bearer(authorization)
        if token is None:
            return False
        digest = hashlib.sha256(token.encode()).hexdigest()
        return any(hmac.compare_digest(digest, stored) for stored in self._tokens)


def issue_token(authority: ConnectionAuthority, owner_secret: str) -> str | None:
    """Test and loopback helper: owner secret in, access token out."""
    verifier = secrets.token_urlsafe(32)
    code = authority.grant(owner_secret, _s256(verifier))
    if code is None:
        return None
    return authority.exchange(code, verifier)


def chatgpt_policy(config: ChatGPTConfig) -> ChannelPolicy:
    capabilities = {Capability.READ_CONTEXT}
    if config.write_enabled:
        capabilities.add(Capability.SUBMIT_REQUEST)
        capabilities.add(Capability.LINK_CHANNEL)
    if config.decisions_enabled:
        capabilities.add(Capability.DECIDE_PROPOSAL)
    return ChannelPolicy(GATEWAY_INGRESS, frozenset(capabilities))


def chatgpt_registration(config: ChatGPTConfig) -> AdapterRegistration:
    return AdapterRegistration(
        adapter_id="chatgpt",
        channel=CHATGPT_CHANNEL,
        credential=config.gateway_credential,
        approval_adapter=False,
    )


class ChatGPTAdapter:
    def __init__(self, runtime: GatewayRuntime, config: ChatGPTConfig) -> None:
        self._runtime = runtime
        self._config = config
        self._connection = ConnectionAuthority(config.owner_secret)

    @property
    def connection(self) -> ConnectionAuthority:
        return self._connection

    def tools(self) -> list[dict]:
        listed = [_attention(), _matter(), _proposals(), _lifecycle()]
        if self._config.write_enabled:
            listed.extend([_submit(), _link(), _visibility()])
        if self._config.decisions_enabled:
            listed.append(_decision())
        return listed

    def handle(
        self,
        message: dict,
        *,
        authorization: str = "",
        meta: dict | None = None,
    ) -> dict:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method == "initialize":
            return _ok(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "wally", "version": "0.18.0"},
                },
            )
        if not self._connection.authenticated(authorization):
            return _err(request_id, "Authentication required.")
        if method == "tools/list":
            return _ok(request_id, {"tools": self.tools()})
        if method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            return _ok(request_id, self.call_tool(name, arguments, meta or {}))
        return _err(request_id, "Unknown method.")

    def call_tool(self, name: str, arguments: dict, meta: dict) -> dict:
        if name not in {item["name"] for item in self.tools()}:
            return _tool_error("That tool is not available.")
        if _forbidden(arguments):
            return _tool_error(
                "Tool arguments cannot set channel, principal, subject, or capabilities."
            )
        host = _host_from_meta(meta)
        needs_session = name in {
            "submit_request",
            "link_session",
            "set_handle_visibility",
            "record_decision",
        }
        if needs_session and not host.session:
            return _tool_error("The host session id is missing.")
        if name == "record_decision":
            return self._decision(arguments, host)
        dispatch = {
            "list_attention": self._attention,
            "get_matter": self._matter,
            "list_proposals": self._proposals,
            "get_lifecycle": self._lifecycle,
            "submit_request": self._submit,
            "link_session": self._link,
            "set_handle_visibility": self._visibility,
        }
        return dispatch[name](arguments, host)

    def _attention(self, arguments: dict, host: GatewayHost) -> dict:
        del arguments
        active = self._call("list_active", {}, host)
        proposals = self._call("list_proposals", {}, host)
        if not active.ok or not proposals.ok:
            return _tool_error(active.error or proposals.error)
        return _tool_json(
            {
                "active_matters": active.data.get("active_matters", []),
                "proposals": proposals.data.get("proposals", []),
            }
        )

    def _matter(self, arguments: dict, host: GatewayHost) -> dict:
        return self._read(
            "get_context",
            {"active_matter_id": arguments.get("active_matter_id", "")},
            host,
        )

    def _proposals(self, arguments: dict, host: GatewayHost) -> dict:
        return self._read(
            "list_proposals",
            {"matter_id": arguments.get("matter_id", "")},
            host,
        )

    def _lifecycle(self, arguments: dict, host: GatewayHost) -> dict:
        return self._read(
            "lifecycle",
            {
                "execution_id": arguments.get("execution_id", ""),
                "correlation_id": arguments.get("correlation_id", ""),
            },
            host,
        )

    def _submit(self, arguments: dict, host: GatewayHost) -> dict:
        evidence = []
        utterance = arguments.get("utterance")
        if utterance:
            evidence.append({"kind": EvidenceKind.LATEST_USER.value, "text": utterance})
        if arguments.get("prior_user"):
            evidence.append(
                {"kind": EvidenceKind.PRIOR_USER.value, "text": arguments["prior_user"]}
            )
        if arguments.get("assistant_summary"):
            evidence.append(
                {
                    "kind": EvidenceKind.ASSISTANT_SUMMARY.value,
                    "text": arguments["assistant_summary"],
                }
            )
        if arguments.get("message_ref"):
            evidence.append(
                {"kind": EvidenceKind.EXTERNAL_REF.value, "text": arguments["message_ref"]}
            )
        return self._read(
            "submit_request",
            {
                "evidence": evidence,
                "active_matter_id": arguments.get("active_matter_id", ""),
                "document_hint": arguments.get("document_hint", ""),
                "recipient_hint": arguments.get("recipient_hint", ""),
                "continue_correlation_id": arguments.get("continue_correlation_id", ""),
                "external_request_ref": arguments.get("message_ref", ""),
            },
            host,
        )

    def _link(self, arguments: dict, host: GatewayHost) -> dict:
        return self._read(
            "link_channel",
            {
                "active_matter_id": arguments.get("active_matter_id", ""),
            },
            host,
        )

    def _visibility(self, arguments: dict, host: GatewayHost) -> dict:
        return self._read(
            "set_visibility",
            {
                "active_matter_id": arguments.get("active_matter_id", ""),
                "visibility": arguments.get("visibility", ""),
            },
            host,
        )

    def _decision(self, arguments: dict, host: GatewayHost) -> dict:
        if not self._config.decisions_enabled:
            return _tool_error("Proposal decisions are not enabled on this connection.")
        return self._read(
            "decide",
            {
                "proposal_id": arguments.get("proposal_id", ""),
                "expected_fingerprint": arguments.get("expected_fingerprint", ""),
                "decision": arguments.get("decision", ""),
            },
            host,
        )

    def _read(self, op: str, body: dict, host: GatewayHost) -> dict:
        result = self._call(op, body, host)
        if not result.ok:
            return _tool_error(result.error)
        return _tool_json(result.data)

    def _call(self, op: str, body: dict, host: GatewayHost) -> GatewayResult:
        if host.session:
            body = {**body, "external_session_ref": host.session}
        return self._runtime.dispatch(
            adapter_id="chatgpt",
            credential=self._config.gateway_credential,
            op=op,
            body=body,
            host=GatewayHost(
                connection_authenticated=True,
                subject=host.subject,
                organization=host.organization,
                session=host.session,
            ),
        )


def _host_from_meta(meta: dict) -> GatewayHost:
    return GatewayHost(
        connection_authenticated=False,
        subject=_meta_value(meta, "openai/subject"),
        organization=_meta_value(meta, "openai/organization"),
        session=_meta_value(meta, "openai/session"),
    )


def _meta_value(meta: dict, key: str) -> str:
    value = meta.get(key)
    if value is None and isinstance(meta.get("openai"), dict):
        short = key.split("/", 1)[-1]
        value = meta["openai"].get(short)
    return value.strip() if isinstance(value, str) else ""


def _forbidden(arguments: dict) -> bool:
    return any(str(key).lower() in FORBIDDEN_CLAIM_KEYS for key in arguments)


def _attention() -> dict:
    return _spec(
        "list_attention",
        "List active continuity handles and pending proposals already stored in Wally.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _READ_ONLY,
    )


def _matter() -> dict:
    return _spec(
        "get_matter",
        "Read one ActiveMatter, its canonical Matter status, and related proposals.",
        {
            "type": "object",
            "properties": {"active_matter_id": {"type": "string"}},
            "required": ["active_matter_id"],
            "additionalProperties": False,
        },
        _READ_ONLY,
    )


def _proposals() -> dict:
    return _spec(
        "list_proposals",
        "List proposal id, intent, status, and fingerprint.",
        {
            "type": "object",
            "properties": {"matter_id": {"type": "string"}},
            "additionalProperties": False,
        },
        _READ_ONLY,
    )


def _lifecycle() -> dict:
    return _spec(
        "get_lifecycle",
        "Read stored execution and verification status for one correlation or execution.",
        {
            "type": "object",
            "properties": {
                "correlation_id": {"type": "string"},
                "execution_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        _READ_ONLY,
    )


def _submit() -> dict:
    return _spec(
        "submit_request",
        "Submit untrusted intent. Wally grounds it and returns a canonical proposal when it can.",
        {
            "type": "object",
            "properties": {
                "utterance": {"type": "string"},
                "prior_user": {"type": "string"},
                "assistant_summary": {"type": "string"},
                "message_ref": {"type": "string"},
                "active_matter_id": {"type": "string"},
                "document_hint": {"type": "string"},
                "recipient_hint": {"type": "string"},
                "continue_correlation_id": {"type": "string"},
            },
            "required": ["utterance"],
            "additionalProperties": False,
        },
        _WRITE,
    )


def _link() -> dict:
    return _spec(
        "link_session",
        "Attach the authenticated ChatGPT session to an existing ActiveMatter.",
        {
            "type": "object",
            "properties": {"active_matter_id": {"type": "string"}},
            "required": ["active_matter_id"],
            "additionalProperties": False,
        },
        _WRITE,
    )


def _visibility() -> dict:
    return _spec(
        "set_handle_visibility",
        "Archive or reopen a continuity handle. This does not resolve the canonical Matter.",
        {
            "type": "object",
            "properties": {
                "active_matter_id": {"type": "string"},
                "visibility": {"type": "string", "enum": ["active", "archived"]},
            },
            "required": ["active_matter_id", "visibility"],
            "additionalProperties": False,
        },
        _WRITE,
    )


def _decision() -> dict:
    return _spec(
        "record_decision",
        "Approve or reject one exact proposal version. The host must confirm this write.",
        {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "expected_fingerprint": {"type": "string"},
                "decision": {"type": "string", "enum": ["approve", "reject"]},
            },
            "required": ["proposal_id", "expected_fingerprint", "decision"],
            "additionalProperties": False,
        },
        _DECISION,
    )


def _spec(name: str, description: str, schema: dict, annotations: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": annotations,
    }


def _tool_json(data: dict) -> dict:
    return {
        "content": [{"type": "text", "text": _brief(data)}],
        "structuredContent": data,
        "isError": False,
    }


def _tool_error(message: str) -> dict:
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"ok": False, "error": message},
        "isError": True,
    }


def _brief(data: dict) -> str:
    if "proposal" in data and isinstance(data["proposal"], dict):
        proposal = data["proposal"]
        return (
            f"Proposal {proposal.get('id')} is {proposal.get('status')} "
            f"({proposal.get('intent')}). Fingerprint {proposal.get('fingerprint')}."
        )
    if "proposals" in data and "active_matters" in data:
        return (
            f"{len(data['active_matters'])} active handles, "
            f"{len(data['proposals'])} proposals."
        )
    if "id" in data and "fingerprint" in data:
        return f"Proposal {data['id']} is {data.get('status')}."
    return "Wally returned stored status."


def _ok(request_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: object, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32001, "message": message},
    }


def _bearer(authorization: str) -> str | None:
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return urlsafe_b64encode(digest).decode().rstrip("=")


def _same_secret(given: str, expected: str) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(given.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )
