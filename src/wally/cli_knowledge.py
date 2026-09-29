"""Knowledge registry CLI commands."""

from __future__ import annotations

import getpass
import shlex
import sys

from wally.audit.logger import AuditLogger
from wally.knowledge.registry import DatabaseRecord, KnowledgeRegistry
from wally.models.knowledge import KnowledgeClass
from wally.providers.knowledge import KnowledgeProvider


def _approver_name() -> str:
    return f"user:{getpass.getuser()}"


def _print_record(record: DatabaseRecord, *, detailed: bool = False) -> None:
    status = record.classification.value
    writable = "yes" if record.classification == KnowledgeClass.OPERATIONAL else "no"
    print(f"  {record.name} ({record.registry_key})")
    print(f"    id: {record.database_id}")
    print(f"    classification: {status} | writable: {writable}")
    if record.approved_at:
        print(f"    approved: {record.approved_by} at {record.approved_at.isoformat()}")
    if detailed and record.recommendation:
        rec = record.recommendation
        print(f"    recommendation: {rec.classification.value}")
        print(f"    reasoning: {rec.reasoning}")
        if rec.signals:
            print(f"    signals: {', '.join(rec.signals)}")


def _recommended_classification(record: DatabaseRecord) -> KnowledgeClass:
    if record.recommendation:
        return record.recommendation.classification
    return KnowledgeClass.OPERATIONAL


def parse_classification_answer(
    answer: str,
    *,
    recommended: KnowledgeClass,
) -> KnowledgeClass | None:
    """Parse interactive input. None means skip."""
    normalized = answer.strip().lower()
    if normalized in {"", "y", "yes"}:
        return recommended
    if normalized in {"n", "no"}:
        return (
            KnowledgeClass.GOVERNANCE
            if recommended == KnowledgeClass.OPERATIONAL
            else KnowledgeClass.OPERATIONAL
        )
    if normalized in {"g", "governance", "gov"}:
        return KnowledgeClass.GOVERNANCE
    if normalized in {"o", "operational", "op"}:
        return KnowledgeClass.OPERATIONAL
    if normalized in {"s", "skip"}:
        return None
    return recommended


def approve_database(
    registry: KnowledgeRegistry,
    key_or_id: str,
    classification: KnowledgeClass,
    *,
    knowledge: KnowledgeProvider | None,
    audit: AuditLogger,
    session_id: str,
    approved_by: str | None = None,
) -> DatabaseRecord:
    record = registry.approve(
        key_or_id,
        classification,
        approved_by=approved_by or _approver_name(),
    )
    if knowledge is not None:
        knowledge.refresh()
    audit.log_simple(
        event_type="knowledge_classification_approved",
        session_id=session_id,
        outcome="success",
        provider="knowledge",
        parameters={
            "database_id": record.database_id,
            "registry_key": record.registry_key,
            "classification": record.classification.value,
            "approved_by": record.approved_by,
        },
    )
    return record


def print_pending_banner(registry: KnowledgeRegistry) -> None:
    pending = registry.list_pending()
    if not pending:
        return
    print(f"\nPending classification ({len(pending)} database(s)):")
    for record in pending:
        rec = record.recommendation
        hint = f" → recommend {rec.classification.value}" if rec else ""
        print(f"  • {record.name} ({record.registry_key}){hint}")
    print("  Use /knowledge pending to review, or approve now at the prompt below.\n")


def prompt_pending_classifications(
    registry: KnowledgeRegistry,
    *,
    knowledge: KnowledgeProvider | None,
    audit: AuditLogger,
    session_id: str,
) -> int:
    """Interactively approve pending databases. Returns count approved."""
    pending = registry.list_pending()
    if not pending:
        return 0

    print(
        "Classify each database so Wally knows whether it may write to it.\n"
        "  Y or Enter — accept recommendation\n"
        "  n       — opposite of recommendation\n"
        "  g / o   — governance (read-only) / operational (writable)\n"
        "  s       — skip for now\n"
    )

    approved = 0
    for record in pending:
        recommended = _recommended_classification(record)
        writable = "writable" if recommended == KnowledgeClass.OPERATIONAL else "read-only"
        print(f"--- {record.name} ({record.registry_key}) ---")
        if record.recommendation:
            print(f"Recommendation: {recommended.value} ({writable})")
            print(f"Reason: {record.recommendation.reasoning}")
        prompt = (
            f"Classify as {recommended.value}? [Y/n/g/o/s] "
            if recommended == KnowledgeClass.OPERATIONAL
            else f"Classify as {recommended.value} (read-only)? [Y/n/g/o/s] "
        )
        try:
            answer = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\nStopped classification review. Remaining databases stay pending.")
            break

        classification = parse_classification_answer(answer, recommended=recommended)
        if classification is None:
            print("Skipped.\n")
            continue

        record = approve_database(
            registry,
            record.database_id,
            classification,
            knowledge=knowledge,
            audit=audit,
            session_id=session_id,
        )
        approved += 1
        writable_result = "yes" if record.classification == KnowledgeClass.OPERATIONAL else "no"
        print(f"Approved as {record.classification.value}. Writable: {writable_result}.\n")

    remaining = len(registry.list_pending())
    if remaining:
        print(f"{remaining} database(s) still pending. Run /knowledge pending anytime.")
    elif approved:
        print("All databases classified.")

    return approved


