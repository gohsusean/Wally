"""Read-time comparisons; observed source state never becomes verified evidence.

Only bounded enum values and business-property hashes persist. Writer attribution
is unknown unless a separate, independently verified Wally attempt supplies it.
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime

from wally.models.ops import ExecutionStatus
from wally.runtime.confirmation import canonical, digest


class NotionReconciliation:
    def __init__(self, store):
        self.store = store

    def refresh_verified(self, target, snapshot, execution, provenance):
        # Explicit verify only: retain the historical attempt and publish fresh read evidence.
        with self.store._connect() as conn:  # noqa: SLF001
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM notion_edit_claims WHERE page_id=?", (target.page_id,)
            ).fetchone():
                from wally.ops.notion_edits import EditError

                raise EditError("An unresolved write prevents refreshing verification.")
            conn.execute(
                "INSERT INTO notion_trusted_snapshots VALUES (?,?,?,?,0) "
                "ON CONFLICT(page_id) DO UPDATE SET target_digest=excluded.target_digest, "
                "snapshot=excluded.snapshot,execution_id=excluded.execution_id,stale=0",
                (target.page_id, target.fingerprint, canonical(asdict(snapshot)), execution.id),
            )
            self.store._edit_event(  # noqa: SLF001
                conn,
                "notion_verification_refreshed",
                {"execution_id": execution.id, "snapshot_digest": digest(asdict(snapshot))},
                provenance,
            )

    def unavailable(self, target, provenance):
        with self.store._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE notion_trusted_snapshots SET stale=1 WHERE page_id=?", (target.page_id,)
            )
            self.store._edit_event(  # noqa: SLF001
                conn, "notion_reconciliation_unavailable", {"target": target.key}, provenance
            )

    def compare(self, target, snapshot, backend, provenance):
        now = datetime.now(UTC).isoformat()
        with self.store._connect() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT * FROM notion_trusted_snapshots WHERE page_id=?", (target.page_id,)
            ).fetchone()
            if row is None:
                # A finite catalog comparison can supply prior certified metadata.
                initial = asdict(
                    getattr(backend, "catalog_snapshot", lambda t, s: s)(target, snapshot)
                )
                execution_id, target_digest = "", target.fingerprint
                # Additive rollout: recover a baseline from existing durable verified attempts.
                for attempt in sorted(
                    self.store.list_executions(), key=lambda ex: ex.verified_at, reverse=True
                ):
                    if attempt.status != ExecutionStatus.VERIFIED_SUCCESS:
                        continue
                    spec = self.store.get_notion_edit(attempt.proposal_id)
                    if (
                        not spec
                        or spec["page_id"] != target.page_id
                        or attempt.plan_digest != digest(spec)
                    ):
                        continue
                    initial = json.loads(json.dumps(spec["before"]))
                    for change in spec["changes"]:
                        initial["values"][change["property_id"]] = change["after"]
                        initial["hashes"][change["property_id"]] = digest(
                            ["select", change["after"]]
                        )
                    execution_id, target_digest = attempt.id, spec["target_digest"]
                    break
                conn.execute(
                    "INSERT OR IGNORE INTO notion_trusted_snapshots VALUES (?,?,?,?,0)",
                    (target.page_id, target_digest, canonical(initial), execution_id),
                )
                conn.commit()  # Never hold the shared DB writer lock while invalidating finance.
                row = conn.execute(
                    "SELECT * FROM notion_trusted_snapshots WHERE page_id=?", (target.page_id,)
                ).fetchone()
            before = json.loads(row["snapshot"])
            keys = sorted(
                key
                for key in set(before["hashes"]) | set(snapshot.hashes)
                if before["hashes"].get(key) != snapshot.hashes.get(key)
            )
            contract_changed = (
                before["schema_digest"] != snapshot.schema_digest
                or row["target_digest"] != target.fingerprint
            )
            catalog_changed = before["finance_version"] != snapshot.finance_version
            changed = bool(keys or contract_changed or catalog_changed)
            affected = False
            if changed:
                reconcile = getattr(backend, "reconcile_certification", None)
                if reconcile is not None:
                    affected = reconcile(
                        target,
                        keys,
                        snapshot=snapshot,
                        baseline_finance_version=before["finance_version"],
                    )
            changes = []
            for rule in target.properties:
                if rule.property_id in keys:
                    changes.append(
                        {
                            "field": rule.field,
                            "before": before["values"].get(rule.property_id, "Unavailable"),
                            "after": snapshot.values.get(rule.property_id, "Unavailable"),
                        }
                    )
            stale = bool(row["stale"] or changed)
            result = {
                "state": "changed"
                if changed
                else (
                    "needs_review"
                    if stale
                    else ("unchanged" if row["execution_id"] else "source_observed")
                ),
                "verification_current": bool(row["execution_id"]) and not stale,
                "baseline": "verified" if row["execution_id"] else "observed",
                "origin": "unknown"
                if stale
                else ("verified_wally_execution" if row["execution_id"] else "unknown"),
                "changes": changes,
                "other_business_changes": len(
                    set(keys) - {r.property_id for r in target.properties}
                ),
                "contract_changed": contract_changed,
                "catalog_changed": catalog_changed,
                "certification_affected": affected,
            }
            if changed:
                event_id = digest(
                    [target.page_id, row["snapshot"], asdict(snapshot), target.fingerprint]
                )
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO notion_reconciliation_events VALUES (?,?,?,?,?,?)",
                    (
                        event_id,
                        target.page_id,
                        canonical({"target": target.key, "page_id": target.page_id, **result}),
                        now,
                        canonical(provenance.as_dict()),
                        canonical(keys),
                    ),
                ).rowcount
                conn.execute(
                    "UPDATE notion_trusted_snapshots SET stale=1 WHERE page_id=? AND snapshot=? "
                    "AND execution_id=?",
                    (target.page_id, row["snapshot"], row["execution_id"]),
                )
                if inserted:
                    # The event itself is the append-only reconciliation audit record.
                    result["event_id"] = event_id
            return result

    def events(self, *, current_only=False):
        with self.store._connect() as conn:  # noqa: SLF001
            return [
                (row["id"], json.loads(row["details"]))
                for row in conn.execute(
                    "SELECT e.id,e.details FROM notion_reconciliation_events e "
                    "JOIN notion_trusted_snapshots s ON e.page_id=s.page_id "
                    + ("WHERE s.stale=1 " if current_only else "")
                    + "ORDER BY e.created_at,e.id"
                )
            ]
