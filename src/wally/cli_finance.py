"""Authenticated local-owner finance operations. No conversational tool registration."""

from __future__ import annotations

import json
from pathlib import Path

from wally.finance import migration
from wally.finance.models import FinanceError, Kind, Scope
from wally.models.principal import Capability


def add_parser(sub):
    parser = sub.add_parser("finance", help="Local-owner financial catalog/certification")
    commands = parser.add_subparsers(dest="finance_command", required=True)
    commands.add_parser("status", help="Read candidate IDs, versions and readiness")
    designate = commands.add_parser("designate", help="Confirm a reviewed source mapping")
    designate.add_argument("source_key")
    for verb in ("certify", "enable", "revoke"):
        command = commands.add_parser(verb)
        command.add_argument("object_id")
        command.add_argument("--scope", choices=[s.value for s in Scope], default="identity")
        if verb == "certify":
            command.add_argument(
                "--proof",
                type=Path,
                required=True,
                help="JSON evidence references/hashes and owner attestations",
            )
    register = commands.add_parser(
        "intake", help="Register provisional facts or revise a local instance"
    )
    register.add_argument("kind", choices=[k.value for k in Kind])
    register.add_argument(
        "--facts", type=Path, required=True, help="Restricted JSON candidate facts"
    )
    register.add_argument("--revise", default="", help="Existing immutable local object ID")
    accept = commands.add_parser(
        "accept-portal", help="Auth-only acceptance of an approved review proposal"
    )
    accept.add_argument("proposal_id")
    migrate = commands.add_parser(
        "schema", help="Preview additive-only columns; --apply asks fresh approval"
    )
    migrate.add_argument("source_key")
    migrate.add_argument("--apply", action="store_true")


def _json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError):
        raise FinanceError("Cannot read the restricted JSON intake/proof file.") from None


def run(app, args, *, channel="cli") -> int:
    from wally.exceptions import WallyError

    context = app.authority.issue(channel)
    app.authority.authorize(context, Capability.READ_CONTEXT)
    catalog = app.finance_catalog
    if catalog is None:
        print("Certified finance catalog unavailable; operational knowledge must be configured.")
        return 1
    try:
        verb = args.finance_command
        if verb == "status":
            result = catalog.status()
        elif verb == "designate":
            catalog.designate(args.source_key, context=context)
            result = {"source": "designated", "rows": "provisional"}
            if app.knowledge is not None:
                app.knowledge.refresh()
        elif verb == "intake":
            record = catalog.register_local(
                Kind(args.kind), _json(args.facts), object_id=args.revise, context=context
            )
            result = {"id": record.id, "version": record.version, "state": "draft"}
        elif verb == "certify":
            proof = _json(args.proof)
            if set(proof) != {"evidence", "attestations"}:
                raise FinanceError("Proof needs evidence and owner attestations only.")
            certificate = catalog.certify(
                args.object_id, Scope(args.scope), context=context, **proof
            )
            result = {"certificate": certificate}
        elif verb in {"enable", "revoke"}:
            getattr(catalog, verb)(args.object_id, Scope(args.scope), context=context)
            result = {"operation": verb, "id": args.object_id, "scope": args.scope}
        elif verb == "accept-portal":
            if app.act is None:
                raise FinanceError("Act & Verify unavailable.")
            catalog._owner(context)
            report = app.act.execute(args.proposal_id, context=context, acceptance=True)
            print(report.message)
            return int(report.blocked)
        elif verb == "schema":
            matches = [s for s in catalog.sources() if s.key == args.source_key]
            if len(matches) != 1:
                raise FinanceError("Unknown or ambiguous source mapping.")
            result = (
                migration.apply(catalog, matches[0], context=context)
                if args.apply
                else migration.preview(catalog, matches[0])
            )
        else:
            raise FinanceError("Unknown finance operation.")
        print(json.dumps(result, indent=2))
        return 0
    except (FinanceError, WallyError):
        # Diagnostics are deliberately bounded; never echo input files or upstream errors.
        print(
            "Finance operation blocked: check candidate/schema, dependencies and local authority."
        )
        return 1
