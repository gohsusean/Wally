"""Login progress, challenge handling and privacy on the existing Playwright adapter."""

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from wally.adapters.browser.authentication import LoginResult, LoginState, LoginStep, advance_login
from wally.adapters.browser.factory import create_browser_provider
from wally.adapters.browser.payments import BrowserPaymentProvider, PaymentBrowserProfile
from wally.adapters.browser.playwright_adapter import PlaywrightBrowserAdapter
from wally.config.loader import load_settings
from wally.exceptions import ProviderUnavailableError
from wally.finance.models import FinanceError
from wally.finance.payments import MandatoryChallenge
from wally.models.browser import BrowserStepResult, BrowserStepStatus

CANARY = "SYNTHETIC-CREDENTIAL-CANARY"


class Field:
    def __init__(self, *, visible=True, enabled=True, readonly=False):
        self.visible, self.enabled, self.readonly = visible, enabled, readonly
        self.value = ""
        self.fills = self.submissions = 0
        self.on_submit = lambda: None

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def get_attribute(self, name):
        return "" if name == "readonly" and self.readonly else None

    def fill(self, value, **kwargs):
        self.fills += 1
        self.value = value

    def input_value(self):
        return self.value

    def press(self, key, **kwargs):
        assert key == "Enter"
        self.submissions += 1
        self.on_submit()

    def click(self, **kwargs):
        self.submissions += 1
        self.on_submit()


class Page:
    def __init__(self):
        self.fields = {
            "#user": Field(),
            "#next": Field(visible=False),
            "#error": Field(visible=False),
            "#challenge": Field(visible=False),
        }

    def locator(self, selector):
        return self.fields[selector]

    def wait_for_timeout(self, milliseconds):
        time.sleep(0.002)


def step(**kwargs):
    return LoginStep(
        "#user",
        ("#next",),
        rejection_selector="#error",
        challenge_selectors=("#challenge",),
        timeout_ms=1,
        **kwargs,
    )


def run(page, **kwargs):
    return advance_login(
        page,
        step(),
        CANARY,
        check_origin=kwargs.get("check_origin", lambda: None),
        before_submit=kwargs.get("guard", lambda: None),
    )


def test_click_or_enter_is_not_login_success_without_an_observed_transition():
    page = Page()
    result = run(page)
    assert result.state == LoginState.STALLED
    assert result.submission_attempted
    assert page.fields["#user"].submissions == 1
    assert CANARY not in repr(result)


@pytest.mark.parametrize(
    "attribute,state",
    [
        ("visible", LoginState.HIDDEN),
        ("enabled", LoginState.DISABLED),
        ("readonly", LoginState.READ_ONLY),
    ],
)
def test_state_drift_while_owner_authorizes_credentials_stops_before_fill(attribute, state):
    page = Page()
    setattr(page.fields["#user"], attribute, attribute == "readonly")
    result = run(page)
    assert result.state == state
    assert not result.submission_attempted
    assert page.fields["#user"].fills == 0


@pytest.mark.parametrize(
    "target,state",
    [
        ("#next", LoginState.ADVANCED),
        ("#error", LoginState.REJECTED),
        ("#challenge", LoginState.CHALLENGE),
    ],
)
def test_transition_is_classified_without_reading_page_content(target, state):
    page = Page()
    page.fields["#user"].on_submit = lambda: setattr(page.fields[target], "visible", True)
    assert run(page).state == state
    assert page.fields["#user"].submissions == 1


def test_challenge_is_never_clicked_or_filled():
    page = Page()
    page.fields["#challenge"].visible = True
    result = run(page)
    assert result.state == LoginState.CHALLENGE
    assert not result.submission_attempted
    assert page.fields["#user"].fills == 0


def test_timeout_after_success_does_not_repeat_submission_or_echo_the_exception():
    page = Page()

    def submit():
        page.fields["#next"].visible = True
        raise TimeoutError(CANARY)

    page.fields["#user"].on_submit = submit
    result = run(page)
    assert result.state == LoginState.ADVANCED
    assert page.fields["#user"].submissions == 1
    assert CANARY not in repr(result)


