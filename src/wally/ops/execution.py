"""Execution eligibility for proposed actions.

v0.14 records approval and then stops. An approved proposal is stored intent for
Act & Verify. This function is the guard that keeps that status from becoming a
tool call, a secret lookup, or a provider write.

Act & Verify must replace this function. It must not grow a true return in this
milestone.
"""

from __future__ import annotations

from wally.models.ops import ProposedAction


def execution_allowed(proposal: ProposedAction) -> bool:
    """Return whether a proposal may be executed.

    Always false. Status, decision, and decision fingerprint are ignored on purpose:
    approval is not execution.
    """
    del proposal
    return False
