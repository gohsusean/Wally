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


class AuthorizationError(WallyError):
    """A request context is not authenticated, or lacks the capability it needs."""


class ExecutionRequestError(WallyError):
    """An execution request was refused before any execution record was written.

    Raised for unknown proposals or executions and for requests that did not come
    from a trusted user command.
    """


class ExecutionNotStartedError(WallyError):
    """The executor stopped before any consequential browser step ran.

    Safe to treat as "known not executed": no credential was submitted and no
    portal state could have changed.
    """

    def __init__(self, category: str, reason: str) -> None:
        self.category = category
        self.reason = reason
        super().__init__(reason)
