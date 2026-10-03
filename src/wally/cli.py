"""Interactive CLI for Wally."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wally.app import create_app
from wally.cli_knowledge import (
    handle_knowledge_command,
    print_pending_banner,
    prompt_pending_classifications,
)
from wally.config.loader import find_project_root
from wally.exceptions import (
    ConfigurationError,
    ExecutionRequestError,
    ProposalDecisionError,
    ProviderUnavailableError,
    WallyError,
)
from wally.models.messages import Session
from wally.models.ops import VerificationOutcome
from wally.ops.act import format_execution, format_executions
from wally.ops.decisions import UserDecision

CLI_CHANNEL = "cli"
REPL_CHANNEL = "repl"

BANNER = """Wally v0.16.0 — personal AI operating system
Type a message to talk to Wally.
Commands: /help /new /health /sessions /knowledge /brief /approvals /execute /exit
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wally — personal AI operating system")
    parser.add_argument(
        "--config",
        default=None,
        help="Config profile name (default: WALLY_CONFIG or macbook)",
    )
    parser.add_argument(
        "--profile",
        choices=["fast", "balanced", "deep"],
        default=None,
        help=(
            "Reasoning profile override for development and debugging "
            "(default: automatic routing; also WALLY_REASONING_PROFILE)"
        ),
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Resume an existing session ID",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root path (auto-detected by default)",
    )
    parser.add_argument(
        "--skip-classification-prompt",
        action="store_true",
        help="Skip interactive pending database classification at startup",
    )
    sub = parser.add_subparsers(dest="cli_command")
    brief = sub.add_parser("brief", help="Generate an operational Chief-of-Staff brief")
    brief.add_argument("--json", action="store_true", dest="brief_json")
    brief.add_argument(
        "--no-refresh",
        action="store_true",
        help="Render from stored matters without observing sources again",
    )
    brief.add_argument(
        "--since",
        default=None,
        help="ISO timestamp; recently resolved items after this time",
    )
    approvals = sub.add_parser("approvals", help="Show the Approval Inbox")
    approvals.add_argument("--json", action="store_true", dest="approvals_json")
    approvals.add_argument(
        "--refresh",
        action="store_true",
        help="Observe sources before listing proposals",
    )
    approve = sub.add_parser("approve", help="Approve a pending proposal without executing it")
    approve.add_argument("proposal_id")
    approve.add_argument("--note", default="", help="Optional short note stored with the decision")
    reject = sub.add_parser("reject", help="Reject a pending proposal")
    reject.add_argument("proposal_id")
    reject.add_argument("--note", default="")
    defer = sub.add_parser("defer", help="Defer a pending proposal until a date")
    defer.add_argument("proposal_id")
    defer.add_argument("--until", required=True, help="Date or ISO timestamp")
    defer.add_argument("--note", default="")
    execute = sub.add_parser(
        "execute",
        help="Execute an approved proposal (asks again before anything runs)",
    )
    execute.add_argument("proposal_id")
    executions = sub.add_parser("executions", help="List execution attempts")
    executions.add_argument("--proposal", default=None, dest="execution_proposal")
    execution = sub.add_parser("execution", help="Show one execution attempt")
    execution.add_argument("execution_id")
    verify = sub.add_parser(
        "verify",
        help="Re-check an execution from stored evidence (read-only), or record your own check",
    )
    verify.add_argument("execution_id")
    verify.add_argument(
        "--confirm",
        choices=["success", "failure"],
        default=None,
        help="Record the outcome you checked yourself for an unverified execution",
    )
    return parser


