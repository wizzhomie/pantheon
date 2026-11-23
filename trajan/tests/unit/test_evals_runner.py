"""The runner: one dataset through the same loop that guards production."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from support import ScriptedAgent, eval_case, toy_preset, toy_scoring, verdict

from trajan import (
    Dataset,
    FunctionJudge,
    JudgePolicy,
    QAConfig,
    guard,
)
from trajan.evals import evaluate

AT = datetime(2026, 11, 22, 17, 0, tzinfo=UTC)


async def agent(case: object, feedback: object) -> dict[str, object]:
    return {"qty": 2, "name": case.payload}  # type: ignore[attr-defined]


def dataset() -> Dataset:
    return Dataset(
        name="toy",
        cases=[
            eval_case("c1", payload="two boxes", expected={"qty": 2, "name": "two boxes"}),
            eval_case("c2", payload="three boxes", expected={"qty": 3, "name": "three boxes"}),
        ],
    )


def make_guarded(rubric: object, *, judge: object = None, config: QAConfig | None = None):
    preset = toy_preset(rubric, checks=[], scoring=toy_scoring())
    return guard(
        agent,
        preset,
        judge=judge,
        config=config or QAConfig(judge=JudgePolicy(enabled=False)),
    )


async def test_evaluate_scores_every_case(rubric: object) -> None:
    report = await evaluate(
        make_guarded(rubric),
        dataset(),
        concurrency=2,
        created_at=AT,
        revision="deadbee",
    )

    assert report.run_id == "20261122T170000-guarded-deadbee"
    assert report.metrics.cases == 2
    assert report.metrics.pass_rate_final == 0.5
    assert report.dataset_hash == dataset().digest()
    assert [case.case_id for case in report.cases] == ["c1", "c2"]
    assert report.cases[0].final_passed is True
    assert report.cases[1].final_passed is False
    assert report.cases[1].wrong_fields == ["qty"]


async def test_a_plain_list_of_cases_is_accepted(rubric: object) -> None:
    report = await evaluate(
        make_guarded(rubric),
        [eval_case("c1", payload="two boxes", expected={"qty": 2, "name": "two boxes"})],
        concurrency=1,
    )

    assert report.metrics.cases == 1
    assert report.metrics.pass_rate_final == 1.0


async def test_evaluate_requires_a_scoring_spec(rubric: object) -> None:
    preset = toy_preset(rubric, checks=[])
    guarded = guard(
        agent, preset, config=QAConfig(judge=JudgePolicy(enabled=False))
    )

    with pytest.raises(ValueError, match=r"preset.scoring is required"):
        await evaluate(guarded, dataset())


async def test_the_mode_comes_from_the_guarded_agent(rubric: object) -> None:
    baseline = make_guarded(rubric).with_mode("baseline")

    report = await evaluate(baseline, dataset(), concurrency=1, created_at=AT, revision="x")

    assert report.mode == "baseline"
    assert report.metrics.cases == 2


async def test_hand_labels_calibrate_the_judge(rubric: object) -> None:
    labelled = Dataset(
        cases=[
            eval_case("c1", payload="two boxes", expected={"qty": 2}, human_verdict=True),
            eval_case("c2", payload="two boxes", expected={"qty": 3}, human_verdict=False),
        ]
    )
    judge = FunctionJudge(lambda case, output: verdict(rubric, good=True))
    guarded = make_guarded(rubric, judge=judge, config=QAConfig())

    report = await evaluate(guarded, labelled, concurrency=1, created_at=AT, revision="x")

    assert report.meta_eval is not None
    assert report.meta_eval.n == 2
    assert report.meta_eval.agreement == 0.5
    assert report.meta_eval.false_pass_ids == ["c2"]


async def test_without_labels_there_is_no_calibration(rubric: object) -> None:
    judge = FunctionJudge(lambda case, output: verdict(rubric, good=True))
    guarded = make_guarded(rubric, judge=judge, config=QAConfig())

    report = await evaluate(guarded, dataset(), concurrency=1, created_at=AT, revision="x")

    assert report.meta_eval is None


async def test_evaluate_rejects_zero_concurrency(rubric: object) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        await evaluate(make_guarded(rubric), dataset(), concurrency=0)


async def test_an_escalated_case_is_scored_as_failed_not_skipped(rubric: object) -> None:
    from support import QtyCheck

    preset = toy_preset(rubric, checks=[QtyCheck()], scoring=toy_scoring())
    guarded = guard(
        ScriptedAgent({"qty": 0}), preset, config=QAConfig(judge=JudgePolicy(enabled=False))
    )

    report = await evaluate(
        guarded,
        Dataset(cases=[eval_case("c1", expected={"qty": 2, "name": "two boxes"})]),
        concurrency=1,
    )

    assert report.cases[0].status == "escalated"
    assert report.cases[0].final_passed is False
    assert report.metrics.escalation_rate == 1.0
