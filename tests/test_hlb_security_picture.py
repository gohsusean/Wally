"""Fail closed before password entry; no live bank, credentials or image artifacts."""

import base64
import stat
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wally.adapters.browser.authentication import LoginResult, LoginState, LoginStep
from wally.adapters.browser.hlb import HLB_ORIGIN, HLBSecurityPicture, read_reference
from wally.adapters.browser.payments import BrowserPaymentProvider, PaymentBrowserProfile
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.finance.models import FinanceError


@pytest.fixture
def picture():
    return HLBSecurityPicture("#picture", "#confirm")


@pytest.fixture
def page():
    image = Mock()
    image.count.return_value = 1
    image.is_visible.return_value = True
    image.evaluate.return_value = {
        "width": 16,
        "height": 16,
        "rgba": base64.b64encode(bytes(range(256)) * 4).decode(),
    }
    checkbox = Mock()
    checkbox.count.return_value = 1
    checkbox.is_checked.return_value = True
    page = SimpleNamespace(url=HLB_ORIGIN + "/login", image=image, checkbox=checkbox)
    page.locator = lambda selector: image if selector == "#picture" else checkbox
    return page


def enroll(picture, page, path, **kwargs):
    picture.enroll(
        page,
        reference=path,
        guard=lambda: None,
        confirm_owner=kwargs.pop("confirm_owner", lambda: True),
        **kwargs,
    )


def test_enrollment_stores_only_private_fingerprint_and_matching_picture_ticks(
    page, picture, tmp_path
):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    assert len(read_reference(path)) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert len(list(path.parent.iterdir())) == 1
    assert page.image.evaluate.return_value["rgba"] not in path.read_text()
    picture.confirm(page, reference=path, guard=lambda: None)
    page.checkbox.check.assert_called_once()


@pytest.mark.parametrize("problem", ["mismatch", "missing", "ambiguous", "hidden", "unloaded"])
def test_bad_picture_never_ticks_or_enters_password(page, picture, tmp_path, problem):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    if problem == "mismatch":
        page.image.evaluate.return_value["rgba"] = base64.b64encode(b"x" * 1024).decode()
    elif problem == "missing":
        page.image.count.return_value = 0
    elif problem == "ambiguous":
        page.image.count.return_value = 2
    elif problem == "hidden":
        page.image.is_visible.return_value = False
    else:
        page.image.evaluate.side_effect = RuntimeError("SENSITIVE-IMAGE-CANARY")
    browser = PlaywrightBrowserAdapter()
    browser._pages["test"] = page
    browser.restrict_origins = Mock()
    browser.submit_authentication_step = Mock()
    result = browser.submit_hlb_password(
        "test",
        picture,
        LoginStep("#password", ("#logout",)),
        "PASSWORD-CANARY",
        reference=path,
        guard=lambda: None,
    )
    assert result == LoginResult(LoginState.FAILED)
    page.checkbox.check.assert_not_called()
    browser.submit_authentication_step.assert_not_called()
    assert path.exists()  # No learning, deletion or replacement on failure.
    assert "CANARY" not in repr(result)


@pytest.mark.parametrize(
    "url",
    [
        "http://s.hongleongconnect.my/",
        "https://s.hongleongconnect.my.attacker.test/",
        "https://attacker.test/",
        "https://s.hongleongconnect.my:444/",
        "https://user:pass@s.hongleongconnect.my/",
    ],
)
def test_origin_rejected_before_image_interaction(page, picture, tmp_path, url):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    page.image.evaluate.reset_mock()
    page.url = url
    with pytest.raises(FinanceError):
        picture.confirm(page, reference=path, guard=lambda: None)
    page.image.evaluate.assert_not_called()
    page.checkbox.check.assert_not_called()


def test_official_menu_fragment_preserves_picture_and_exact_origin_gate(page, picture, tmp_path):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    page.url = HLB_ORIGIN + "/dashboard#menu"
    picture.confirm(page, reference=path, guard=lambda: None)
    page.checkbox.check.assert_called_once()
    page.checkbox.check.reset_mock()
    page.url = "https://attacker.test/#https://s.hongleongconnect.my/"
    with pytest.raises(FinanceError):
        picture.confirm(page, reference=path, guard=lambda: None)
    page.checkbox.check.assert_not_called()


def test_missing_reference_never_captures_or_learns(page, picture, tmp_path):
    with pytest.raises(FinanceError):
        picture.confirm(page, reference=tmp_path / "missing", guard=lambda: None)
    page.image.evaluate.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("problem", [None, "origin", "navigation"])
def test_hlb_login_waits_for_final_username_form_and_checks_origin(problem):
    page = Mock()
    page.url = HLB_ORIGIN + "/"
    browser = PlaywrightBrowserAdapter()
    browser._pages["test"] = page
    browser.restrict_origins = Mock()
    if problem == "origin":
        page.url = "https://attacker.invalid/"
    elif problem == "navigation":
        page.locator.return_value.wait_for.side_effect = RuntimeError("PRIVATE-URL-CANARY")
    if problem:
        with pytest.raises(FinanceError, match="login form/origin not ready"):
            browser.wait_for_hlb_login("test")
        if problem == "origin":
            page.locator.assert_not_called()
    else:
        browser.wait_for_hlb_login("test")
        page.locator.assert_called_once_with("#idLoginId")
        page.locator.return_value.wait_for.assert_called_once_with(state="visible", timeout=20000)