def _print_health(app) -> None:
    health = app.orchestrator.check_health()
    for provider, ok in health.items():
        status = "ready" if ok else "not configured"
        print(f"  {provider}: {status}")
    hints: list[str] = []
    if not health.get("openai", False):
        hints.append("Set OPENAI_API_KEY in .env or your shell for reasoning.")
    if app.settings.knowledge_enabled and not health.get("knowledge", False):
        hints.append("Set NOTION_API_KEY and share Notion databases with your integration.")
    if app.settings.workflow_enabled and not health.get("workflow", False):
        hints.append(
            "Set N8N_WEBHOOK_BASE_URL and configure config/workflows.yaml for workflows."
        )
    if app.settings.communications_enabled and not health.get("communications", False):
        hints.append(
            "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN. "
            "If you see invalid_grant, re-run scripts/google_auth.py and replace the "
            "refresh token in .env."
        )
    if app.settings.web_enabled and not health.get("web", False):
        hints.append("Set OPENAI_API_KEY for web search (OpenAI web_search adapter).")
    if app.settings.finance_enabled and not health.get("finance", False):
        hints.append(
            "Enable knowledge (NOTION_API_KEY) for finance. "
            "Enable workflows (N8N_WEBHOOK_BASE_URL) for payments."
        )
    if app.settings.secrets_enabled and not health.get("secrets", False):
        hints.append(
            "Install 1Password CLI, run `op signin`, and set providers.secrets.adapter: op_cli."
        )
    if app.knowledge_registry is not None:
        pending = app.knowledge_registry.list_pending()
        if pending:
            hints.append(
                f"{len(pending)} database(s) pending classification — "
                "see banner above or run /knowledge pending"
            )
    for hint in hints:
        print(f"  → {hint}")


def _run_brief(app, args) -> int:
    text = _render_brief(
        app,
        refresh=not args.no_refresh,
        since=args.since,
        as_json=args.brief_json,
    )
    if text is None:
        return 1
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def _print_brief(app, *, refresh: bool = True, context=None) -> None:
    text = _render_brief(app, refresh=refresh, since=None, as_json=False, context=context)
    if text is None:
        return
    print("\n" + text)


def _request(app, channel: str, *, session_id: str = ""):
    """Channel adapter: the operator at this terminal is the authenticated principal.

    The adapter only asks for a context. Whether that context may decide, execute,
    or verify is checked by the application service, not here.
    """
    return app.authority.issue(channel, external_session_ref=session_id)


def _render_brief(
    app,
    *,
    refresh: bool,
    since: str | None,
    as_json: bool,
    context=None,
) -> str | None:
    if app.ops is None:
        print("Operational brief is disabled. Set ops.enabled: true in config.", file=sys.stderr)
        return None
    try:
        return app.ops.render(
            refresh=refresh,
            since=since,
            as_json=as_json,
            context=context or _request(app, CLI_CHANNEL),
        )
    except ProviderUnavailableError as exc:
        print(f"Brief failed: {exc}", file=sys.stderr)
        return None


def _print_help() -> None:
    print(
        "\nCommands:\n"
        "  /help       Show this help\n"
        "  /new        Start a new session\n"
        "  /sessions   List recent session IDs\n"
        "  /health     Check provider status\n"
        "  /knowledge  Manage knowledge database classifications\n"
        "  /brief      Generate an operational brief, including suggestions\n"
        "  /approvals  Show proposals waiting for a decision\n"
        "  /approve <id> [note]\n"
        "  /reject <id> [note]\n"
        "  /defer <id> --until <date> [note]\n"
        "  /execute <proposal-id>   Act on an approved proposal (asks again first)\n"
        "  /executions              List execution attempts\n"
        "  /execution <execution-id>\n"
        "  /verify <execution-id> [--confirm success|failure]\n"
        "  /exit       Quit\n"
        "\n"
        "Approving a proposal records your decision. It does not send, pay, or submit.\n"
        "Only /execute acts, and only for supported proposals. A bill review logs in\n"
        "to the trusted portal and stops; it never pays.\n"
    )


def _ops_required(app) -> bool:
    if app.ops is None:
        print(
            "Operational proposals are disabled. Set ops.enabled: true in config.",
            file=sys.stderr,
        )
        return False
    return True


def _print_decision_result(proposal) -> None:
    print(
        f"{proposal.status.value}: {proposal.id}\n"
        "Wally has not executed this proposal."
    )


