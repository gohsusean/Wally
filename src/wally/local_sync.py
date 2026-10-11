"""Copy essential local state to iCloud Drive, without starting Wally providers."""

from __future__ import annotations

import argparse
import fcntl
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

DATABASES = ("sessions.db", "knowledge_registry.db", "operations.db")
LABEL = "com.wally.local-sync"
INTERVAL_SECONDS = 3600


def icloud_destination(home: Path) -> Path:
    drive = home / "Library/Mobile Documents/com~apple~CloudDocs"
    if not drive.is_dir():
        raise FileNotFoundError("iCloud Drive is unavailable for this user.")
    return drive / "Wally"


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular local file: {path}")


def _publish(source: Path, destination: Path) -> None:
    """Replace a single current copy only after its bytes have been written."""
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.parent.is_symlink() or destination.is_symlink():
        raise ValueError("Sync destination must not be a symlink.")
    # Stage outside iCloud so partial files are never eligible for upload.
    # The default macOS temporary directory and iCloud Drive share a volume;
    # replace fails safely rather than copying partially across volumes.
    fd, name = tempfile.mkstemp(prefix="wally-copy-")
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def sync_local_data(repo: Path, destination: Path) -> list[Path]:
    """Allowlisted one-way copy; no deletion, versions, or credential access."""
    repo = repo.resolve()
    destination = destination.absolute()
    data = repo / "data"
    if destination.resolve().is_relative_to(repo) or destination.is_symlink():
        raise ValueError("Choose a dedicated destination outside the working checkout.")
    # Validate before opening SQLite: never create a missing working database.
    for name in DATABASES:
        _regular_file(data / name)
    if (data / "audit").is_symlink() or (data / "manual-payment").is_symlink():
        raise ValueError("Operational source directories must not be symlinks.")
    copied: list[Path] = []
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (data / ".icloud-sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # SQLite writes only to local staging. iCloud receives closed databases,
        # including committed WAL rows, with no live journals or sidecars.
        with tempfile.TemporaryDirectory(prefix="wally-sync-") as temporary:
            staging = Path(temporary)
            for name in DATABASES:
                staged = staging / name
                source = sqlite3.connect((data / name).as_uri() + "?mode=ro", uri=True)
                target = sqlite3.connect(staged)
                try:
                    source.backup(target)
                    target.execute("PRAGMA journal_mode=DELETE")
                finally:
                    target.close()
                    source.close()
                output = destination / "data" / name
                _publish(staged, output)
                copied.append(output)
            for path in sorted((data / "audit").glob("*.jsonl")):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", path.name):
                    continue
                _regular_file(path)
                staged = staging / "audit.jsonl"
                # Bound an active append-only log to its initial length and
                # omit any unfinished final record. The next copy catches up.
                remaining = path.stat().st_size
                with path.open("rb") as source, staged.open("wb") as target:
                    while remaining > 0:
                        line = source.readline(remaining)
                        if not line.endswith(b"\n"):
                            break
                        target.write(line)
                        remaining -= len(line)
                output = destination / "data/audit" / path.name
                _publish(staged, output)
                copied.append(output)
            # Original payment documents and structured owner evidence are
            # durable records. Generated PNG/text/rendering files are excluded.
            for path in sorted((data / "manual-payment").rglob("*")):
                if path.suffix.lower() not in {".json", ".pdf"}:
                    continue
                _regular_file(path)
                if not path.resolve().is_relative_to(data / "manual-payment"):
                    raise ValueError("Payment evidence must remain inside its source directory.")
                output = destination / "data" / path.relative_to(data)
                _publish(path, output)
                copied.append(output)
    return copied


def agent_spec(repo: Path, home: Path, destination: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(repo / ".venv/bin/python"),
            "-B",
            "-m",
            "wally.local_sync",
            "run",
            "--repo",
            str(repo),
            "--destination",
            str(destination),
        ],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "EnvironmentVariables": {"HOME": str(home), "PATH": "/usr/bin:/bin"},
    }


def install_agent(repo: Path, home: Path, destination: Path) -> Path:
    """Install only this copy job; never restart Telegram or other providers."""
    path = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    spec = agent_spec(repo, home, destination)
    if not Path(spec["ProgramArguments"][0]).is_file():
        raise FileNotFoundError("The existing Wally virtualenv is required.")
    if path.exists():
        raise FileExistsError(f"Sync agent already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        plistlib.dump(spec, handle)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "enable", f"{domain}/{LABEL}"], check=True)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(path)], check=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "install"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    destination = args.destination or icloud_destination(Path.home())
    if args.action == "install":
        print(
            f"Installed hourly sync: {install_agent(args.repo.resolve(), Path.home(), destination)}"
        )
    else:
        copied = sync_local_data(args.repo, destination)
        print(f"Copied {len(copied)} files to {destination}. Cloud upload is not verified.")


if __name__ == "__main__":
    main()
