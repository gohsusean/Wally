"""Isolated state-copy checks; no iCloud account, credentials, or services."""

import plistlib
import sqlite3
from pathlib import Path

import pytest

from wally.local_sync import DATABASES, _publish, agent_spec, icloud_destination, sync_local_data


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "data/audit").mkdir(parents=True)
    for name in DATABASES:
        with sqlite3.connect(repo / "data" / name) as conn:
            conn.execute("CREATE TABLE state (value TEXT)")
            conn.execute("INSERT INTO state VALUES ('preserve')")
    return repo


def test_copies_committed_wal_and_replaces_current_copy(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    destination = tmp_path / "icloud/Wally"
    writer = sqlite3.connect(repo / "data/operations.db")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO state VALUES ('committed WAL row')")
        writer.commit()
        writer.execute("INSERT INTO state VALUES ('uncommitted')")
        sync_local_data(repo, destination)
        with sqlite3.connect(destination / "data/operations.db") as copied:
            assert copied.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert copied.execute("SELECT value FROM state").fetchall() == [
                ("preserve",),
                ("committed WAL row",),
            ]
        writer.rollback()
        writer.execute("INSERT INTO state VALUES ('next run')")
        writer.commit()
        sync_local_data(repo, destination)
        with sqlite3.connect(destination / "data/operations.db") as copied:
            assert copied.execute("SELECT count(*) FROM state").fetchone() == (3,)
        assert not list(destination.rglob("*-wal"))
        assert not list(destination.rglob("*-shm"))
        assert len(list((destination / "data").glob("*.db"))) == 3
    finally:
        writer.close()


def test_allowlist_and_complete_audit_lines(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    audit = repo / "data/audit/2026-10-11.jsonl"
    audit.write_bytes(b'{"event":"first"}\n{"partial":')
    (repo / ".env").write_text("SECRET=excluded")
    (repo / "data/audit/secret.jsonl").write_text("excluded")
    (repo / "data/cache").mkdir()
    (repo / "data/cache/token.json").write_text("excluded")
    evidence = repo / "data/manual-payment/occurrence"
    evidence.mkdir(parents=True)
    (evidence / "facts.json").write_text('{"amount":"100"}')
    (evidence / "original.pdf").write_bytes(b"original document")
    (evidence / "render.png").write_bytes(b"excluded")
    (evidence / "extracted.txt").write_text("excluded")
    destination = tmp_path / "icloud/Wally"
    copied = sync_local_data(repo, destination)
    assert {str(path.relative_to(destination)) for path in copied} == {
        *(f"data/{name}" for name in DATABASES),
        "data/audit/2026-10-11.jsonl",
        "data/manual-payment/occurrence/facts.json",
        "data/manual-payment/occurrence/original.pdf",
    }
    assert (destination / "data/audit/2026-10-11.jsonl").read_bytes() == b'{"event":"first"}\n'
    assert audit.read_bytes().endswith(b'{"partial":')
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in copied)


def test_missing_database_never_creates_source_or_overwrites_copy(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    destination = tmp_path / "icloud/Wally"
    sync_local_data(repo, destination)
    before = (destination / "data/operations.db").read_bytes()
    (repo / "data/operations.db").unlink()
    with pytest.raises(ValueError):
        sync_local_data(repo, destination)
    assert not (repo / "data/operations.db").exists()
    assert (destination / "data/operations.db").read_bytes() == before


def test_hourly_agent_uses_existing_python_without_app_bootstrap(tmp_path: Path) -> None:
    spec = plistlib.loads(
        plistlib.dumps(agent_spec(tmp_path / "repo", tmp_path, tmp_path / "Wally"))
    )
    assert spec["StartInterval"] == 3600
    assert spec["RunAtLoad"] is True
    assert spec["ProgramArguments"][0].endswith("/.venv/bin/python")
    assert spec["ProgramArguments"][3] == "wally.local_sync"
    assert set(spec["EnvironmentVariables"]) == {"HOME", "PATH"}


def test_missing_icloud_drive_is_not_created_as_a_plain_folder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        icloud_destination(tmp_path)
    assert not (tmp_path / "Library").exists()


def test_failed_publication_keeps_previous_copy_and_no_cloud_staging(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    source.write_bytes(b"new complete database")
    destination = tmp_path / "icloud/state.db"
    destination.parent.mkdir()
    destination.write_bytes(b"previous complete database")

    def interrupted_replace(path, target):
        # Even at the publish boundary no partial/staging file is in iCloud.
        assert list(destination.parent.iterdir()) == [destination]
        assert destination.read_bytes() == b"previous complete database"
        assert path.read_bytes() == source.read_bytes()
        raise OSError("publication interrupted")

    monkeypatch.setattr(Path, "replace", interrupted_replace)
    with pytest.raises(OSError, match="publication interrupted"):
        _publish(source, destination)
    assert destination.read_bytes() == b"previous complete database"
    assert list(destination.parent.iterdir()) == [destination]
