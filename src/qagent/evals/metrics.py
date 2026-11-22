"""Aggregating per-case results into the numbers a report shows.

The rules that make these numbers honest rather than flattering:

* **pass rates are ground truth**, not "the gate let it through". A wrong answer approved
  by the loop is a false pass, and it gets its own rate instead of inflating the pass rate.
* **retry recovery** counts only cases that were actually retried, so a pipeline that
  retries everything cannot look good by retrying nothing.
* **cost per passed case** divides by cases that are actually correct. Cheap fabrication
  must not look efficient.
* **p50/p95 use nearest rank**, not interpolation: with thirty cases an interpolated p95
  is a made-up number between two real ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any

from ..contracts.evals import (
    CaseCategory,
    CaseResult,
    CategoryMetrics,
    EvalCase,
    Metrics,
)
from ..contracts.trace import RunResult
from .scoring import CaseScore

ACCEPTED = ("passed", "passed_after_retry")


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """One case, its run and its score: the unit every metric is computed from."""

    case: EvalCase
    result: RunResult[Any]
    score: CaseScore

    @property
    def loop_accepted(self) -> bool:
        """Whether the QA loop handed the answer on (in shadow mode: would have)."""
        return self.result.status in ACCEPTED

    @property
    def first_attempt_passed(self) -> bool:
        """Accepted on the first try *and* correct: a lucky first answer is not a pass."""
        return self.result.status == "passed" and self.score.passed

    @property
    def retries(self) -> int:
        return max(0, self.result.totals.attempts - 1)

    def failure_codes(self) -> list[str]:
        codes: list[str] = list(self.result.gate.codes()) if self.result.gate else []
        reason = self.result.escalation_reason
        if reason is not None:
            codes.append(reason)
        return codes

    def to_case_result(self) -> CaseResult:
        return CaseResult(
            case_id=self.case.case_id,
            category=self.case.category,
            status=self.result.status,
            first_attempt_passed=self.first_attempt_passed,
            final_passed=self.score.passed,
            retries=min(self.retries, 10),
            field_accuracy=self.score.field_accuracy,
            missing_fields=self.score.missing,
            wrong_fields=self.score.wrong,
            hallucinated_fields=self.score.hallucinated,
            failure_codes=self.failure_codes(),
            latency_ms=self.result.totals.latency_ms,
            cost_usd=self.result.totals.cost_usd,
        )


def build_metrics(records: Sequence[CaseRecord]) -> Metrics:
    """Compute the aggregate block of a report."""
    total = len(records)
    if total == 0:
        raise ValueError("metrics need at least one case")

    retried = [record for record in records if record.retries > 0]
    passed = [record for record in records if record.score.passed]
    total_cost = sum(record.result.totals.cost_usd for record in records)
    latencies = [record.result.totals.latency_ms for record in records]

    return Metrics(
        cases=total,
        pass_rate_first_attempt=_rate(
            sum(1 for record in records if record.first_attempt_passed), total
        ),
        pass_rate_final=_rate(len(passed), total),
        retry_rate=_rate(len(retried), total),
        retry_recovery_rate=_rate(
            sum(1 for record in retried if record.score.passed), len(retried)
        ),
        escalation_rate=_rate(
            sum(1 for record in records if record.result.status == "escalated"), total
        ),
        false_pass_rate=_rate(
            sum(
                1
                for record in records
                if record.loop_accepted and not record.score.passed
            ),
            total,
        ),
        field_accuracy_macro=round(
            sum(record.score.field_accuracy for record in records) / total, 4
        ),
        hallucination_rate=_rate(
            sum(1 for record in records if record.score.hallucinated), total
        ),
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        prompt_tokens=sum(record.result.totals.prompt_tokens for record in records),
        completion_tokens=sum(record.result.totals.completion_tokens for record in records),
        cost_usd_total=round(total_cost, 6),
        cost_usd_per_case=round(total_cost / total, 6),
        cost_usd_per_passed_case=round(total_cost / len(passed), 6) if passed else 0.0,
        by_category=_by_category(records),
    )


def percentile(values: Sequence[float], quantile: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, ceil(quantile * len(ordered)))
    return round(ordered[rank - 1], 3)


def _by_category(records: Sequence[CaseRecord]) -> dict[CaseCategory, CategoryMetrics]:
    grouped: dict[CaseCategory, list[CaseRecord]] = {}
    for record in records:
        grouped.setdefault(record.case.category, []).append(record)
    result: dict[CaseCategory, CategoryMetrics] = {}
    for category, group in grouped.items():
        result[category] = CategoryMetrics(
            cases=len(group),
            pass_rate_first_attempt=_rate(
                sum(1 for record in group if record.first_attempt_passed), len(group)
            ),
            pass_rate_final=_rate(
                sum(1 for record in group if record.score.passed), len(group)
            ),
            escalation_rate=_rate(
                sum(1 for record in group if record.result.status == "escalated"),
                len(group),
            ),
            field_accuracy=round(
                sum(record.score.field_accuracy for record in group) / len(group), 4
            ),
        )
    return result


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0
