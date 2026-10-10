"""Explicit opt-in: python -m wally.codex. Never started by normal App bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import httpx

from wally.adapters.notion.edits import NotionEditBackend
from wally.audit.logger import AuditLogger
from wally.codex.config import ConfiguredConfirmation, load_config
from wally.codex.service import (
    CODEX_CHANNEL,
    CODEX_POLICY,
    CODEX_TELEGRAM_POLICY,
    CodexEditAdapter,
    registration,
)
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


def build_adapter(settings, *, policy_path: Path | None = None, approval_channel="telegram"):
    policy_path = policy_path or settings.project_root / "config" / "notion-edits.yaml"
    config = load_config(policy_path)
    authority = PrincipalAuthority(
        {CODEX_CHANNEL: CODEX_POLICY if approval_channel == "local" else CODEX_TELEGRAM_POLICY},
        human_confirmers={CODEX_CHANNEL: ConfiguredConfirmation(policy_path, config.confirmer)}
        if config.confirmer and approval_channel == "local"
        else {},
    )
    store = OperationsStore(settings.ops_database)
    service = build_edit_service(settings, store, authority, policy_path)
    adapter = registration()
    gateway = GatewayRuntime(
        store,
        authority,
        (adapter,),
        notion_edits=service,
        audit=AuditLogger(settings.audit_directory),
    )
    return CodexEditAdapter(
        gateway, adapter, telegram_approval=approval_channel == "telegram",
        telegram_policy_provider=lambda: load_config(policy_path),
    )


def build_edit_service(settings, store, authority, policy_path, *, telegram_gate=None):
    """Same restricted Notion composition for proposal origin and trusted worker."""
    config = load_config(policy_path)
    registry = KnowledgeRegistry(settings.knowledge_registry_database)

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
    return NotionEditService(
        store,
        authority,
        backend,
        config.targets,
        writes_enabled=(config.telegram_writes_enabled if telegram_gate else config.writes_enabled)
        and not settings.dry_run,
        targets_provider=lambda: load_config(policy_path).targets,
        write_gate=(
            lambda: (
                load_config(policy_path).telegram_writes_enabled
                and not settings.dry_run
                and telegram_gate()
            )
        )
        if telegram_gate
        else (lambda: load_config(policy_path).writes_enabled and not settings.dry_run),
    )


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
    parser.add_argument("--approval-channel", choices=("telegram", "local"), default="telegram")
    parser.add_argument(
        "--edit-policy", type=Path, help="Reviewed local policy, never a tool argument"
    )
    parser.add_argument(
        "--state-dir", type=Path, help="Isolate registry, operations and audit state"
    )
    args = parser.parse_args()
    try:
        settings = load_settings(project_root=args.project_root, config_name=args.config)
        if args.state_dir:
            state = args.state_dir.resolve()
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            settings = replace(
                settings,
                ops_database=state / "operations.db",
                knowledge_registry_database=state / "registry.db",
                audit_directory=state / "audit",
            )
        options = {"policy_path": args.edit_policy}
        if args.approval_channel != "telegram":
            options["approval_channel"] = args.approval_channel
        serve_stdio(build_adapter(settings, **options), sys.stdin, sys.stdout)
    except WallyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
