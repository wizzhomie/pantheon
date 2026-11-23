"""Plausibility bounds: numbers outside a sane interval, dates in the past.

Cross-field logic (pickup before delivery) does not live here: it belongs to the preset's
``consistency`` rules, because only the preset knows which comparisons make sense.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import MISSING, get_path

Numeric = int | float | Decimal


@dataclass(frozen=True, slots=True)
class NumberRange:
    """An inclusive interval for a numeric field."""

    path: str
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class DateRule:
    """A date that must not be in the past (or must not be in the future)."""

    path: str
    not_before_today: bool = False
    not_after_today: bool = False

    def __post_init__(self) -> None:
        if not (self.not_before_today or self.not_after_today):
            raise ValueError("a DateRule needs at least one bound")


@dataclass(frozen=True, slots=True)
class RangesCheck:
    """Interval and date-window checks over declared paths."""

    numbers: Sequence[NumberRange] = ()
    dates: Sequence[DateRule] = ()
    severity: Severity = "error"
    id: str = "ranges"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        issues = self._check_numbers(output)
        issues.extend(self._check_dates(output, ctx))
        return issues

    def _check_numbers(self, output: BaseModel) -> list[Issue]:
        issues: list[Issue] = []
        for rule in self.numbers:
            value = get_path(output, rule.path)
            if value is MISSING or value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float, Decimal)):
                continue
            number = float(value)
            unit = f" {rule.unit}" if rule.unit else ""
            if rule.minimum is not None and number < rule.minimum:
                issues.append(self._out_of_range(rule, value, f">= {rule.minimum}{unit}"))
            elif rule.maximum is not None and number > rule.maximum:
                issues.append(self._out_of_range(rule, value, f"<= {rule.maximum}{unit}"))
        return issues

    def _out_of_range(self, rule: NumberRange, value: object, bound: str) -> Issue:
        return Issue(
            code="G3_OUT_OF_RANGE",
            severity=self.severity,
            source="check",
            field_path=rule.path,
            message=f"{rule.path}={value!r} is outside the sane interval ({bound})",
            hint="re-check the number and its unit against the input",
            details={"value": str(value), "expected": bound},
        )

    def _check_dates(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        issues: list[Issue] = []
        today = ctx.now.date()
        for rule in self.dates:
            value = get_path(output, rule.path)
            when = _as_date(value)
            if when is None:
                continue
            if rule.not_before_today and when < today:
                issues.append(self._date_issue(rule, when, "is in the past"))
            elif rule.not_after_today and when > today:
                issues.append(self._date_issue(rule, when, "is in the future"))
        return issues

    def _date_issue(self, rule: DateRule, when: date, problem: str) -> Issue:
        return Issue(
            code="G3_DATE_OUT_OF_RANGE",
            severity=self.severity,
            source="check",
            field_path=rule.path,
            message=f"{rule.path}={when.isoformat()} {problem}",
            hint="confirm the date with the sender instead of guessing",
            details={"value": when.isoformat()},
        )


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
