"""Judge calibration: measuring the measurer.

A judge is another model, so its verdicts are not ground truth. Calibration compares them
with hand labels and reports the numbers that make the judge's authority debatable:
agreement, Cohen's kappa (agreement above chance) and — the one that matters most — the
list of breaches the judge approved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from ..contracts.case import Case
from ..contracts.evals import MetaEval
from .base import Judge


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One case where both the judge and a human said pass or fail."""

    case_id: str
    judge_passed: bool
    human_passed: bool


async def calibrate(
    judge: Judge,
    labelled: Sequence[tuple[Case, BaseModel, bool]],
) -> MetaEval:
    """Run the judge over hand-labelled pairs and score its agreement.

    ``labelled`` is ``(case, output, human_passed)``: the output is the artefact a human
    already judged, so the loop is not involved at all.
    """
    samples: list[CalibrationSample] = []
    for case, output, human_passed in labelled:
        result = await judge.review(case, output)
        samples.append(
            CalibrationSample(
                case_id=case.case_id,
                judge_passed=result.verdict.passed,
                human_passed=human_passed,
            )
        )
    return score_calibration(samples)


def score_calibration(samples: Sequence[CalibrationSample]) -> MetaEval:
    """Confusion-matrix maths, kept separate so it can be tested without a model."""
    n = len(samples)
    if n == 0:
        raise ValueError("calibration needs at least one labelled sample")

    true_positive = sum(1 for s in samples if s.judge_passed and s.human_passed)
    false_positive = sum(1 for s in samples if s.judge_passed and not s.human_passed)
    false_negative = sum(1 for s in samples if not s.judge_passed and s.human_passed)
    true_negative = n - true_positive - false_positive - false_negative

    agreement = (true_positive + true_negative) / n
    predicted_positive = (true_positive + false_positive) / n
    actual_positive = (true_positive + false_negative) / n
    chance = predicted_positive * actual_positive + (1 - predicted_positive) * (
        1 - actual_positive
    )
    # kappa is undefined when both raters are constant and identical; report 0 rather than
    # a division by zero, and let the report show agreement next to it.
    kappa = 0.0 if chance == 1.0 else (agreement - chance) / (1 - chance)

    return MetaEval(
        n=n,
        agreement=round(agreement, 4),
        cohens_kappa=round(kappa, 4),
        judge_precision=round(_ratio(true_positive, true_positive + false_positive), 4),
        judge_recall=round(_ratio(true_positive, true_positive + false_negative), 4),
        false_pass_ids=[s.case_id for s in samples if s.judge_passed and not s.human_passed],
        false_fail_ids=[s.case_id for s in samples if s.human_passed and not s.judge_passed],
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
