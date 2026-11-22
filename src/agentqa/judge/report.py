"""What the judge model is asked to return.

Note what is **absent**: there is no ``passed`` and no ``score`` field. Asking a model to
grade itself and then trusting the answer is how "LLM-as-a-judge" becomes decoration; here
the model supplies observations, and :class:`Rubric` turns them into a decision.
"""

from __future__ import annotations

from pydantic import Field

from ..contracts.base import StrictModel
from ..contracts.issues import Severity
from ..contracts.verdict import CriterionScore


class JudgeIssue(StrictModel):
    """A defect as described by the model: no taxonomy knowledge required."""

    code: str | None = Field(
        default=None,
        max_length=60,
        description="short uppercase id such as OMISSION or FAKE_QUOTE; normalised by the core",
    )
    severity: Severity = "warning"
    field_path: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=500)
    hint: str | None = Field(default=None, max_length=300)


class JudgeReport(StrictModel):
    """The judge's structured answer, before any scoring."""

    criterion_scores: dict[str, CriterionScore] = Field(
        description="one 1..5 integer per rubric criterion id"
    )
    issues: list[JudgeIssue] = Field(default_factory=list, max_length=20)
    fix_instructions: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="imperative, field-scoped: what to change and where",
    )
    must_escalate: bool = Field(
        default=False,
        description="true when only a human can decide (ambiguous or contradictory input)",
    )
    confidence: float = Field(default=0.7, ge=0, le=1)
