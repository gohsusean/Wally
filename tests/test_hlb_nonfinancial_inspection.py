"""The operator inspection route cannot initiate a transfer, even with a scope."""

from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
import yaml

from wally.adapters.browser.hlb_test_transfer import ENTRY
from wally.finance.models import FinanceError
from wally.finance.test_transfers import APPROVED_TEST, OwnerAccountTestSource
from wally.runtime.hlb_diagnostics import HLBNonfinancialInspection


@pytest.fixture
def inspector(tmp_path):
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump(APPROVED_TEST))
    return HLBNonfinancialInspection(Mock(), Mock(), OwnerAccountTestSource(path))


@pytest.mark.parametrize("control", ["idBtnSubmit", "idBtnSubmit2", "mobileDeviceFlagUpdateBtn2"])
def test_inspection_cannot_dispatch_unscoped_transfer_commands(inspector, control):
    inspector._authorization = Mock()  # Even an accidentally supplied payment scope cannot help.
    request = SimpleNamespace(
        url=ENTRY, method="POST", headers={}, post_data=urlencode({control: "Next"})
    )
    assert inspector._request_allowed(request) is False
    with pytest.raises(FinanceError, match="cannot submit"):
        inspector.submit(Mock(), Mock(), guard=lambda: True)


@pytest.mark.parametrize("drift", [None, "amount", "command", "execute", "extra", "repeated"])
def test_inspection_allows_only_exact_empty_favourite_lookup(inspector, drift):
    fields = {"idFormEntry": "idFormEntry", "javax.faces.ViewState": "PRIVATE-CANARY"}
    control = "j_idt500:0:select"
    inspector._lookup = {"url": ENTRY, "control": control, "fields": fields}
    body = {
        **fields,
        "javax.faces.source": control,
        "javax.faces.partial.execute": control,
        "javax.faces.partial.render": "idFormEntry",
        "javax.faces.behavior.event": "action",
        "javax.faces.partial.event": "click",
        "javax.faces.partial.ajax": "true",
    }
    if drift == "amount":
        body["idAmt"] = "1.00"
    elif drift == "command":
        body["javax.faces.source"] = "idBtnSubmit"
    elif drift == "execute":
        body["javax.faces.partial.execute"] = "@form"
    elif drift == "extra":
        body["idBtnSubmit"] = "Next"
    request = SimpleNamespace(
        url=ENTRY, method="POST", headers={"content-type": "application/x-www-form-urlencoded"},
        post_data=urlencode(body),
    )
    if drift == "repeated":
        assert inspector._request_allowed(request) is True
    assert inspector._request_allowed(request) is (drift is None)
    if drift is None:
        assert inspector._request_allowed(request) is False


def test_native_command_snapshot_matches_observed_mojarra_button_serialization(inspector):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page()
            page.set_content("""<form action="https://bank.invalid/form">
                <input name="idFormEntry" value="idFormEntry" type="hidden">
                <input name="javax.faces.ViewState" value="SYNTHETIC-TOKEN" type="hidden">
                <input name="idAmt" value="">
                <input type="button" name="j_idt601" value="">
                <input type="button" name="idBtnCancel" value="Cancel">
                <input type="submit" name="idBtnSubmit" value="Next">
                <a id="j_idt609:2:j_idt611">Existing favourite</a>
            </form>""")
            data = inspector._command_data(page.locator("a"))
            assert dict(data["fields"]) == {
                "idFormEntry": "idFormEntry", "javax.faces.ViewState": "SYNTHETIC-TOKEN",
                "idAmt": "",
            }
            assert data["jsf_buttons"] == {"j_idt601": "", "idBtnCancel": "Cancel"}
            assert data["control"] == "j_idt609:2:j_idt611"
            assert "idBtnSubmit" not in dict(data["fields"])
        finally:
            browser.close()
