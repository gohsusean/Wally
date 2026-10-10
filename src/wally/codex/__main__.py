"""Explicit opt-in: python -m wally.codex. Never started by normal App bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from wally.adapters.notion.edits import NotionEditBackend
from wally.audit.logger import AuditLogger
from wally.codex.config import ConfiguredConfirmation, load_config
from wally.codex.service import CODEX_CHANNEL, CODEX_POLICY, CodexEditAdapter, registration
from wally.config.loader import load_settings
from wally.exceptions import WallyError
from wally.finance.config import load_profiles, load_sources
from wally.finance.notion import NotionFinanceReader
from wally.finance.service import FinanceService
from wally.finance.store import FinanceStore
from wally.gateway.service import GatewayRuntime
from wally.knowledge.registry import KnowledgeRegistry
from wally.ops.notion_edits import EditError, NotionEditService
from wally.ops.store import OperationsStore
from wally.runtime.principals import PrincipalAuthority


class NoFinancialConfirmation:
    def request_approval(self, *args, **kwargs):
        return False


def build_adapter(settings):
    policy_path = settings.project_root / "config" / "notion-edits.yaml"
    config = load_config(policy_path)
    authority = PrincipalAuthority(
        {CODEX_CHANNEL: CODEX_POLICY},
        human_confirmers={CODEX_CHANNEL: ConfiguredConfirmation(policy_path, config.confirmer)}
        if config.confirmer
        else {},
    )
    registry = KnowledgeRegistry(settings.knowledge_registry_database)
    store = OperationsStore(settings.ops_database)

    def client():
        # Called for read-client construction and again at authorized write time.
        # No secret is a CLI argument, tool result, policy field or audit parameter.
        key = os.environ.get("NOTION_API_KEY")
        if not key:
            raise EditError("Notion credential unavailable.")
        return httpx.Client(
            base_url="https://api.notion.com/v1",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
            follow_redirects=False,
        )

    # A missing read credential still permits an empty, safely disabled runtime.
    reader = NotionFinanceReader(client()) if config.targets else None
    finance_path = settings.project_root / "config" / "finance.yaml"
    finance = FinanceService(
        FinanceStore(settings.ops_database),
        authority=authority,
        approval=NoFinancialConfirmation(),
        registry=registry,
        reader=reader,
        sources=lambda: load_sources(finance_path),
        profiles=lambda: load_profiles(finance_path),
        dry_run=settings.dry_run,
    )
    backend = NotionEditBackend(reader, registry, finance=finance, write_client=client)
    service = NotionEditService(
        store,
        authority,
        backend,
        config.targets,
        writes_enabled=config.writes_enabled and not settings.dry_run,
        targets_provider=lambda: load_config(policy_path).targets,
        write_gate=lambda: load_config(policy_path).writes_enabled and not settings.dry_run,
    )
    adapter = registration()
    gateway = GatewayRuntime(
        store,
        authority,
        (adapter,),
        notion_edits=service,
        audit=AuditLogger(settings.audit_directory),
    )
    return CodexEditAdapter(gateway, adapter)


def serve_stdio(adapter, input_stream, output_stream):
    while True:
        # Bounded line framing; never accept an unbounded tool/evidence payload.
        line = input_stream.readline(65537)
        if not line:
            return
        if len(line) > 65536:
            raise EditError("MCP message exceeds the allowed size.")
        try:
            message = json.loads(line)
        except ValueError:
            message = None
        if not isinstance(message, dict):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Malformed MCP message.",
                },
            }
        else:
            response = adapter.handle(message)
        if response is not None:
            output_stream.write(json.dumps(response) + "\n")
            output_stream.flush()


def main():
    parser = argparse.ArgumentParser(
        description="Wally scoped-edit local MCP (disabled by default)"
    )
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--config")
    args = parser.parse_args()
    try:
        settings = load_settings(project_root=args.project_root, config_name=args.config)
        serve_stdio(build_adapter(settings), sys.stdin, sys.stdout)
    except WallyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
