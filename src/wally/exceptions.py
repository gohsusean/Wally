"""Wally-specific exceptions."""


class WallyError(Exception):
    """Base exception for Wally errors."""


class ProviderUnavailableError(WallyError):
    """A required provider is unreachable or misconfigured."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"{provider} is unavailable: {reason}")


class ConfigurationError(WallyError):
    """Invalid or missing configuration."""


class ProposalTransitionError(WallyError):
    """A proposal lifecycle transition could not be applied safely.

    Raised instead of erasing history or leaving two active proposals for one
    matter and intent.
    """


class ProposalDecisionError(WallyError):
    """An explicit user decision could not be recorded.

    Raised for unknown proposals, decisions that are not currently allowed, and
    any attempt to decide a proposal from an untrusted origin.
    """
