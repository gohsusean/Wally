"""Local HTTP MCP endpoint. It does not listen unless ``serve`` is called."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from wally.audit.logger import AuditLogger
from wally.chatgpt.service import (
    ChatGPTAdapter,
    ChatGPTConfig,
    chatgpt_policy,
    chatgpt_registration,
    issue_token,
)
from wally.config.loader import Settings
from wally.gateway.service import GatewayRuntime
from wally.ops.request_propose import TrustedRecord
from wally.ops.service import ObserveBriefService
from wally.ops.store import OperationsStore
from wally.runtime.principals import LOCAL_OPERATOR_CHANNELS, PrincipalAuthority


def load_trusted_records(path: Path) -> tuple[TrustedRecord, ...]:
    if not path.is_file():
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    records: list[TrustedRecord] = []
    for kind in ("document", "recipient"):
        for item in raw.get(f"{kind}s", []) or []:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if record_id and title:
                records.append(TrustedRecord(id=record_id, title=title, kind=kind))
    return tuple(records)


def build_adapter(settings: Settings, config: ChatGPTConfig) -> ChatGPTAdapter:
    store = OperationsStore(settings.ops_database)
    authority = PrincipalAuthority(
        {**LOCAL_OPERATOR_CHANNELS, "chatgpt": chatgpt_policy(config)}
    )
    audit = AuditLogger(settings.audit_directory)
    ops = ObserveBriefService(store, audit=audit, authority=authority)
    records = load_trusted_records(settings.project_root / "config" / "chatgpt.yaml")
    runtime = GatewayRuntime(
        store,
        authority,
        (chatgpt_registration(config),),
        ops=ops,
        audit=audit,
        trusted_records=records,
        owner_subjects=config.allowed_subjects,
        owner_organizations=config.allowed_organizations,
    )
    return ChatGPTAdapter(runtime, config)


def bind(adapter: ChatGPTAdapter, host: str, port: int) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("The ChatGPT adapter listens on localhost only.")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(min(length, 65_536))
            if self.path == "/oauth/token":
                self._oauth(raw)
                return
            try:
                message = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "Request body must be an object."})
                return
            if not isinstance(message, dict):
                self._send(400, {"error": "Request body must be an object."})
                return
            meta = message.get("_meta") if isinstance(message.get("_meta"), dict) else {}
            result = adapter.handle(
                message,
                authorization=self.headers.get("Authorization", ""),
                meta=meta,
            )
            status = 401 if _unauthenticated(result) else 200
            self._send(status, result)

        def _oauth(self, raw: bytes) -> None:
            try:
                form = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                form = {}
            if not isinstance(form, dict):
                form = {}
            secret = str(form.get("owner_secret") or "")
            verifier = str(form.get("code_verifier") or "")
            code = adapter.connection.grant(secret, _challenge(verifier)) if verifier else None
            token = adapter.connection.exchange(code, verifier) if code else None
            if token is None:
                self._send(401, {"error": "access_denied"})
                return
            self._send(200, {"access_token": token, "token_type": "Bearer"})

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            if status == 401:
                self.send_header(
                    "WWW-Authenticate",
                    'Bearer realm="wally", error="invalid_token"',
                )
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

    return ThreadingHTTPServer((host, port), Handler)


def serve(adapter: ChatGPTAdapter, host: str, port: int) -> None:
    server = bind(adapter, host, port)
    print(f"Wally ChatGPT adapter on http://{host}:{server.server_address[1]}/mcp")
    if not adapter.tools() or not any(
        item["name"] == "record_decision" for item in adapter.tools()
    ):
        print("record_decision is disabled until host confirmation is configured.")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _challenge(verifier: str) -> str:
    from wally.chatgpt.service import _s256

    return _s256(verifier)


def _unauthenticated(result: dict) -> bool:
    error = result.get("error")
    return isinstance(error, dict) and error.get("message") == "Authentication required."


def config_from_env(env: dict[str, str]) -> ChatGPTConfig:
    subjects = _csv(env.get("WALLY_CHATGPT_ALLOWED_SUBJECTS", ""))
    orgs = _csv(env.get("WALLY_CHATGPT_ALLOWED_ORGS", ""))
    return ChatGPTConfig(
        owner_secret=env.get("WALLY_CHATGPT_OWNER_SECRET", ""),
        gateway_credential=env.get("WALLY_CHATGPT_GATEWAY_CREDENTIAL", ""),
        allowed_subjects=frozenset(subjects),
        allowed_organizations=frozenset(orgs),
        write_enabled=_flag(env.get("WALLY_CHATGPT_WRITE", "")),
        decision_confirmation=_flag(env.get("WALLY_CHATGPT_DECISION_CONFIRMATION", "")),
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


# Re-export for operators who mint a token from the REPL during setup.
__all__ = ["build_adapter", "config_from_env", "issue_token", "load_trusted_records", "serve"]
