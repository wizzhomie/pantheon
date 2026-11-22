"""The judge rubric: criteria with weights, and the verdict arithmetic that lives in code.

A rubric is data, not prose. Two consequences worth the extra model:

* the same rubric generates the judge prompt and the scoring formula, so they cannot
  drift apart;
* changing weights or the pass threshold is a config edit that shows up in the run's
  ``preset_hash`` rather than an invisible prompt rewrite.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import Field, field_validator

from .base import StrictModel
from .issues import Issue, Severity

CRITERION_ID_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"

MIN_SCORE = 1
MAX_SCORE = 5


class Criterion(StrictModel):
    """One axis of judgement, with an explicit weight."""

    id: str = Field(pattern=CRITERION_ID_PATTERN, description="stable id, e.g. grounding")
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(
        min_length=1,
        max_length=600,
        description="what a 5 and a 1 mean here; goes into the judge prompt verbatim",
    )
    weight: float = Field(gt=0, le=1, description="relative weight; normalised at scoring")


class Rubric(StrictModel):
    """Criteria plus the threshold that turns scores into a pass/fail decision."""

    criteria: list[Criterion] = Field(min_length=1, max_length=12)
    pass_score: float = Field(default=0.75, ge=0, le=1)
    blocking: frozenset[Severity] = frozenset({"error", "critical"})

    @field_validator("criteria")
    @classmethod
    def _unique_ids(cls, criteria: list[Criterion]) -> list[Criterion]:
        ids = [criterion.id for criterion in criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion ids must be unique within a rubric")
        return criteria

    def weighted_score(self, scores: Mapping[str, int]) -> float:
        """Collapse 1..5 criterion scores into a single 0..1 score.

        Weights need not sum to 1: they are normalised, so a rubric can be extended
        without rebalancing every number.
        """
        expected = {criterion.id for criterion in self.criteria}
        unknown = set(scores) - expected
        missing = expected - set(scores)
        if unknown:
            raise ValueError(f"unknown criteria: {sorted(unknown)}")
        if missing:
            raise ValueError(f"missing criteria: {sorted(missing)}")
        for criterion_id, raw in scores.items():
            if not MIN_SCORE <= raw <= MAX_SCORE:
                raise ValueError(
                    f"score for {criterion_id!r} must be within "
                    f"{MIN_SCORE}..{MAX_SCORE}, got {raw}"
                )
        total_weight = sum(criterion.weight for criterion in self.criteria)
        weighted = sum(
            criterion.weight * ((scores[criterion.id] - MIN_SCORE) / (MAX_SCORE - MIN_SCORE))
            for criterion in self.criteria
        )
        return weighted / total_weight

    def decide(self, scores: Mapping[str, int], issues: Sequence[Issue]) -> tuple[bool, float]:
        """Return ``(passed, score)``.

        The decision rule lives here, not in the judge prompt: a model that grades
        itself is systematically generous, so the final word belongs to code.
        """
        # Round before comparing: 0.7 * 0.75 + 0.3 * 0.75 evaluates to
        # 0.7499999999999999, and a result that sits exactly on the threshold must not
        # fail because of binary float representation.
        score = round(self.weighted_score(scores), 6)
        blocked = any(issue.severity in self.blocking for issue in issues)
        return (not blocked and score >= self.pass_score), round(score, 4)
