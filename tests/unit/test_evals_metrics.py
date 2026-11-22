"""Metrics: honest denominators, nearest-rank percentiles and per-category breakdown."""

from __future__ import annotations

import pytest
from support import BAD_SCORE, GOOD_SCORE, case_record

from qagent.evals.metrics import CaseRecord, build_metrics, percentile
from qagent.evals.scoring import CaseScore


def four_records() -> list[CaseRecord]:
    return [
        case_record("c1", score=GOOD_SCORE, cost=0.01, latency=100.0),
        case_record(
            "c2",
            score=GOOD_SCORE,
            status="passed_after_retry",
            attempts=2,
            cost=0.02,
            latency=300.0,
        ),
        case_record(
            "c3",
            score=BAD_SCORE,
            status="escalated",
            attempts=2,
            cost=0.04,
            latency=500.0,
        ),
        case_record("c4", score=BAD_SCORE, cost=0.01, latency=200.0),
    ]


def test_rates_count_ground_truth_not_only_the_gate() -> None:
    metrics = build_metrics(four_records())

    assert metrics.cases == 4
    assert metrics.pass_rate_first_attempt == 0.25  # only c1 was right on attempt 1
    assert metrics.pass_rate_final == 0.5  # c1 and c2 are right
    assert metrics.false_pass_rate == 0.25  # c4 was accepted and is wrong
    assert metrics.escalation_rate == 0.25


def test_retry_metrics_use_only_retried_cases() -> None:
    metrics = build_metrics(four_records())

    assert metrics.retry_rate == 0.5
    assert metrics.retry_recovery_rate == 0.5  # c2 recovered, c3 did not


def test_retry_recovery_is_zero_when_nothing_was_retried() -> None:
    metrics = build_metrics([case_record("c1"), case_record("c2")])

    assert metrics.retry_rate == 0.0
    assert metrics.retry_recovery_rate == 0.0


def test_cost_is_reported_per_case_and_per_passed_case() -> None:
    metrics = build_metrics(four_records())

    assert metrics.cost_usd_total == pytest.approx(0.08)
    assert metrics.cost_usd_per_case == pytest.approx(0.02)
    assert metrics.cost_usd_per_passed_case == pytest.approx(0.04)


def test_cost_per_passed_case_is_zero_when_nothing_passed() -> None:
    metrics = build_metrics([case_record("c1", score=BAD_SCORE, cost=0.5)])

    assert metrics.cost_usd_per_passed_case == 0.0
    assert metrics.cost_usd_total == pytest.approx(0.5)


def test_accuracy_is_a_macro_average_and_hallucinations_count_cases() -> None:
    hallucinating = case_record(
        "c5", score=CaseScore(field_accuracy=0.9, passed=False, hallucinated=["qty"])
    )
    metrics = build_metrics([case_record("c1"), hallucinating])

    assert metrics.field_accuracy_macro == 0.95
    assert metrics.hallucination_rate == 0.5


def test_latency_percentiles_use_nearest_rank() -> None:
    records = [
        case_record(f"c{index}", latency=float(value))
        for index, value in enumerate([100, 200, 300, 500])
    ]
    metrics = build_metrics(records)

    assert metrics.latency_p50_ms == 200.0
    assert metrics.latency_p95_ms == 500.0


def test_percentile_edges() -> None:
    assert percentile([], 0.95) == 0.0
    assert percentile([42.0], 0.5) == 42.0
    assert percentile([1.0, 2.0], 0.95) == 2.0
    assert percentile([3.0, 1.0, 2.0], 0.5) == 2.0


def test_categories_are_broken_down_separately() -> None:
    records = [
        case_record("h1", category="happy_path", score=GOOD_SCORE),
        case_record("h2", category="happy_path", score=BAD_SCORE),
        case_record("a1", category="adversarial", score=GOOD_SCORE),
    ]
    metrics = build_metrics(records)

    assert metrics.by_category["happy_path"].cases == 2
    assert metrics.by_category["happy_path"].pass_rate_final == 0.5
    assert metrics.by_category["adversarial"].pass_rate_final == 1.0


def test_a_case_result_carries_the_loop_outcome_and_the_codes() -> None:
    record = case_record("c3", score=BAD_SCORE, status="escalated", attempts=2)
    result = record.to_case_result()

    assert result.status == "escalated"
    assert result.final_passed is False
    assert result.first_attempt_passed is False
    assert result.retries == 1
    assert result.failure_codes == ["G3_OUT_OF_RANGE", "max_attempts_exhausted"]
    assert result.wrong_fields == ["qty"]


def test_first_attempt_pass_requires_correctness_not_only_acceptance() -> None:
    accepted_but_wrong = case_record("c1", score=BAD_SCORE)
    recovered = case_record("c2", score=GOOD_SCORE, status="passed_after_retry", attempts=2)

    assert accepted_but_wrong.first_attempt_passed is False
    assert recovered.first_attempt_passed is False
    assert recovered.to_case_result().final_passed is True


def test_metrics_reject_an_empty_sample() -> None:
    with pytest.raises(ValueError, match="at least one case"):
        build_metrics([])
