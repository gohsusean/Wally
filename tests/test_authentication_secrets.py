"""Scoped credential batches retain authorization, exact references and privacy."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wally.adapters.secrets.dispatch import DispatchSecretsProvider
from wally.adapters.secrets.memory import MemorySecretsProvider
from wally.adapters.secrets.op_cli import OnePasswordCliAdapter
from wally.audit.logger import AuditLogger
from wally.exceptions import ProviderUnavailableError
from wally.runtime.secret_resolver import GovernedSecretsResolver

REFS = ("op://vault/item/username", "op://vault/item/password")
VALUES = ("SYNTHETIC-USER-CANARY", "SYNTHETIC-PASSWORD-CANARY")


def test_one_scoped_read_uses_pinned_account_and_audits_only_references(tmp_path):
    payload = json.dumps(
        [
            {"id": "password", "value": VALUES[1]},
            {"id": "username", "value": VALUES[0]},
        ]
    )
    provider = OnePasswordCliAdapter(account="account-id")
    resolver = GovernedSecretsResolver(provider, audit=AuditLogger(tmp_path))
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/fake/op"),
        patch(
            "wally.adapters.secrets.op_cli.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload),
        ) as command,
    ):
        assert resolver.resolve_many(REFS, authorized=True) == VALUES
        args, kwargs = command.call_args
        assert command.call_count == 1
        assert args[0] == [
            "op",
            "item",
            "get",
            "item",
            "--vault",
            "vault",
            "--fields",
            "username,password",
            "--format=json",
            "--reveal",
            "--account",
            "account-id",
        ]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 90
        assert not any(value in repr(command.call_args) for value in VALUES)
    audit = next(tmp_path.glob("*.jsonl")).read_text()
    assert all(ref in audit for ref in REFS)
    assert not any(value in audit for value in VALUES)


@pytest.mark.parametrize("references,authorized", [(REFS, False), ((REFS[0], "raw-value"), True)])
def test_batch_checks_all_authority_and_references_before_any_read(references, authorized):
    provider = MemorySecretsProvider(dict(zip(REFS, VALUES, strict=True)))
    with patch.object(provider, "resolve") as read:
        with pytest.raises(ProviderUnavailableError):
            GovernedSecretsResolver(provider).resolve_many(references, authorized=authorized)
        read.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        "SYNTHETIC-PASSWORD-CANARY",
        "{}",
        "[]",
        '[{"id":"username","value":"SYNTHETIC-USER-CANARY"}]',
        '[{"id":"username","value":"x"},{"id":"username","value":"y"}]',
        '[{"id":"username","value":"x"},{"id":"password","value":""}]',
    '[{"id":"username","value":"x"},{"id":"custom-password","value":"y"}]',
    ],
)
def test_bad_scoped_response_is_bounded_and_not_retried(payload):
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/fake/op"),
        patch(
            "wally.adapters.secrets.op_cli.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload),
        ) as read,
    ):
        with pytest.raises(ProviderUnavailableError) as failure:
            OnePasswordCliAdapter().resolve_many(REFS)
        assert "CANARY" not in str(failure.value)
        assert read.call_count == 1


def test_timeout_has_no_retry_or_partial_result(tmp_path):
    expired = subprocess.TimeoutExpired("op", 90, output=VALUES[0], stderr=VALUES[1])
    with (
        patch("wally.adapters.secrets.op_cli.shutil.which", return_value="/fake/op"),
        patch("wally.adapters.secrets.op_cli.subprocess.run", side_effect=expired) as read,
    ):
        resolver = GovernedSecretsResolver(OnePasswordCliAdapter(), audit=AuditLogger(tmp_path))
        with pytest.raises(ProviderUnavailableError) as failure:
            resolver.resolve_many(REFS, authorized=True)
        assert read.call_count == 1
        assert "CANARY" not in str(failure.value)
    assert "CANARY" not in next(tmp_path.glob("*.jsonl")).read_text()


def test_other_references_remain_field_scoped_and_dispatch_keeps_keychain_separate():
    memory = MemorySecretsProvider(
        {**dict(zip(REFS, VALUES, strict=True)), "keychain://test/item": "key-value"}
    )
    dispatch = DispatchSecretsProvider(memory, memory)
    resolver = GovernedSecretsResolver(dispatch)
    assert resolver.resolve_many(REFS, authorized=True) == VALUES
    assert resolver.resolve_many((REFS[0], "keychain://test/item"), authorized=True) == (
        VALUES[0],
        "key-value",
    )


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 121])
def test_interactive_timeout_is_bounded(timeout):
    with pytest.raises(ValueError):
        OnePasswordCliAdapter(timeout_seconds=timeout)


@pytest.mark.parametrize("account", ["--debug", "bad/account", "account with spaces"])
def test_account_pin_cannot_be_a_cli_option_or_path(account):
    with pytest.raises(ValueError):
        OnePasswordCliAdapter(account=account)


@pytest.mark.parametrize("batch", [False, True])
def test_raw_credential_rejected_as_a_reference_is_not_copied_into_audit(tmp_path, batch):
    provider = MemorySecretsProvider({})
    resolver = GovernedSecretsResolver(provider, audit=AuditLogger(tmp_path))
    with pytest.raises(ProviderUnavailableError), patch.object(provider, "resolve") as read:
        if batch:
            resolver.resolve_many((REFS[0], VALUES[1]), authorized=True)
        else:
            resolver.resolve(VALUES[1], authorized=True)
    read.assert_not_called()
    audit = next(tmp_path.glob("*.jsonl")).read_text()
    assert VALUES[1] not in audit
    assert "[invalid reference]" in audit
