"""OAuth repair uses only temporary configuration and mocked local consent."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from wally.adapters.google.auth import DEFAULT_SCOPES
from wally.adapters.google.setup import repair_oauth


def test_repair_preserves_other_configuration_and_hides_credentials(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("# existing\nOTHER=value\nGOOGLE_REFRESH_TOKEN=old\n")
    creds = Mock(refresh_token="new-private-token", granted_scopes=DEFAULT_SCOPES)
    with patch("wally.adapters.google.setup.InstalledAppFlow") as factory:
        flow = factory.from_client_config.return_value
        flow.run_local_server.return_value = creds
        repair_oauth(SimpleNamespace(google_client_id="id", google_client_secret="secret"), env)
    assert env.read_text() == "# existing\nOTHER=value\nGOOGLE_REFRESH_TOKEN='new-private-token'\n"
    assert env.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr().out == ""
    assert factory.from_client_config.call_args.kwargs["autogenerate_code_verifier"] is True
    assert flow.run_local_server.call_args.kwargs["authorization_prompt_message"] == ""
    creds.refresh.assert_called_once()


@pytest.mark.parametrize(
    "token,scopes,drift", [(None, True, False), ("new", False, False), ("new", True, True)]
)
def test_incomplete_or_changed_consent_preserves_current_token(tmp_path, token, scopes, drift):
    env = tmp_path / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN=old\n")
    creds = Mock(refresh_token=token, granted_scopes=DEFAULT_SCOPES if scopes else [])

    def consent(**kwargs):
        if drift:
            env.write_text("GOOGLE_REFRESH_TOKEN=concurrent\n")
        return creds

    with patch("wally.adapters.google.setup.InstalledAppFlow") as factory:
        factory.from_client_config.return_value.run_local_server.side_effect = consent
        with pytest.raises(ValueError):
            repair_oauth(SimpleNamespace(google_client_id="id", google_client_secret="secret"), env)
    assert env.read_text() == f"GOOGLE_REFRESH_TOKEN={'concurrent' if drift else 'old'}\n"


def test_refresh_failure_preserves_token_and_restores_logging(tmp_path):
    import logging

    env = tmp_path / ".env"
    env.write_text("GOOGLE_REFRESH_TOKEN=old\n")
    creds = Mock(refresh_token="new", granted_scopes=DEFAULT_SCOPES)
    creds.refresh.side_effect = RuntimeError("synthetic sensitive token response")
    previous = logging.root.manager.disable
    with patch("wally.adapters.google.setup.InstalledAppFlow") as factory:
        factory.from_client_config.return_value.run_local_server.return_value = creds
        with pytest.raises(RuntimeError):
            repair_oauth(SimpleNamespace(google_client_id="id", google_client_secret="secret"), env)
    assert logging.root.manager.disable == previous
    assert env.read_text() == "GOOGLE_REFRESH_TOKEN=old\n"
