"""Transport-level failures, mapped to a small closed set.

The distinction that matters downstream is ``retryable``: a rate limit or a timeout may be
worth one transport retry, a bad key never is. Semantic retries (feed the critic's feedback
back to the agent) are a different mechanism and live in the runtime loop.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every model-side failure."""

    retryable: bool = False


class LLMTimeout(LLMError):
    """The provider did not answer inside the timeout."""

    retryable = True


class LLMRateLimited(LLMError):
    """Provider-side throttling, optionally with a hint about when to come back."""

    retryable = True

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class LLMAuthError(LLMError):
    """Missing or rejected credentials. Never retried."""


class LLMBadResponse(LLMError):
    """The provider answered, but not with a usable payload."""


class CassetteMiss(LLMError):
    """A replay was requested for a request that is not in the cassette."""

    def __init__(self, message: str, key: str, path: str) -> None:
        super().__init__(message)
        self.key = key
        self.path = path


class JudgeError(LLMError):
    """The judge could not produce a scorable verdict (bad JSON, unknown criteria)."""
