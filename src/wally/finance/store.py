"""Additive finance records in the operational database. Certificates never resurrect."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from wally.finance.models import (
    Candidate,
    FinanceError,
    Instance,
    Kind,
    Locator,
    Scope,
    digest,
    parse_facts,
    semantic_key,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Record:
    id: str
    version: int
    candidate: Candidate = field(repr=False)
    invalid: bool = False


class FinanceStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS finance_objects (
                id TEXT PRIMARY KEY, locator TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                version INTEGER NOT NULL, fingerprint TEXT NOT NULL, candidate TEXT NOT NULL,
                semantic_key TEXT NOT NULL, invalid INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS finance_versions (
                object_id TEXT NOT NULL, version INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                candidate TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(object_id,version));
            CREATE TABLE IF NOT EXISTS finance_certificates (
                id TEXT PRIMARY KEY, object_id TEXT NOT NULL, version INTEGER NOT NULL,
                scope TEXT NOT NULL, fingerprint TEXT NOT NULL, schema_version INTEGER NOT NULL,
                dependencies TEXT NOT NULL, evidence TEXT NOT NULL, attestations TEXT NOT NULL,
                provenance TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS finance_certificate_events (
                certificate_id TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(certificate_id,event));
            CREATE TABLE IF NOT EXISTS finance_enablement (
                definition_id TEXT NOT NULL, scope TEXT NOT NULL, binding TEXT NOT NULL,
                PRIMARY KEY(definition_id,scope));
            CREATE TABLE IF NOT EXISTS finance_events (
                id INTEGER PRIMARY KEY, object_id TEXT NOT NULL, event TEXT NOT NULL,
                details TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS finance_sources (
                source_key TEXT PRIMARY KEY, manifest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS finance_auth_acceptance (
                definition_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                execution_id TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS finance_reference_owners (
                account_id TEXT NOT NULL, reference_hash TEXT NOT NULL,
                instance_id TEXT NOT NULL, PRIMARY KEY(account_id,reference_hash));
            CREATE TABLE IF NOT EXISTS finance_reference_aliases (
                instance_id TEXT NOT NULL, account_id TEXT NOT NULL,
                reference_hash TEXT NOT NULL, PRIMARY KEY(instance_id,reference_hash));
            """)

    def connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def get(self, object_id: str) -> Record:
        with self.connect() as c:
            row = c.execute("SELECT * FROM finance_objects WHERE id=?", (object_id,)).fetchone()
        if row is None:
            raise FinanceError("Unknown canonical financial object.")
        return Record(row["id"], row["version"], decode(row["candidate"]), bool(row["invalid"]))

    def records(self) -> list[Record]:
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM finance_objects ORDER BY id")]
        return [self.get(i) for i in ids]

    def register(self, candidate: Candidate) -> Record:
        # Registration is provisional. Source locators, not copied Notion IDs, allocate identity.
        raw = encode(candidate)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM finance_objects WHERE locator=?", (candidate.locator.key,)
            ).fetchone()
            object_id = row["id"] if row else f"fin_{uuid4().hex}"
            self._check_reference(c, candidate, object_id)
            if row and row["kind"] != candidate.kind:
                raise FinanceError("A source cannot change canonical record kind.")
            changed = row is None or row["fingerprint"] != candidate.fingerprint
            version = (row["version"] + 1 if row else 1) if changed else row["version"]
            if changed:
                c.execute(
                    "INSERT INTO finance_versions VALUES (?,?,?,?,?)",
                    (object_id, version, candidate.fingerprint, raw, now()),
                )
                if row:
                    self._invalidate(c, object_id, "material_changed")
                c.execute(
                    "INSERT INTO finance_objects VALUES (?,?,?,?,?,?,?,0) "
                    "ON CONFLICT(id) DO UPDATE SET version=excluded.version, "
                    "fingerprint=excluded.fingerprint,candidate=excluded.candidate, "
                    "semantic_key=excluded.semantic_key,invalid=0",
                    (
                        object_id,
                        candidate.locator.key,
                        candidate.kind,
                        version,
                        candidate.fingerprint,
                        raw,
                        semantic_key(candidate.kind, candidate.facts),
                    ),
                )
            else:
                # A previously inaccessible/deleted source needs fresh certification on recovery.
                c.execute(
                    "UPDATE finance_objects SET candidate=?, invalid=0 WHERE id=?", (raw, object_id)
                )
            self._note_reference(c, candidate, object_id)
            if changed:
                self._event(
                    c,
                    object_id,
                    "registered" if not row else "material_revision",
                    {"version": version},
                )
        return self.get(object_id)

    def invalidate(self, object_id: str, event: str = "source_unavailable") -> None:
        with self.connect() as c:
            self._invalidate(c, object_id, event)
            c.execute("UPDATE finance_objects SET invalid=1 WHERE id=?", (object_id,))

    def _invalidate(self, c, object_id, event):
        c.execute(
            "INSERT OR IGNORE INTO finance_certificate_events "
            "SELECT id,?,? FROM finance_certificates WHERE object_id=?",
            (event, now(), object_id),
        )
        self._event(c, object_id, event, {})

    def duplicates(self, record: Record) -> bool:
        with self.connect() as c:
            count = c.execute(
                "SELECT COUNT(*) FROM finance_objects WHERE semantic_key=?",
                (semantic_key(record.candidate.kind, record.candidate.facts),),
            ).fetchone()[0]
        return count != 1

    def certificate(self, object_id: str, scope: Scope) -> dict | None:
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM finance_certificates WHERE object_id=? AND scope=? "
                "AND NOT EXISTS (SELECT 1 FROM finance_certificate_events e "
                "WHERE e.certificate_id=finance_certificates.id) ORDER BY rowid DESC LIMIT 1",
                (object_id, scope),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in ("dependencies", "evidence", "attestations", "provenance"):
            result[name] = json.loads(result[name])
        return result

    def certify(
        self,
        record: Record,
        scope: Scope,
        binding: dict,
        evidence: list,
        attestations: list,
        provenance: dict,
    ) -> str:
        cert_id = f"cert_{uuid4().hex}"
        with self.connect() as c:
            current = c.execute(
                "SELECT version,fingerprint,invalid FROM finance_objects WHERE id=?", (record.id,)
            ).fetchone()
            if (
                not current
                or current[0] != record.version
                or current[1] != record.candidate.fingerprint
                or current[2]
            ):
                raise FinanceError("Financial candidate changed during certification.")
            # Supersede certificates without removing history.
            c.execute(
                "INSERT OR IGNORE INTO finance_certificate_events SELECT id,'superseded',? "
                "FROM finance_certificates WHERE object_id=? AND scope=?",
                (now(), record.id, scope),
            )
            c.execute(
                "INSERT INTO finance_certificates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cert_id,
                    record.id,
                    record.version,
                    scope,
                    record.candidate.fingerprint,
                    record.candidate.schema_version,
                    json.dumps(binding),
                    json.dumps(evidence),
                    json.dumps(attestations),
                    json.dumps(provenance),
                    now(),
                ),
            )
            self._event(c, record.id, "certified", {"certificate_id": cert_id, "scope": scope})
        return cert_id

    def enable(self, definition_id: str, scope: Scope, binding: str, provenance: dict):
        with self.connect() as c:
            c.execute(
                "INSERT INTO finance_enablement VALUES (?,?,?) "
                "ON CONFLICT DO UPDATE SET binding=excluded.binding",
                (definition_id, scope, binding),
            )
            self._event(
                c,
                definition_id,
                "enabled",
                {"scope": scope, "binding": binding, "provenance": provenance},
            )

    def enabled(self, definition_id: str, scope: Scope, binding: str) -> bool:
        with self.connect() as c:
            row = c.execute(
                "SELECT binding FROM finance_enablement WHERE definition_id=? AND scope=?",
                (definition_id, scope),
            ).fetchone()
        return row is not None and row[0] == binding

    def designate(self, key: str, manifest: str, provenance: dict):
        with self.connect() as c:
            c.execute(
                "INSERT INTO finance_sources VALUES (?,?) "
                "ON CONFLICT DO UPDATE SET manifest=excluded.manifest",
                (key, manifest),
            )
            self._event(
                c, key, "source_designated", {"manifest": manifest, "provenance": provenance}
            )

    def designated(self, key: str, manifest: str) -> bool:
        with self.connect() as c:
            row = c.execute(
                "SELECT manifest FROM finance_sources WHERE source_key=?", (key,)
            ).fetchone()
        return row is not None and row[0] == manifest

    def invalidate_scope(
        self, object_id: str, scope: Scope, event: str, provenance: dict | None = None
    ):
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO finance_certificate_events SELECT id,?,? "
                "FROM finance_certificates WHERE object_id=? AND scope=?",
                (event, now(), object_id, scope),
            )
            self._event(c, object_id, event, {"scope": scope, "provenance": provenance or {}})

    def has_history(self, object_id: str) -> bool:
        with self.connect() as c:
            return (
                c.execute(
                    "SELECT 1 FROM finance_certificates WHERE object_id=?", (object_id,)
                ).fetchone()
                is not None
            )

    def audit(self, object_id: str, event: str, details: dict):
        with self.connect() as c:
            self._event(c, object_id, event, details)

    def accept_auth(self, definition_id: str, binding: str, execution_id: str):
        with self.connect() as c:
            c.execute(
                "INSERT INTO finance_auth_acceptance VALUES (?,?,?,?) "
                "ON CONFLICT DO UPDATE SET binding=excluded.binding, "
                "execution_id=excluded.execution_id,created_at=excluded.created_at",
                (definition_id, binding, execution_id, now()),
            )
            self._event(
                c,
                definition_id,
                "auth_only_accepted",
                {"binding": binding, "execution_id": execution_id},
            )

    def accepted(self, definition_id: str, binding: str) -> bool:
        with self.connect() as c:
            row = c.execute(
                "SELECT binding FROM finance_auth_acceptance WHERE definition_id=?",
                (definition_id,),
            ).fetchone()
        return row is not None and row[0] == binding

    def _check_reference(self, c, candidate: Candidate, object_id: str):
        facts = candidate.facts
        if not isinstance(facts, Instance) or not facts.invoice_reference:
            return
        account = self.get(facts.definition).candidate.facts.account
        owner = c.execute(
            "SELECT instance_id FROM finance_reference_owners "
            "WHERE account_id=? AND reference_hash=?",
            (account, digest(facts.invoice_reference)),
        ).fetchone()
        if owner is not None and owner[0] != object_id:
            raise FinanceError("Invoice reference already belongs to another occurrence.")
        if c.execute(
            "SELECT 1 FROM finance_reference_aliases WHERE account_id=? "
            "AND reference_hash=? AND instance_id<>?",
            (account, digest(facts.invoice_reference), object_id),
        ).fetchone():
            raise FinanceError("Invoice reference already belongs to another occurrence.")

    def check_reference(self, candidate: Candidate, object_id: str):
        with self.connect() as c:
            self._check_reference(c, candidate, object_id)

    def _note_reference(self, c, candidate: Candidate, object_id: str):
        facts = candidate.facts
        if not isinstance(facts, Instance) or not facts.invoice_reference:
            return
        account = self.get(facts.definition).candidate.facts.account
        c.execute(
            "INSERT OR IGNORE INTO finance_reference_owners VALUES (?,?,?)",
            (account, digest(facts.invoice_reference), object_id),
        )
        c.execute(
            "INSERT OR IGNORE INTO finance_reference_aliases VALUES (?,?,?)",
            (object_id, account, digest(facts.invoice_reference)),
        )

    def _event(self, c, object_id, event, details):
        c.execute(
            "INSERT INTO finance_events(object_id,event,details,created_at) VALUES (?,?,?,?)",
            (object_id, event, json.dumps(details), now()),
        )


def encode(candidate: Candidate) -> str:
    return json.dumps(asdict(candidate), sort_keys=True)


def decode(raw: str) -> Candidate:
    data = json.loads(raw)
    return Candidate(
        Kind(data["kind"]),
        Locator(**data["locator"]),
        parse_facts(Kind(data["kind"]), data["facts"]),
        data["source_revision"],
        data["schema_version"],
    )