def _run_approvals(app, *, refresh: bool, as_json: bool) -> int:
    if not _ops_required(app):
        return 1
    try:
        text = app.ops.approvals(
            refresh=refresh, as_json=as_json, context=_request(app, CLI_CHANNEL)
        )
    except ProviderUnavailableError as exc:
        print(f"Approvals failed: {exc}", file=sys.stderr)
        return 1
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def _run_stored_decision(app, args) -> int:
    if not _ops_required(app):
        return 1
    decision = {
        "approve": UserDecision.APPROVE,
        "reject": UserDecision.REJECT,
        "defer": UserDecision.DEFER,
    }[args.cli_command]
    try:
        proposal = app.ops.decide(
            args.proposal_id,
            decision=decision,
            context=_request(app, CLI_CHANNEL),
            note=getattr(args, "note", "") or "",
            defer_until=getattr(args, "until", "") or "",
        )
    except (ProposalDecisionError, ProviderUnavailableError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_decision_result(proposal)
    return 0


def _act_required(app) -> bool:
    if getattr(app, "act", None) is None:
        print(
            "Execution is disabled. Set ops.enabled: true in config.",
            file=sys.stderr,
        )
        return False
    return True


def _print_execution_report(report) -> None:
    print(report.message)
    if report.execution is not None:
        print(f"Execution: {report.execution.id} ({report.execution.status.value})")


def _run_execute(app, proposal_id: str, *, context) -> int:
    if not _act_required(app):
        return 1
    try:
        report = app.act.execute(proposal_id, context=context)
    except (ExecutionRequestError, ProviderUnavailableError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_execution_report(report)
    return 1 if report.blocked else 0


def _run_verify(app, execution_id: str, *, confirm: str | None, context) -> int:
    if not _act_required(app):
        return 1
    outcome = None
    if confirm == "success":
        outcome = VerificationOutcome.VERIFIED_SUCCESS
    elif confirm == "failure":
        outcome = VerificationOutcome.VERIFIED_FAILURE
    try:
        report = app.act.verify(execution_id, context=context, confirm=outcome)
    except ExecutionRequestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_execution_report(report)
    return 1 if report.blocked else 0


def _run_executions(app, *, proposal_id: str | None) -> int:
    if not _act_required(app):
        return 1
    print(format_executions(app.act.list_executions(proposal_id=proposal_id)))
    return 0


def _run_execution(app, execution_id: str) -> int:
    if not _act_required(app):
        return 1
    try:
        execution = app.act.get_execution(execution_id)
    except ExecutionRequestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(format_execution(execution))
    return 0


def parse_repl_ops_command(user_input: str) -> dict | None:
    """Parse an inbox or execution command. Returns None when the line is not one.

    The verb is case-insensitive. Ids are not lowercased.
    """
    parts = user_input.split()
    if not parts:
        return None
    verb = parts[0].lower()
    if verb == "/approvals":
        return {
            "action": "approvals",
            "as_json": "--json" in parts[1:],
            "refresh": "--refresh" in parts[1:],
        }
    if verb == "/executions":
        return {"action": "executions"}
    if verb in {"/execute", "/execution", "/verify"}:
        if len(parts) < 2:
            raise ExecutionRequestError(f"{verb} needs an id.")
        confirm = None
        if verb == "/verify" and "--confirm" in parts[2:]:
            index = parts.index("--confirm")
            if index + 1 >= len(parts) or parts[index + 1] not in {"success", "failure"}:
                raise ExecutionRequestError("--confirm needs success or failure.")
            confirm = parts[index + 1]
        return {"action": verb[1:], "id": parts[1], "confirm": confirm}
    if verb not in {"/approve", "/reject", "/defer"}:
        return None
    if len(parts) < 2:
        raise ProposalDecisionError(f"{verb} needs a proposal id.")
    proposal_id = parts[1]
    rest = parts[2:]
    until = ""
    if verb == "/defer":
        if "--until" not in rest:
            raise ProposalDecisionError("defer needs --until <date>.")
        index = rest.index("--until")
        if index + 1 >= len(rest):
            raise ProposalDecisionError("defer needs --until <date>.")
        until = rest[index + 1]
        rest = rest[:index] + rest[index + 2 :]
    note = " ".join(token for token in rest if token not in {"--json", "--refresh"})
    return {
        "action": verb[1:],
        "proposal_id": proposal_id,
        "until": until,
        "note": note,
    }


def _run_repl_ops(app, user_input: str, *, session_id: str = "") -> bool:
    """Handle an inbox command. Returns True when the line was an inbox command."""
    try:
        parsed = parse_repl_ops_command(user_input)
    except (ProposalDecisionError, ExecutionRequestError) as exc:
        print(str(exc))
        return True
    if parsed is None:
        return False
    context = _request(app, REPL_CHANNEL, session_id=session_id)
    if parsed["action"] == "execute":
        _run_execute(app, parsed["id"], context=context)
        return True
    if parsed["action"] == "verify":
        _run_verify(app, parsed["id"], confirm=parsed["confirm"], context=context)
        return True
    if parsed["action"] == "executions":
        _run_executions(app, proposal_id=None)
        return True
    if parsed["action"] == "execution":
        _run_execution(app, parsed["id"])
        return True
    if not _ops_required(app):
        return True
    try:
        if parsed["action"] == "approvals":
            text = app.ops.approvals(
                refresh=parsed["refresh"], as_json=parsed["as_json"], context=context
            )
            print("\n" + text)
            return True
        proposal = app.ops.decide(
            parsed["proposal_id"],
            decision=UserDecision(parsed["action"]),
            context=context,
            note=parsed["note"],
            defer_until=parsed["until"],
        )
    except (ProposalDecisionError, ProviderUnavailableError) as exc:
        print(str(exc))
        return True
    _print_decision_result(proposal)
    return True


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        root = args.project_root or find_project_root()
        app = create_app(
            project_root=root,
            config_name=args.config,
            reasoning_profile=args.profile,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    command = getattr(args, "cli_command", None)
    if command == "brief":
        return _run_brief(app, args)
    if command == "approvals":
        return _run_approvals(app, refresh=args.refresh, as_json=args.approvals_json)
    if command in {"approve", "reject", "defer"}:
        return _run_stored_decision(app, args)
    if command == "execute":
        return _run_execute(app, args.proposal_id, context=_request(app, CLI_CHANNEL))
    if command == "executions":
        return _run_executions(app, proposal_id=args.execution_proposal)
    if command == "execution":
        return _run_execution(app, args.execution_id)
    if command == "verify":
        return _run_verify(
            app,
            args.execution_id,
            confirm=args.confirm,
            context=_request(app, CLI_CHANNEL),
        )

    session: Session = app.sessions.resume_or_create(args.session)
    dry_run = "on" if app.settings.dry_run else "off"

    print(BANNER)
    print(f"Session: {session.id}")
    if app.settings.reasoning_profile_override:
        profile_label = (
            f"{app.settings.reasoning_profile_override} (override)"
        )
    else:
        profile_label = f"auto (default {app.settings.default_reasoning_profile})"

    print(
        f"Environment: {app.settings.environment} | "
        f"Profile: {profile_label} | "
        f"Dry-run: {dry_run}"
    )
    if app.knowledge_registry is not None:
        print_pending_banner(app.knowledge_registry)
    _print_health(app)

    if (
        app.knowledge_registry is not None
        and not args.skip_classification_prompt
        and sys.stdin.isatty()
    ):
        prompt_pending_classifications(
            app.knowledge_registry,
            knowledge=app.knowledge,
            audit=app.audit,
            session_id=session.id,
        )

    print()

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 0

        if not user_input:
            continue

        if user_input.startswith("/"):
            verb = user_input.split()[0].lower()
            if verb in {"/exit", "/quit"}:
                print("Goodbye.")
                return 0
            if verb == "/help":
                _print_help()
                continue
            if verb == "/new":
                session = app.sessions.create_session()
                print(f"New session: {session.id}")
                continue
            if verb == "/sessions":
                sessions = app.sessions.list_sessions(limit=10)
                if not sessions:
                    print("No sessions yet.")
                else:
                    for item in sessions:
                        count = app.sessions.message_count(item.id)
                        print(f"  {item.id}  ({count} messages, {item.created_at.date()})")
                continue
            if verb == "/health":
                _print_health(app)
                continue
            if verb == "/knowledge" or user_input.lower().startswith("/knowledge"):
                handle_knowledge_command(
                    user_input,
                    registry=app.knowledge_registry,
                    knowledge=app.knowledge,
                    audit=app.audit,
                    session_id=session.id,
                )
                continue
            if verb == "/brief":
                _print_brief(
                    app,
                    refresh="--no-refresh" not in user_input.split(),
                    context=_request(app, REPL_CHANNEL, session_id=session.id),
                )
                continue
            if _run_repl_ops(app, user_input, session_id=session.id):
                continue
            print(f"Unknown command: {user_input}. Type /help for options.")
            continue

        try:
            response = app.orchestrator.handle(session, user_input)
        except ProviderUnavailableError as exc:
            print(f"\nwally> {exc}")
            print("I cannot proceed without the language model.\n")
            continue
        except WallyError as exc:
            print(f"\nwally> Something went wrong: {exc}\n")
            continue

        print(f"\nwally> {response.content}\n")
