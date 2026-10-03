"""LaunchAgent plist: command, restart, and no secrets."""

from __future__ import annotations

import plistlib
from pathlib import Path

from wally.telegram.launchd import LABEL, THROTTLE_SECONDS, build_agent_spec, render_plist


def test_plist_runs_the_poller_and_restarts_without_secrets(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WALLY_TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("WALLY_TELEGRAM_GATEWAY_CREDENTIAL", "gateway-grant-value")
    monkeypatch.setenv("OP_SESSION_personal", "op-session-token")
    python = tmp_path / ".venv" / "bin" / "python"
    spec = build_agent_spec(
        repo=tmp_path,
        python=python,
        home=tmp_path / "home",
        op_binary=None,
    )
    text = render_plist(spec).decode()
    loaded = plistlib.loads(render_plist(spec))

    assert loaded["Label"] == LABEL
    assert loaded["ProgramArguments"] == [str(python), "-m", "wally", "telegram", "poll"]
    assert loaded["WorkingDirectory"] == str(tmp_path)
    assert loaded["RunAtLoad"] is True
    assert loaded["KeepAlive"] == {"SuccessfulExit": False, "Crashed": True}
    assert loaded["ThrottleInterval"] == THROTTLE_SECONDS == 30
    assert "telegram.owner_user_id" not in text
    assert "WALLY_TELEGRAM_BOT_TOKEN" not in text
    assert "123456:" not in text
    assert "gateway-grant-value" not in text
    assert "op-session-token" not in text
    assert "op://" not in text
    assert "zsh" not in text
    assert ".zshrc" not in text
    assert "bashrc" not in text
    assert "source " not in text
    assert loaded["EnvironmentVariables"]["PATH"].startswith(str(python.parent))
    assert "/opt/homebrew/bin" in loaded["EnvironmentVariables"]["PATH"]
    assert str(tmp_path / "data" / "logs" / "telegram-poll.stdout.log") == loaded["StandardOutPath"]
