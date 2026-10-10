"""No biometric prompt or real helper process is launched by these tests."""

import hashlib
import json
import os
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from wally.adapters.macos.confirmation import MacOSBiometricConfirmation
from wally.runtime.confirmation import HumanReview


def test_native_helper_requires_integrity_owner_and_exact_response(tmp_path, monkeypatch):
    helper = tmp_path / "review-helper"
    helper.write_bytes(b"synthetic compiled helper")
    helper.chmod(0o700)
    provider = MacOSBiometricConfirmation(
        helper,
        helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
        owner_uid=os.getuid(),
    )
    review = HumanReview(
        "decide_proposal", "owner", "codex", "correlation", "{}", "nonce", time.time() + 100
    )
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert args == [str(helper)]
        data = json.loads(kwargs["input"])
        assert data["digest"] == review.fingerprint
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "confirmed": True,
                    "nonce": review.nonce,
                    "digest": review.fingerprint,
                }
            ),
        )

    monkeypatch.setattr("wally.adapters.macos.confirmation.platform.system", lambda: "Darwin")
    monkeypatch.setattr("wally.adapters.macos.confirmation.subprocess.run", run)
    assert provider.confirm(review)
    assert len(calls) == 1
    helper.write_bytes(b"substituted helper")
    assert not provider.confirm(review)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        {"confirmed": True},
        {"confirmed": True, "nonce": "other", "digest": "forged"},
        {"confirmed": False},
        {"approved": "owner said approve"},
    ],
)
def test_model_like_or_wrong_native_response_never_confirms(tmp_path, monkeypatch, response):
    helper = tmp_path / "helper"
    helper.write_bytes(b"fake")
    helper.chmod(0o700)
    provider = MacOSBiometricConfirmation(
        helper, helper_sha256=hashlib.sha256(b"fake").hexdigest(), owner_uid=os.getuid()
    )
    monkeypatch.setattr("wally.adapters.macos.confirmation.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "wally.adapters.macos.confirmation.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(response)),
    )
    review = HumanReview(
        "execute_notion_edit", "owner", "codex", "req", "{}", "n", time.time() + 100
    )
    assert not provider.confirm(review)
    assert not provider.confirm(replace(review, expires_at=0))


def test_terminal_admin_provider_preserves_local_behavior_and_rejects_agent_pipes(monkeypatch):
    from wally.adapters.cli.approval import TerminalHumanConfirmation

    prompts = []
    approval = SimpleNamespace(
        request_approval=lambda summary, **kwargs: prompts.append(summary) or True
    )
    provider = TerminalHumanConfirmation(approval)
    review = HumanReview(
        "decide_proposal", "owner", "cli", "req", '{"scope":"exact"}', "n", time.time() + 100
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert not provider.confirm(review)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert provider.confirm(review)
    assert review.presentation in prompts[0]
    assert not provider.confirm(replace(review, channel="codex"))