def test_enrollment_requires_explicit_confirmation_and_stability(page, picture, tmp_path):
    path = tmp_path / "private/picture.sha256"
    with pytest.raises(FinanceError):
        enroll(picture, page, path, confirm_owner=lambda: False)
    assert not path.exists()

    def drift():
        page.image.evaluate.return_value["rgba"] = base64.b64encode(b"x" * 1024).decode()
        return True

    with pytest.raises(FinanceError, match="changed"):
        enroll(picture, page, path, confirm_owner=drift)
    assert not path.exists()


def test_replacement_is_explicit_and_reconfirmed(page, picture, tmp_path):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    old = path.read_bytes()
    page.image.evaluate.return_value["rgba"] = base64.b64encode(b"x" * 1024).decode()
    with pytest.raises(FinanceError, match="explicit replacement"):
        enroll(picture, page, path)
    with pytest.raises(FinanceError):
        enroll(picture, page, path, replace=True, confirm_owner=lambda: False)
    assert path.read_bytes() == old
    enroll(picture, page, path, replace=True)
    assert path.read_bytes() != old


def test_drift_after_authorization_stops_before_checkbox(page, picture, tmp_path):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)

    def drift():
        page.image.evaluate.return_value["rgba"] = base64.b64encode(b"x" * 1024).decode()

    with pytest.raises(FinanceError):
        picture.confirm(page, reference=path, guard=drift)
    page.checkbox.check.assert_not_called()


def test_reference_and_picture_drift_cannot_rebind_confirmation(page, picture, tmp_path):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)

    def drift():
        page.image.evaluate.return_value["rgba"] = base64.b64encode(b"x" * 1024).decode()
        path.write_text(picture.capture(page) + "\n")

    with pytest.raises(FinanceError, match="reference changed"):
        picture.confirm(page, reference=path, guard=drift)
    page.checkbox.check.assert_not_called()


def test_reference_replacement_after_checkbox_stops_before_password(page, picture, tmp_path):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    browser = PlaywrightBrowserAdapter()
    browser._pages["test"] = page
    browser.restrict_origins = Mock()
    browser.submit_authentication_step = Mock(return_value=LoginResult(LoginState.ADVANCED))
    calls = 0

    def drift():
        nonlocal calls
        calls += 1
        if calls == 2:  # After checkbox confirmation, before password focus/fill.
            page.image.evaluate.return_value["rgba"] = base64.b64encode(b"x" * 1024).decode()
            path.write_text(picture.capture(page) + "\n")

    result = browser.submit_hlb_password(
        "test",
        picture,
        LoginStep("#password", ("#logout",)),
        "PASSWORD-CANARY",
        reference=path,
        guard=drift,
    )
    assert result == LoginResult(LoginState.FAILED)
    page.checkbox.check.assert_called_once()
    page.checkbox.click.assert_not_called()
    browser.submit_authentication_step.assert_not_called()


@pytest.mark.parametrize("unsafe", ["permissions", "symlink", "directory", "malformed"])
def test_unsafe_reference_is_rejected(page, picture, tmp_path, unsafe):
    path = tmp_path / "private/picture.sha256"
    enroll(picture, page, path)
    if unsafe == "permissions":
        path.chmod(0o644)
    elif unsafe == "symlink":
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    elif unsafe == "directory":
        path.parent.chmod(0o755)
    else:
        path.write_text("malformed")
    with pytest.raises(FinanceError):
        picture.confirm(page, reference=path, guard=lambda: None)
    page.checkbox.check.assert_not_called()


def test_hlb_profile_cannot_bypass_picture_gate_and_binds_selectors(picture):
    challenges = ("#bank-challenge",)
    profile = PaymentBrowserProfile(
        "hlb",
        "1",
        HLB_ORIGIN + "/login",
        HLB_ORIGIN + "/history",
        (HLB_ORIGIN,),
        "op://Private/HLB/username",
        "op://Private/HLB/password",
        challenge_selector=challenges[0],
        username_step=LoginStep("#username", ("#picture",), challenge_selectors=challenges),
        password_step=LoginStep("#password", ("#logout",), challenge_selectors=challenges),
        hlb_picture=picture,
    )
    with pytest.raises(FinanceError):
        _ = replace(profile, hlb_picture=None).fingerprint
    changed = replace(profile, hlb_picture=replace(picture, image_selector="#other"))
    assert profile.fingerprint != changed.fingerprint
    browser = Mock()
    browser.submit_authentication_step.return_value = LoginResult(LoginState.ADVANCED)
    browser.submit_hlb_password.return_value = LoginResult(LoginState.FAILED)
    resolver = Mock()
    resolver.resolve_many.return_value = ("USER-CANARY", "PASSWORD-CANARY")
    with pytest.raises(FinanceError):
        BrowserPaymentProvider(browser, profile).authenticate(
            resolver,
            guard=lambda: None,
            session_id="test",
        )
    browser.submit_authentication_step.assert_called_once()  # Username only.
    browser.submit_hlb_password.assert_called_once()
    browser.run_actions.assert_not_called()  # No payment or false auth success.
