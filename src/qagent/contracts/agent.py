"""The agent contract: what the core calls, and the feedback it may hand back.

The core knows nothing about the agent's internals: it calls a function, parses the
result, and on failure calls the same function again with :class:`Feedback`. That is
the whole integration surface, which is what makes the library agent-agnostic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeAlias

from pydantic import BaseModel, Field

from .base import JsonValue, StrictModel
from .case import Case
from .issues import Issue
from .trace import Usage
from .verdict import JudgeVerdict

Attempt: TypeAlias = BaseModel | dict[str, JsonValue] | str
"""What an agent may return: a model instance, a dict, or raw text to be parsed."""


class Feedback(StrictModel):
    """Everything the core tells the agent before a retry.

    This is the only channel between QA and the agent, so it is a contract rather than
    a prompt fragment: traces, metrics and retry logic stay identical across domains.

    ``previous_output`` is part of the contract because the wrapped agent is called as a
    plain function: without the failed answer it could only fix its mistakes by keeping
    hidden state per case, which makes retries untestable.
    """

    attempt: int = Field(ge=1, description="the attempt that just failed (1-based)")
    issues: list[Issue] = Field(default_factory=list)
    judge: JudgeVerdict | None = None
    instructions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="imperative, field-scoped instructions distilled from the issues",
    )
    previous_output: str | None = Field(
        default=None,
        max_length=100_000,
        description="the rejected answer as text; None when the policy asks to omit it",
    )


@dataclass(frozen=True, slots=True)
class AgentReply:
    """An attempt plus what it cost.

    A bare :data:`Attempt` is enough to run the loop, but then the trace can only account
    for the judge's tokens, and a cost budget that ignores the agent is decoration. Agents
    that know their own LLM calls return this wrapper; simple deterministic agents keep
    returning the bare attempt.
    """

    attempt: Attempt
    usages: tuple[Usage, ...] = field(default_factory=tuple)
    truncated: bool = False
    """The agent's model stopped at its output limit (becomes ``P3_TRUNCATED``)."""


AgentFn: TypeAlias = Callable[[Case, Feedback | None], Awaitable[Attempt | AgentReply]]
"""A wrapped agent: receives the case and (on retries) feedback, returns an attempt."""
