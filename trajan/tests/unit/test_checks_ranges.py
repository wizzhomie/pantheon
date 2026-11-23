"""Plausibility bounds: numbers and dates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel

from qagent.checks import CheckContext, DateRule, NumberRange, RangesCheck


class Toy(BaseModel):
    weight: Decimal | None = None
    volume: float | None = None
    pickup: date | None = None
    raw: str | None = None


CtxFactory = Callable[..., CheckContext]
TODAY = date(2026, 11, 22)  # FIXED_NOW in conftest


def test_values_inside_the_interval_pass(make_ctx: CtxFactory) -> None:
    check = RangesCheck(numbers=[NumberRange("weight", minimum=0.1, maximum=30_000, unit="kg")])
    output = Toy(weight=Decimal("4000"))
    assert check(output, make_ctx("text")) == []


def test_below_minimum_is_reported(make_ctx: CtxFactory) -> None:
    check = RangesCheck(numbers=[NumberRange("weight", minimum=1.0, maximum=30_000, unit="kg")])
    issues = check(Toy(weight=Decimal("0.4")), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G3_OUT_OF_RANGE"]
    assert issues[0].details["expected"] == ">= 1.0 kg"


def test_above_maximum_is_reported(make_ctx: CtxFactory) -> None:
    check = RangesCheck(numbers=[NumberRange("volume", maximum=120.0, unit="cbm")])
    issues = check(Toy(volume=480.0), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G3_OUT_OF_RANGE"]
    assert issues[0].details["expected"] == "<= 120.0 cbm"


def test_past_date_is_reported(make_ctx: CtxFactory) -> None:
    check = RangesCheck(dates=[DateRule("pickup", not_before_today=True)])
    issues = check(Toy(pickup=TODAY - timedelta(days=3)), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G3_DATE_OUT_OF_RANGE"]


def test_today_and_future_dates_pass(make_ctx: CtxFactory) -> None:
    check = RangesCheck(dates=[DateRule("pickup", not_before_today=True)])
    assert check(Toy(pickup=TODAY), make_ctx("text")) == []
    assert check(Toy(pickup=TODAY + timedelta(days=5)), make_ctx("text")) == []


def test_future_date_rejected_when_bound_says_so(make_ctx: CtxFactory) -> None:
    check = RangesCheck(dates=[DateRule("pickup", not_after_today=True)])
    issues = check(Toy(pickup=TODAY + timedelta(days=1)), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G3_DATE_OUT_OF_RANGE"]


def test_date_rule_needs_a_bound() -> None:
    with pytest.raises(ValueError, match="at least one bound"):
        DateRule("pickup")


def test_missing_and_non_numeric_values_are_skipped(make_ctx: CtxFactory) -> None:
    check = RangesCheck(numbers=[NumberRange("weight", minimum=1.0)])
    assert check(Toy(), make_ctx("text")) == []
    # a string in a numeric slot is a schema problem, not a plausibility problem
    assert check(Toy(raw="around 4 tons"), make_ctx("text")) == []


def test_string_dates_are_understood(make_ctx: CtxFactory) -> None:
    class TextDate(BaseModel):
        pickup: str | None = None

    check = RangesCheck(dates=[DateRule("pickup", not_before_today=True)])
    issues = check(TextDate(pickup="2026-11-01"), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G3_DATE_OUT_OF_RANGE"]
