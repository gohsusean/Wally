"""Attended CLI modes retain explicit enrollment and the existing secret boundary."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wally.adapters.browser.authentication import LoginResult, LoginState
from wally.finance.models import FinanceError
from wally.runtime import hlb_login


@pytest.mark.parametrize(
    "mode", ["login", "enroll", "enroll-and-login", "declined", "credentials-unavailable"]
)
def test_attended_modes_enroll_only_after_owner_confirmation(monkeypatch, capsys, mode):
    browser = Mock()
    browser.open_session.return_value = SimpleNamespace(session_id="test")
    browser.submit_authentication_step.return_value = LoginResult(LoginState.ADVANCED, True)
    browser.submit_hlb_password.return_value = LoginResult(LoginState.ADVANCED, True)
    resolver = Mock()
    resolver.resolve_many.return_value = ("USER-CANARY", "PASSWORD-CANARY")
    reference_check = Mock()
    monkeypatch.setattr(hlb_login, "PlaywrightBrowserAdapter", Mock(return_value=browser))
    monkeypatch.setattr(hlb_login, "load_settings", Mock())
    monkeypatch.setattr(hlb_login, "create_secrets_provider", Mock())
    monkeypatch.setattr(hlb_login, "AuditLogger", Mock())
    monkeypatch.setattr(hlb_login, "GovernedSecretsResolver", Mock(return_value=resolver))
    monkeypatch.setattr(hlb_login, "read_reference", reference_check)
    confirmation = Mock(return_value="no" if mode == "declined" else "CONFIRM HLB PICTURE")
    monkeypatch.setattr("builtins.input", confirmation)

    def enroll(*args, **kwargs):
        if kwargs["confirm_owner"]() is not True:
            raise FinanceError("Enrollment declined.")

    browser.enroll_hlb_security_picture.side_effect = enroll
    argv = ["hlb_login", "--username-ref", "op://Private/HLB/username",
            "--password-ref", "op://Private/HLB/password"]
    if mode not in {"login", "credentials-unavailable"}:
        argv.append("--enroll-and-login" if mode == "declined" else "--" + mode)
    monkeypatch.setattr(sys, "argv", argv)
    if mode == "credentials-unavailable":
        resolver.resolve_many.side_effect = RuntimeError("PRIVATE-ERROR-CANARY")
        assert hlb_login.main() == 1
        browser.open_session.assert_not_called()
        browser.submit_hlb_password.assert_not_called()
        browser.shutdown.assert_called_once()
        output = capsys.readouterr()
        assert "stopped during 1Password credential retrieval" in output.out
        assert "CANARY" not in output.out + output.err
        return
    assert hlb_login.main() == (1 if mode == "declined" else 0)
    browser.wait_for_hlb_login.assert_called_once_with("test")
    if mode == "login":
        reference_check.assert_called_once()
        browser.enroll_hlb_security_picture.assert_not_called()
        confirmation.assert_not_called()
    else:
        reference_check.assert_not_called()
        browser.enroll_hlb_security_picture.assert_called_once()
        confirmation.assert_called_once()
    if mode in {"login", "enroll-and-login"}:
        step = browser.submit_hlb_password.call_args.args[2]
        assert step.next_frame_selector == "iframe"
        assert step.next_selectors == ('a[href*="logout" i]',)
    else:
        browser.submit_hlb_password.assert_not_called()
    refs = resolver.resolve_many.call_args.args[0]
    assert len(refs) == (1 if mode == "enroll" else 2)
    browser.shutdown.assert_called_once()
    output = capsys.readouterr()
    assert "CANARY" not in output.out + output.err
