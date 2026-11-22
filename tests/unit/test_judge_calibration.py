"""Calibration maths and the calibration run."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from agentqa.contracts import Case, JudgeVerdict, Rubric
from agentqa.judge import CalibrationSample, FunctionJudge, calibrate, score_calibration


def sample(case_id: str, judge: bool, human: bool) -> CalibrationSample:
    return CalibrationSample(case_id=case_id, judge_passed=judge, human_passed=human)


def test_confusion_matrix_maths() -> None:
    meta = score_calibration(
        [
            sample("c1", True, True),
            sample("c2", True, True),
            sample("c3", False, False),
            sample("c4", True, False),  # the judge approved a breach
        ]
    )
    assert meta.n == 4
    assert meta.agreement == pytest.approx(0.75)
    assert meta.judge_precision == pytest.approx(0.6667, abs=1e-4)
    assert meta.judge_recall == pytest.approx(1.0)
    assert meta.cohens_kappa == pytest.approx(0.5)
    assert meta.false_pass_ids == ["c4"]
    assert meta.false_fail_ids == []


def test_perfect_balanced_agreement_gives_kappa_one() -> None:
    meta = score_calibration([sample("c1", True, True), sample("c2", False, False)])
    assert meta.agreement == 1.0
    assert meta.cohens_kappa == pytest.approx(1.0)


def test_always_passing_judge_is_exposed_by_kappa() -> None:
    samples = [sample(f"c{index}", True, True) for index in range(9)] + [sample("c9", True, False)]
    meta = score_calibration(samples)
    assert meta.agreement == pytest.approx(0.9)
    assert meta.cohens_kappa < 0.1
    assert meta.false_pass_ids == ["c9"]


def test_degenerate_constant_labels_do_not_divide_by_zero() -> None:
    meta = score_calibration([sample("c1", True, True), sample("c2", True, True)])
    assert meta.agreement == 1.0
    assert meta.cohens_kappa == 0.0  # undefined by definition; reported, not crashed


def test_calibration_needs_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        score_calibration([])


class Output(BaseModel):
    cargo: str


def case(case_id: str) -> Case:
    return Case(case_id=case_id, payload="cargo line")


def make_judge(rubric: Rubric) -> FunctionJudge:
    def producer(current: Case, output: Output) -> JudgeVerdict:
        good = "pallet" in output.cargo
        scores = {"grounding": 5 if good else 1, "completeness": 5 if good else 1}
        return JudgeVerdict.from_rubric(rubric, scores)

    return FunctionJudge(producer)


async def test_calibrate_runs_the_judge_over_labelled_outputs(rubric: Rubric) -> None:
    labelled = [
        (case("c1"), Output(cargo="4 pallets"), True),
        (case("c2"), Output(cargo="some freight"), False),
    ]
    meta = await calibrate(make_judge(rubric), labelled)
    assert meta.n == 2
    assert meta.agreement == 1.0
    assert meta.false_pass_ids == []
    assert meta.false_fail_ids == []


async def test_calibrate_flags_a_breach_the_judge_approved(rubric: Rubric) -> None:
    judge = make_judge(rubric)
    labelled = [(case("c1"), Output(cargo="4 pallets"), False)]
    meta = await calibrate(judge, labelled)
    assert meta.false_pass_ids == ["c1"]
    assert meta.false_fail_ids == []


async def test_function_judge_counts_calls(rubric: Rubric) -> None:
    judge = make_judge(rubric)
    await judge.review(case("c1"), Output(cargo="4 pallets"))
    assert judge.calls == 1
