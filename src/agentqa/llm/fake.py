"""A scripted client: the reason the test suite is fast, deterministic and free.

The script is a queue, not a mapping. By the time a test asserts "the third call happened
after the retry", order is the thing being tested, so the client keeps requests in order
for assertions as well.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from .base import LLMClient, LLMRequest, LLMResponse
from .errors import LLMError

Scripted = str | LLMResponse | BaseException
"""A script entry: final text, a full response, or an exception to raise."""


class FakeClient:
    """Returns scripted answers in order and records what it was asked."""

    name = "fake"

    def __init__(self, script: Iterable[Scripted] = (), *, model: str = "fake-model") -> None:
        self._script: deque[Scripted] = deque(script)
        self.requests: list[LLMRequest] = []
        self.model = model

    def push(self, *entries: Scripted) -> None:
        """Append more answers (convenient for multi-attempt tests)."""
        self._script.extend(entries)

    @property
    def remaining(self) -> int:
        return len(self._script)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._script:
            raise LLMError(f"FakeClient script exhausted after {len(self.requests)} call(s)")
        entry = self._script.popleft()
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, str):
            return LLMResponse(text=entry, model=request.model or self.model)
        return entry


def as_client(client: FakeClient) -> LLMClient:
    """Typing helper: a fake satisfies the protocol structurally, names help readers."""
    return client