def test_origin_and_authorization_are_rechecked_before_submission():
    page = Page()
    origin = Mock(side_effect=[None, RuntimeError(CANARY)])
    result = run(page, check_origin=origin)
    assert result.state == LoginState.ORIGIN_CHANGED
    assert not result.submission_attempted
    result = run(page, guard=Mock(side_effect=RuntimeError(CANARY)))
    assert result.state == LoginState.AUTHORIZATION_CHANGED
    assert not result.submission_attempted
    assert page.fields["#user"].submissions == 0
    assert CANARY not in repr(result)


def test_authority_drift_during_fill_stops_before_submit_and_hides_errors():
    page = Page()
    result = run(page, guard=Mock(side_effect=[None, RuntimeError(CANARY)]))
    assert result.state == LoginState.AUTHORIZATION_CHANGED
    assert page.fields["#user"].fills == 1
    assert page.fields["#user"].submissions == 0
    page.fields["#user"].fill = Mock(side_effect=TimeoutError(CANARY))
    result = run(page)
    assert result.state == LoginState.FAILED
    assert CANARY not in repr(result)


def test_cleanup_failure_cannot_echo_secrets_or_prevent_driver_cleanup():
    adapter = PlaywrightBrowserAdapter()
    context = SimpleNamespace(close=Mock(side_effect=RuntimeError(CANARY)))
    adapter._pages["session"] = SimpleNamespace(
        close=Mock(side_effect=RuntimeError(CANARY)), context=context
    )
    driver = SimpleNamespace(stop=Mock())
    adapter._playwright = driver
    adapter._browser = SimpleNamespace(close=Mock(side_effect=RuntimeError(CANARY)))
    adapter.shutdown()
    driver.stop.assert_called_once()
    context.close.assert_called_once()
    assert not adapter._pages and adapter._browser is None and adapter._playwright is None


def test_login_reports_network_failure_separately_from_http_rejection_without_request_data():
    handlers = {}
    page = SimpleNamespace(
        on=lambda name, callback: handlers.update({name: callback}),
        remove_listener=lambda name, callback: handlers.pop(name),
    )
    adapter = PlaywrightBrowserAdapter()
    adapter._pages["session"] = page
    adapter.restrict_origins = Mock()

    class Request:
        resource_type = "xhr"
        url = "https://bank.invalid/bootstrap?token=" + CANARY
        failure = "net::ERR_HTTP2_PROTOCOL_ERROR"

        @property
        def post_data(self):
            raise AssertionError("Diagnostics must never read request payloads")

        @property
        def headers(self):
            raise AssertionError("Diagnostics must never read headers or cookies")

    def interact(*args, **kwargs):
        handlers["requestfailed"](Request())
        handlers["response"](SimpleNamespace(request=Request(), status=503))
        handlers["response"](SimpleNamespace(request=Request(), status=201))
        handlers["requestfailed"](
            SimpleNamespace(resource_type="fetch", url=Request.url, failure=CANARY)
        )
        handlers["requestfailed"](
            SimpleNamespace(resource_type="xhr", url="https://other.invalid", failure=CANARY)
        )
        return LoginResult(LoginState.STALLED, True)

    with patch("wally.adapters.browser.authentication.advance_login", side_effect=interact):
        result = adapter.submit_authentication_step(
            "session",
            step(),
            CANARY,
            allowed_origins=("https://bank.invalid",),
            before_submit=lambda: None,
        )
    assert result.state == LoginState.STALLED and result.submission_attempted
    assert result.network_errors == (
        "net::ERR_HTTP2_PROTOCOL_ERROR",
        "unclassified_network_failure",
    )
    assert result.http_error_statuses == (503,)
    assert CANARY not in repr(result) and "bank.invalid" not in repr(result)
    assert handlers == {}


def test_login_network_observer_is_bounded_and_removed_when_interaction_fails():
    handlers = {}
    page = SimpleNamespace(
        on=lambda name, callback: handlers.update({name: callback}),
        remove_listener=lambda name, callback: handlers.pop(name),
    )
    adapter = PlaywrightBrowserAdapter()
    adapter._pages["session"] = page
    adapter.restrict_origins = Mock()

    def interact(*args, **kwargs):
        for status in range(400, 430):
            handlers["response"](
                SimpleNamespace(
                    request=SimpleNamespace(resource_type="fetch", url="https://bank.invalid/api"),
                    status=status,
                )
            )
        return LoginResult(LoginState.STALLED, True)

    with patch("wally.adapters.browser.authentication.advance_login", side_effect=interact):
        result = adapter.submit_authentication_step(
            "session",
            step(),
            CANARY,
            allowed_origins=("https://bank.invalid",),
            before_submit=lambda: None,
        )
    assert len(result.http_error_statuses) == 8 and handlers == {}
    with patch(
        "wally.adapters.browser.authentication.advance_login", side_effect=RuntimeError(CANARY)
    ):
        result = adapter.submit_authentication_step(
            "session",
            step(),
            CANARY,
            allowed_origins=("https://bank.invalid",),
            before_submit=lambda: None,
        )
    assert result.state == LoginState.FAILED and handlers == {}
    assert CANARY not in repr(result)


