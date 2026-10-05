"""Per-user LaunchAgent for ``wally telegram poll``. No secrets in the plist."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.wally.telegram-poll"
THROTTLE_SECONDS = 30
_PATH_PREFIXES = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


def agent_plist_path(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_paths(repo: Path) -> tuple[Path, Path]:
    directory = repo / "data" / "logs"
    return (
        directory / "telegram-poll.stdout.log",
        directory / "telegram-poll.stderr.log",
    )


def build_agent_spec(
    *,
    repo: Path,
    python: Path,
    home: Path,
    op_binary: Path | None = None,
) -> dict:
    """Describe the agent. The caller supplies absolute paths. Shell rc files are not used."""
    stdout, stderr = log_paths(repo)
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "wally", "telegram", "poll"],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False, "Crashed": True},
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
        "EnvironmentVariables": {
            "HOME": str(home),
            "PATH": _controlled_path(python, op_binary),
        },
    }


def render_plist(spec: dict) -> bytes:
    return plistlib.dumps(spec, sort_keys=False)


def _controlled_path(python: Path, op_binary: Path | None) -> str:
    parts = [str(python.parent)]
    if op_binary is not None:
        parts.append(str(op_binary.parent))
    parts.extend(_PATH_PREFIXES)
    seen: dict[str, None] = {}
    for part in parts:
        seen.setdefault(part, None)
    return ":".join(seen)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _target() -> str:
    return f"{_domain()}/{LABEL}"


def install_agent(repo: Path, *, home: Path | None = None, python: Path | None = None) -> Path:
    user_home = home or Path.home()
    interpreter = python or _default_python(repo)
    if not interpreter.is_file():
        raise SystemExit(f"Wally interpreter is missing: {interpreter}")
    spec = build_agent_spec(
        repo=repo,
        python=interpreter,
        home=user_home,
        op_binary=_find_op(),
    )
    stdout, _stderr = log_paths(repo)
    stdout.parent.mkdir(parents=True, exist_ok=True)
    path = agent_plist_path(user_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_plist(spec))
    _launchctl("enable", _target())
    boot = _launchctl("bootstrap", _domain(), str(path))
    if boot.returncode != 0:
        _launchctl("bootout", _target())
        boot = _launchctl("bootstrap", _domain(), str(path))
        if boot.returncode != 0:
            raise SystemExit(boot.stderr.strip() or "launchctl bootstrap failed.")
    _launchctl("kickstart", "-k", _target())
    print(f"Installed {path}")
    return path


def start_agent(repo: Path) -> None:
    if not agent_plist_path().is_file():
        install_agent(repo)
        return
    _launchctl("enable", _target())
    result = _launchctl("kickstart", "-k", _target())
    if result.returncode != 0:
        install_agent(repo)
        return
    print("Started com.wally.telegram-poll")


def stop_agent() -> None:
    _launchctl("bootout", _target())
    _launchctl("disable", _target())
    print("Stopped com.wally.telegram-poll. It stays stopped until start or the next install.")


def restart_agent(repo: Path) -> None:
    if not agent_plist_path().is_file():
        install_agent(repo)
        return
    result = _launchctl("kickstart", "-k", _target())
    if result.returncode != 0:
        install_agent(repo)
        return
    print("Restarted com.wally.telegram-poll")


def uninstall_agent() -> None:
    _launchctl("bootout", _target())
    _launchctl("disable", _target())
    path = agent_plist_path()
    if path.is_file():
        path.unlink()
    print("Uninstalled com.wally.telegram-poll")
    print(
        "The login-keychain bot token is still there. "
        "Remove it with `wally telegram credential remove`."
    )


def status_agent() -> None:
    path = agent_plist_path()
    print(f"Label: {LABEL}")
    print(f"Plist: {path}")
    if not path.is_file():
        print("Installed: no")
        return
    print("Installed: yes")
    spec = plistlib.loads(path.read_bytes())
    arguments = spec.get("ProgramArguments") or []
    print("Command:", " ".join(str(part) for part in arguments))
    print("WorkingDirectory:", spec.get("WorkingDirectory", ""))
    logs = spec.get("StandardErrorPath", "")
    print("Logs:", spec.get("StandardOutPath", ""), logs)
    result = _launchctl("print", _target())
    text = result.stdout
    state = "not loaded"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("state =") or stripped.startswith("pid ="):
            print(stripped)
            if stripped.startswith("state ="):
                state = stripped.split("=", 1)[1].strip()
    if result.returncode != 0 and state == "not loaded":
        print("state = not loaded")


def _default_python(repo: Path) -> Path:
    return repo / ".venv" / "bin" / "python"


def _find_op() -> Path | None:
    for directory in _PATH_PREFIXES:
        candidate = Path(directory) / "op"
        if candidate.is_file():
            return candidate
    which = _launchctl_which("op")
    return which


def _launchctl_which(name: str) -> Path | None:
    result = subprocess.run(
        ["/usr/bin/which", name],
        check=False,
        capture_output=True,
        text=True,
    )
    found = result.stdout.strip()
    if result.returncode == 0 and found:
        return Path(found)
    return None


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )
