"""The judge contract.

A judge answers one question — "is this output acceptable given this input?" — and returns
a verdict plus the price of asking. The verdict's ``passed`` flag is never produced by the
model: it is recomputed from the rubric, which is why the protocol returns a *result*
(verdict + usage) rather than the model's raw answer.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from ..contracts.base import StrictModel
from ..contracts.case import Case
from ..contracts.issues import GateResult
from ..contracts.trace import Usage
from ..contracts.verdict import JudgeVerdict


class JudgeResult(StrictModel):
    """A verdict plus the calls it took to get it."""

    verdict: JudgeVerdict
    usages: list[Usage] = Field(default_factory=list)
    model: str | None = None


class Judge(Protocol):
    """Structural type for anything that can review an output."""

    name: str

    async def review(
        self,
        case: Case,
        output: BaseModel,
        gate: GateResult | None = None,
    ) -> JudgeResult: ...
