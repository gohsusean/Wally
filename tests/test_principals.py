"""Authenticated principals: channels issue contexts; services check capabilities."""

from __future__ import annotations

import pytest

from wally.exceptions import AuthorizationError
from wally.models.principal import Capability, Principal, RequestContext, clean_ref
from wally.runtime.principals import (
    LOCAL_OPERATOR_CHANNELS,
    ChannelPolicy,
    PrincipalAuthority,
)


def test_local_channels_can_decide_execute_and_verify() -> None:
    authority = PrincipalAuthority()
    assert authority.channels() == ("cli", "repl")
    for channel in authority.channels():
        context = authority.issue(channel)
        for capability in Capability:
            assert authority.check(context, capability) is None
        assert context.principal.subject == "owner"
        assert context.principal.authentication == "local_terminal"
        assert context.correlation_id.startswith("req_")
        assert context.provenance().channel == channel
        assert context.provenance().principal == "owner"


def test_unregistered_channel_cannot_be_issued() -> None:
    authority = PrincipalAuthority()
    with pytest.raises(AuthorizationError):
        authority.issue("telegram")
    with pytest.raises(AuthorizationError):
        authority.issue("chatgpt")
    with pytest.raises(AuthorizationError):
        authority.issue("model")


def test_forged_context_claiming_cli_is_rejected() -> None:
    authority = PrincipalAuthority()
    forged = RequestContext(
        principal=Principal(subject="owner", channel="cli", authentication="local_terminal"),
        correlation_id="req_forged",
    )
    with pytest.raises(AuthorizationError):
        authority.authorize(forged, Capability.EXECUTE_PROPOSAL)
    with pytest.raises(AuthorizationError):
        authority.authorize("cli", Capability.EXECUTE_PROPOSAL)
    with pytest.raises(AuthorizationError):
        authority.authorize(None, Capability.DECIDE_PROPOSAL)


def test_context_from_another_authority_is_rejected() -> None:
    issued = PrincipalAuthority().issue("cli")
    other = PrincipalAuthority()
    with pytest.raises(AuthorizationError):
        other.authorize(issued, Capability.EXECUTE_PROPOSAL)


def test_copied_grant_cannot_switch_channel() -> None:
    authority = PrincipalAuthority()
    cli = authority.issue("cli")
    swapped = RequestContext(
        principal=Principal(
            subject="owner",
            channel="repl",
            authentication="local_terminal",
            grant=cli.principal.grant,
        ),
        correlation_id=cli.correlation_id,
    )
    with pytest.raises(AuthorizationError):
        authority.authorize(swapped, Capability.EXECUTE_PROPOSAL)


def test_future_channel_registers_without_execute() -> None:
    authority = PrincipalAuthority(
        {
            **LOCAL_OPERATOR_CHANNELS,
            "remote": ChannelPolicy(
                "remote_session", frozenset({Capability.DECIDE_PROPOSAL})
            ),
        }
    )
    remote = authority.issue("remote")
    authority.authorize(remote, Capability.DECIDE_PROPOSAL)
    with pytest.raises(AuthorizationError, match="may not execute proposal"):
        authority.authorize(remote, Capability.EXECUTE_PROPOSAL)
    with pytest.raises(AuthorizationError, match="may not verify execution"):
        authority.authorize(remote, Capability.VERIFY_EXECUTION)


def test_clean_ref_strips_control_and_caps_length() -> None:
    assert clean_ref("  sess\x00id\n ") == "sess id"
    assert len(clean_ref("x" * 500)) == 200
    assert clean_ref(None) == ""
    assert clean_ref(12) == "12"


def test_issue_copies_external_refs_into_provenance() -> None:
    context = PrincipalAuthority().issue(
        "repl",
        external_session_ref="sess-1",
        external_request_ref="msg-9",
        correlation_id="req_fixed",
    )
    provenance = context.provenance()
    assert provenance.as_dict() == {
        "channel": "repl",
        "principal": "owner",
        "external_session_ref": "sess-1",
        "external_request_ref": "msg-9",
        "correlation_id": "req_fixed",
    }
    assert "grant" not in provenance.as_dict()
