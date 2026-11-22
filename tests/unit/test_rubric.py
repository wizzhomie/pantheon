"""Rubric arithmetic and the guarantee that the judge cannot grade itself."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from agentqa.contracts import Criterion, JudgeVerdict, Rubric

PERFECT = {"grounding": 5, "completeness": 5}
WORST = {"grounding": 1, "completeness": 1}


def test_scores_map_onto_zero_to_one(rubric: Rubric) -> None:
    assert rubric.weighted_score(PERFECT) == 1.0
    assert rubric.weighted_score(WORST) == 0.0
    assert rubric.weighted_score({"grounding": 5, "completeness": 1}) == pytest.approx(0.7)
    assert rubric.weighted_score({"grounding": 4, "completeness": 4}) == pytest.approx(0.75)


def test_weights_need_not_sum_to_one(rubric: Rubric) -> None:
    stretched = Rubric(
        criteria=[criterion.model_copy(update={"weight": 7.0}) for criterion in rubric.criteria],
        pass_score=0.75,
    )
    assert stretched.weighted_score(PERFECT) == 1.0


def test_incomplete_or_unknown_scores_are_errors(rubric: Rubric) -> None:
    with pytest.raises(ValueError, match="missing criteria"):
        rubric.weighted_score({"grounding": 5})
    with pytest.raises(ValueError, match="unknown criteria"):
        rubric.weighted_score({**PERFECT, "style": 3})


@pytest.mark.parametrize("raw", [0, 6, -1, 10])
def test_scores_outside_one_to_five_are_errors(rubric: Rubric, raw: int) -> None:
    with pytest.raises(ValueError, match="must be within"):
        rubric.weighted_score({"grounding": raw, "completeness": 3})


def test_decide_requires_threshold_and_no_blocking_issue(
    rubric: Rubric, issue: Callable[..., Issue]
) -> None:
    passed, score = rubric.decide({"grounding": 4, "completeness": 4}, [])
    assert passed is True
    assert score == 0.75

    blocked, _ = rubric.decide({"grounding": 4, "completeness": 4}, [issue(severity="error")])
    assert blocked is False

    warned, _ = rubric.decide({"grounding": 4, "completeness": 4}, [issue(severity="warning")])
    assert warned is True


def test_decide_fails_below_threshold(rubric: Rubric) -> None:
    passed, score = rubric.decide({"grounding": 3, "completeness": 3}, [])
    assert passed is False
    assert score == 0.5


def test_blocking_severities_are_configurable(
    rubric: Rubric, issue: Callable[..., Issue]
) -> None:
    lenient = rubric.model_copy(update={"blocking": frozenset({"critical"})})
    passed, _ = lenient.decide(PERFECT, [issue(severity="error")])
    assert passed is True


def test_duplicate_criterion_ids_are_rejected() -> None:
    criterion = Criterion(id="aa", title="A", description="A", weight=0.5)
    with pytest.raises(ValidationError):
        Rubric(criteria=[criterion, criterion])


def test_pass_score_bounds() -> None:
    criterion = Criterion(id="aa", title="A", description="A", weight=1.0)
    with pytest.raises(ValidationError):
        Rubric(criteria=[criterion], pass_score=1.5)


def test_verdict_passed_is_computed_not_claimed(
    rubric: Rubric, issue: Callable[..., Issue]
) -> None:
    verdict = JudgeVerdict.from_rubric(
        rubric, {"grounding": 5, "completeness": 5}, [issue(severity="error")]
    )
    assert verdict.passed is False
    assert verdict.score == 1.0

    clean = JudgeVerdict.from_rubric(rubric, {"grounding": 5, "completeness": 5})
    assert clean.passed is True
    assert clean.score == 1.0


def test_verdict_rejects_out_of_range_criterion_scores() -> None:
    with pytest.raises(ValidationError):
        JudgeVerdict(passed=True, score=0.9, criterion_scores={"grounding": 9})


def test_verdict_limits_fix_instructions() -> None:
    with pytest.raises(ValidationError):
        instructions = [f"fix {index}" for index in range(6)]
        JudgeVerdict(passed=False, score=0.2, fix_instructions=instructions)


def test_verdict_blocks_on_its_own_issues(
    rubric: Rubric, issue: Callable[..., Issue]
) -> None:
    verdict = JudgeVerdict.from_rubric(rubric, {"grounding": 5, "completeness": 5}, [issue()])
    assert [item.code for item in verdict.blocking_issues] == ["G1_REQUIRED_MISSING"]