def test_chrome_stable_is_a_playwright_channel_with_no_personal_profile():
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=Mock(return_value=object())))
    with patch(
        "playwright.sync_api.sync_playwright",
        return_value=SimpleNamespace(start=lambda: playwright),
    ):
        adapter = PlaywrightBrowserAdapter(channel="chrome")
        adapter._ensure_browser()
    playwright.chromium.launch.assert_called_once_with(headless=False, channel="chrome")


def test_macbook_selects_existing_playwright_chrome_and_interactive_secret_budget(project_root):
    settings = load_settings(project_root=project_root, config_name="macbook")
    assert settings.browser_adapter == "playwright"
    assert settings.browser_channel == "chrome"
    assert settings.secrets_timeout_seconds == 90
    assert settings.secrets_account
    assert isinstance(create_browser_provider(settings), PlaywrightBrowserAdapter)


def test_unsupported_channel_is_rejected_without_another_browser_system():
    with pytest.raises(ProviderUnavailableError):
        PlaywrightBrowserAdapter(channel="unreviewed-browser")


def staged_profile():
    challenge = ("#mandatory-bank-challenge",)
    return PaymentBrowserProfile(
        "staged-login",
        "1",
        "https://bank.invalid/login",
        "https://bank.invalid/history",
        ("https://bank.invalid",),
        "op://Sandbox/Login/username",
        "op://Sandbox/Login/password",
        username_step=LoginStep("#username", ("#password",), challenge_selectors=challenge),
        password_step=LoginStep("#password", ("#authenticated",), challenge_selectors=challenge),
    )


def test_staged_payment_driver_reuses_auth_only_steps_after_scoped_secret_read():
    events = []
    profile = staged_profile()

    def resolve(*args, **kwargs):
        assert kwargs["authorized"]
        events.append("secrets")
        return (CANARY, CANARY)

    def open_session(**kwargs):
        events.append("open")
        return SimpleNamespace(session_id="session")

    browser = SimpleNamespace(
        open_session=open_session,
        submit_authentication_step=Mock(return_value=LoginResult(LoginState.ADVANCED, True)),
        has_selector=lambda *args: False,
        run_actions=Mock(return_value=BrowserStepResult("session", BrowserStepStatus.COMPLETED)),
    )
    provider = BrowserPaymentProvider(browser, profile)
    provider.authenticate(
        SimpleNamespace(resolve_many=resolve), guard=lambda: None, session_id="auth"
    )
    assert events == ["secrets", "open"]
    assert browser.submit_authentication_step.call_count == 2
    assert browser.run_actions.call_count == 1  # Read-only VERIFY_AUTH; no payment click.
    actions = browser.run_actions.call_args.args[1]
    assert [action.action_type.value for action in actions] == ["verify_auth"]
    browser.submit_authentication_step.reset_mock()
    browser.submit_authentication_step.return_value = LoginResult(LoginState.CHALLENGE)
    with pytest.raises(MandatoryChallenge):
        provider.authenticate(
            SimpleNamespace(resolve_many=resolve), guard=lambda: None, session_id="auth"
        )
    assert browser.submit_authentication_step.call_count == 1


def test_reviewed_fingerprint_binds_login_steps_and_mandatory_challenge_checks():
    profile = staged_profile()
    changed = replace(
        profile, username_step=replace(profile.username_step, input_selector="#other")
    )
    assert changed.fingerprint != profile.fingerprint
    invalid = replace(profile, username_step=replace(profile.username_step, challenge_selectors=()))
    with pytest.raises(FinanceError, match="mandatory challenge"):
        _ = invalid.fingerprint