def handle_knowledge_command(
    command_line: str,
    *,
    registry: KnowledgeRegistry | None,
    knowledge: KnowledgeProvider | None,
    audit: AuditLogger,
    session_id: str,
) -> bool:
    """Handle /knowledge subcommands. Returns True if handled."""
    parts = shlex.split(command_line)
    if not parts or parts[0].lower() != "/knowledge":
        return False

    if registry is None:
        print("Knowledge registry is not available. Check NOTION_API_KEY and connectivity.")
        return True

    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        records = registry.list_all()
        if not records:
            print("No databases in registry.")
            return True
        print(f"Knowledge databases ({len(records)}):\n")
        for record in records:
            _print_record(record)
        return True

    if sub == "pending":
        records = registry.list_pending()
        if not records:
            print("No databases pending classification.")
            return True
        print(f"Pending classification ({len(records)}):\n")
        for record in records:
            _print_record(record, detailed=True)
        if sys.stdin.isatty():
            print_pending_banner(registry)
            print("\nStarting interactive review...\n")
            prompt_pending_classifications(
                registry,
                knowledge=knowledge,
                audit=audit,
                session_id=session_id,
            )
        else:
            print("\nApprove with: /knowledge approve <name-or-id> operational|governance")
        return True

    if sub == "review":
        if len(parts) < 3:
            print("Usage: /knowledge review <name-or-id>")
            return True
        record = registry.find_by_key_or_id(parts[2])
        if record is None:
            print(f"Database not found: {parts[2]}")
            return True
        print(f"Review: {record.name}\n")
        _print_record(record, detailed=True)
        if record.classification == KnowledgeClass.PENDING and sys.stdin.isatty():
            recommended = _recommended_classification(record)
            try:
                answer = input(
                    f"\nClassify as {recommended.value}? [Y/n/g/o/s] "
                )
            except (EOFError, KeyboardInterrupt):
                print("\nSkipped.")
                return True
            classification = parse_classification_answer(answer, recommended=recommended)
            if classification is not None:
                record = approve_database(
                    registry,
                    record.database_id,
                    classification,
                    knowledge=knowledge,
                    audit=audit,
                    session_id=session_id,
                )
                writable = "yes" if record.classification == KnowledgeClass.OPERATIONAL else "no"
                print(f"Approved as {record.classification.value}. Writable: {writable}")
        return True

    if sub == "approve":
        if len(parts) < 4:
            print("Usage: /knowledge approve <name-or-id> operational|governance")
            return True
        target = parts[2]
        try:
            classification = KnowledgeClass(parts[3].lower())
        except ValueError:
            print("Classification must be operational or governance.")
            return True
        if classification == KnowledgeClass.PENDING:
            print("Cannot approve to pending.")
            return True

        try:
            record = approve_database(
                registry,
                target,
                classification,
                knowledge=knowledge,
                audit=audit,
                session_id=session_id,
            )
        except ValueError as exc:
            print(str(exc))
            return True

        print(
            f"Approved '{record.name}' as {record.classification.value}. "
            f"Writable: {'yes' if record.classification == KnowledgeClass.OPERATIONAL else 'no'}"
        )
        return True

    print(
        "Knowledge commands:\n"
        "  /knowledge list\n"
        "  /knowledge pending    Review and classify interactively\n"
        "  /knowledge review <name-or-id>\n"
        "  /knowledge approve <name-or-id> operational|governance"
    )
    return True
