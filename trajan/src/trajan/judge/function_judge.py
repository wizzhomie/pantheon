"""Callable judges: for tests, and for deterministic "judges" that are not models.

A team that only ever tests with a real model ends up with slow, flaky, expensive tests and
a gate nobody dares change. A callable judge makes the loop testable: script the verdict,
assert what the pipeline did with it.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from ..contracts.case import Case
from ..contracts.issues import GateResult
from ..contracts.verdict import JudgeVerdict
from .base import JudgeResult

VerdictProducer = Callable[[Case, BaseModel], JudgeVerdict | Awaitable[JudgeVerdict]]


class FunctionJudge:
    """Wrap a function (sync or async) that produces a verdict."""

    name = "function"

    def __init__(self, producer: VerdictProducer) -> None:
        self.producer = producer
        self.calls = 0

    async def review(
        self,
        case: Case,
        output: BaseModel,
        gate: GateResult | None = None,
    ) -> JudgeResult:
        self.calls += 1
        produced = self.producer(case, output)
        verdict = await produced if inspect.isawaitable(produced) else produced
        return JudgeResult(verdict=verdict, usages=[], model=None)
