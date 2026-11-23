"""The budget: money, wall-clock time and judge calls, each with its own rule."""

from __future__ import annotations

import pytest

from trajan import RunLimits, Usage
from trajan.runtime.budget import Budget


class Clock:
    """A hand-cranked clock: latency tests must not sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000


def usage(cost: float = 0.01, *, price_known: bool = True) -> Usage:
    return Usage(
        model="m",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=cost,
        latency_ms=10.0,
        price_known=price_known,
    )


def test_spending_accumulates_money_and_tokens() -> None:
    budget = Budget(RunLimits(max_cost_usd=1.0), clock=Clock())
    budget.spend(usage(0.02))
    budget.spend(usage(0.03))

    assert budget.cost_usd == pytest.approx(0.05)
    assert budget.prompt_tokens == 200
    assert budget.completion_tokens == 100
    assert budget.price_complete is True
    assert budget.breach() is None


def test_an_unknown_price_marks_the_run_incomplete() -> None:
    budget = Budget(RunLimits(), clock=Clock())
    budget.spend(usage(0.0, price_known=False))

    assert budget.price_complete is False


def test_the_cost_cap_is_reached_exactly_at_the_limit() -> None:
    budget = Budget(RunLimits(max_cost_usd=0.05), clock=Clock())
    budget.spend(usage(0.049))
    assert budget.breach() is None

    budget.spend(usage(0.001))
    assert budget.breach() == "cost_usd"


def test_no_cost_cap_means_no_cost_breach() -> None:
    budget = Budget(RunLimits(max_cost_usd=None), clock=Clock())
    budget.spend(usage(500.0))

    assert budget.breach() is None


def test_latency_is_wall_clock_not_the_sum_of_calls() -> None:
    clock = Clock()
    budget = Budget(RunLimits(max_latency_ms=100), clock=clock)
    clock.advance_ms(60)
    budget.spend(usage(0.01))
    assert budget.elapsed_ms() == pytest.approx(60.0)
    assert budget.breach() is None

    clock.advance_ms(50)
    assert budget.breach() == "latency_ms"


def test_the_judge_call_budget_is_counted_separately() -> None:
    budget = Budget(RunLimits(max_judge_calls=1), clock=Clock())
    assert budget.judge_allowed() is True

    budget.note_judge_call()
    assert budget.judge_calls == 1
    assert budget.judge_allowed() is False


def test_zero_judge_calls_forbids_the_judge_entirely() -> None:
    budget = Budget(RunLimits(max_judge_calls=0), clock=Clock())

    assert budget.judge_allowed() is False
    assert budget.breach() is None  # a judge cap is not a money cap

