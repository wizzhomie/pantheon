"""Offline evaluation: cases, scoring rules, metrics and reports.

The same preset that guards a live run scores a regression run, so the numbers in a
report describe the loop people actually ship, not a parallel evaluation path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import JsonValue, StrictModel
from .trace import Mode, Status

CaseCategory = Literal["happy_path", "edge_case", "adversarial"]
Comparator = Literal["exact", "normalized", "fuzzy", "set", "numeric"]
Direction = Literal["up", "down", "neutral"]


class FieldSpec(StrictModel):
    """How one field of the output is compared with the golden expectation."""

    path: str = Field(min_length=1, max_length=200, description="dotted path, e.g. cargo.0.weight")
    comparator: Comparator = "normalized"
    weight: float = Field(default=1.0, gt=0)
    tolerance: float | None = Field(
        default=None, ge=0, description="absolute tolerance for the numeric comparator"
    )


class Trap(StrictModel):
    """A value the agent must never produce (hallucination bait)."""

    path: str = Field(min_length=1, max_length=200)
    forbidden: list[JsonValue] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)


class ScoringSpec(StrictModel):
    """What "correct" means for a preset, stated explicitly instead of inferred."""

    fields: list[FieldSpec] = Field(min_length=1)
    traps: list[Trap] = Field(default_factory=list)
    min_field_accuracy: float | None = Field(
        default=None, ge=0, le=1, description="hard floor; below it the case fails outright"
    )


class EvalCase(StrictModel):
    """One labelled case from the regression dataset."""

    case_id: str
    category: CaseCategory
    tags: list[str] = Field(default_factory=list, max_length=20)
    payload: JsonValue
    expected: dict[str, JsonValue] = Field(description="golden values, by dotted path")
    traps: list[Trap] = Field(default_factory=list)
    human_verdict: bool | None = Field(
        default=None, description="hand label; only used by judge calibration"
    )
    notes: str | None = Field(default=None, max_length=1000)


class CaseResult(StrictModel):
    """Outcome of one case in a regression run."""

    case_id: str
    category: CaseCategory
    status: Status
    first_attempt_passed: bool
    final_passed: bool
    retries: int = Field(ge=0, le=10)
    field_accuracy: float = Field(ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    wrong_fields: list[str] = Field(default_factory=list)
    hallucinated_fields: list[str] = Field(default_factory=list)
    failure_codes: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)


class CategoryMetrics(StrictModel):
    """The same numbers, restricted to one category of cases."""

    cases: int = Field(ge=0)
    pass_rate_first_attempt: float = Field(ge=0, le=1)
    pass_rate_final: float = Field(ge=0, le=1)
    escalation_rate: float = Field(ge=0, le=1)
    field_accuracy: float = Field(ge=0, le=1)


class Metrics(StrictModel):
    """Aggregate numbers for one run.

    Two deliberate choices: every rate is reported twice (first attempt and final), and
    cost is reported per case *and* per passed case — otherwise a pipeline that is
    cheap because it fabricates looks like the winner.
    """

    cases: int = Field(ge=0)
    pass_rate_first_attempt: float = Field(ge=0, le=1)
    pass_rate_final: float = Field(ge=0, le=1)
    retry_rate: float = Field(ge=0, le=1, default=0.0)
    retry_recovery_rate: float = Field(ge=0, le=1, default=0.0)
    escalation_rate: float = Field(ge=0, le=1, default=0.0)
    false_pass_rate: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="the gate accepted the answer and the golden fields say it was wrong",
    )
    field_accuracy_macro: float = Field(ge=0, le=1, default=0.0)
    hallucination_rate: float = Field(ge=0, le=1, default=0.0)
    latency_p50_ms: float = Field(default=0.0, ge=0)
    latency_p95_ms: float = Field(default=0.0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cost_usd_total: float = Field(default=0.0, ge=0)
    cost_usd_per_case: float = Field(default=0.0, ge=0)
    cost_usd_per_passed_case: float = Field(default=0.0, ge=0)
    by_category: dict[CaseCategory, CategoryMetrics] = Field(default_factory=dict)


class MetaEval(StrictModel):
    """Judge calibration against hand labels.

    ``false_pass_ids`` is the important field: a breach the judge approved is the one
    error class that ships silently.
    """

    n: int = Field(ge=0)
    agreement: float = Field(ge=0, le=1)
    cohens_kappa: float = Field(ge=-1, le=1)
    judge_precision: float = Field(ge=0, le=1)
    judge_recall: float = Field(ge=0, le=1)
    false_pass_ids: list[str] = Field(default_factory=list)
    false_fail_ids: list[str] = Field(default_factory=list)


class EvalReport(StrictModel):
    """Everything needed to trust (or distrust) one regression run."""

    run_id: str = Field(min_length=1, max_length=80)
    created_at: datetime
    git_sha: str = Field(default="", max_length=40)
    mode: Mode
    preset_name: str = Field(min_length=1, max_length=80)
    preset_hash: str = Field(
        min_length=1,
        description="hash of schema + checks + rubric + prompts; guards comparisons",
    )
    dataset_hash: str = Field(min_length=1)
    llm: dict[str, str] = Field(default_factory=dict)
    metrics: Metrics
    meta_eval: MetaEval | None = None
    cases: list[CaseResult] = Field(default_factory=list)


class MetricDelta(StrictModel):
    """One metric in a comparison, with the direction that counts as an improvement."""

    a: float
    b: float
    delta: float
    direction_is_good: Direction = "up"
    improved: bool = False


class CaseChange(StrictModel):
    """A case whose status changed between two runs."""

    case_id: str
    from_status: Status
    to_status: Status
    failure_codes: list[str] = Field(default_factory=list)


class ReportDiff(StrictModel):
    """Comparison of two reports.

    The hash flags are not decoration: comparing runs whose preset or dataset changed
    produces a number that looks meaningful and is not.
    """

    a: str
    b: str
    metrics: dict[str, MetricDelta] = Field(default_factory=dict)
    changed_cases: list[CaseChange] = Field(default_factory=list)
    preset_hash_changed: bool = False
    dataset_hash_changed: bool = False
