"""Structured judge output.

``passed`` and ``score`` are derived values: they are recomputed from the rubric by
:meth:`JudgeVerdict.from_rubric`, never taken from the model's own claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import Field

from .base import StrictModel
from .issues import Issue
from .rubric import Rubric

CriterionScore = Annotated[int, Field(ge=1, le=5, description="1 (bad) .. 5 (good)")]


class JudgeVerdict(StrictModel):
    """What the critic concluded about one attempt."""

    passed: bool = Field(description="recomputed from the rubric by the core")
    score: float = Field(ge=0, le=1, description="weighted rubric score")
    criterion_scores: dict[str, CriterionScore] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list, max_length=20)
    fix_instructions: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="imperative, field-scoped instructions for the retry prompt",
    )
    must_escalate: bool = Field(
        default=False,
        description="the judge saw something only a human can decide",
    )
    confidence: float = Field(default=1.0, ge=0, le=1)

    @property
    def blocking_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.blocking]

    @classmethod
    def from_rubric(
        cls,
        rubric: Rubric,
        scores: dict[str, int],
        issues: Sequence[Issue] = (),
        fix_instructions: Sequence[str] = (),
        *,
        must_escalate: bool = False,
        confidence: float = 1.0,
    ) -> JudgeVerdict:
        """Build a verdict, computing ``passed`` and ``score`` from the rubric."""
        issue_list = list(issues)
        passed, score = rubric.decide(scores, issue_list)
        return cls(
            passed=passed,
            score=score,
            criterion_scores={key: value for key, value in scores.items()},
            issues=issue_list,
            fix_instructions=list(fix_instructions),
            must_escalate=must_escalate,
            confidence=confidence,
        )
