"""Shared pieces for the runtime tests: a tiny domain, a check and a scripted agent.

The point of these tests is the loop, not the domain, so the fixture domain is about as
small as one can be: an output with two fields, one check, one rubric.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from qagent import (
    Case,
    CheckContext,
    Criterion,
    EvalCase,
    Feedback,
    FieldSpec,
    Issue,
    JudgeVerdict,
    Preset,
    Rubric,
    RunResult,
    ScoringSpec,
    Totals,
    Usage,
)
from qagent.evals.metrics import CaseRecord
from qagent.evals.scoring import CaseScore

ReplyItem = Any


class ToyOutput(BaseModel):
    name: str = ""
    qty: int = 0


def qty_issue(severity: str = "error", code: str = "G3_OUT_OF_RANGE") -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        source="check",
        field_path="qty",
        message="qty must be positive",
        hint="extract a positive quantity",
    )


class QtyCheck:
    """The Check protocol in eight lines: ``qty`` must be positive."""

    id = "qty"
    tier = "fast"

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        self.calls += 1
        if not isinstance(output, ToyOutput) or output.qty > 0:
            return []
        return [qty_issue()]


class ScriptedAgent:
    """An agent that returns scripted entries in order and records every call.

    An entry may be a value, a :class:`BaseException` to raise, or a callable that
    receives ``(case, feedback)`` and returns the reply.
    """

    def __init__(self, *replies: ReplyItem) -> None:
        self.replies = list(replies)
        self.calls: list[Feedback | None] = []

    async def __call__(self, case: Case, feedback: Feedback | None) -> ReplyItem:
        self.calls.append(feedback)
        if not self.replies:
            raise AssertionError("the agent was called more often than the test scripted")
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(case, feedback)
        return item


def toy_preset(
    rubric: Rubric, *, checks: list[Any] | None = None, **overrides: Any
) -> Preset[ToyOutput]:
    return Preset(
        name="toy",
        output_model=ToyOutput,
        rubric=rubric,
        checks=[QtyCheck()] if checks is None else checks,
        agent_model="toy-model",
        **overrides,
    )


def verdict(rubric: Rubric, *, good: bool = True, **kwargs: Any) -> JudgeVerdict:
    scores = {"grounding": 5, "completeness": 5} if good else {"grounding": 1, "completeness": 1}
    return JudgeVerdict.from_rubric(rubric, scores, kwargs.pop("issues", ()), **kwargs)


def failing_judge(rubric: Rubric) -> Callable[[Case, BaseModel], JudgeVerdict]:
    def producer(case: Case, output: BaseModel) -> JudgeVerdict:
        return verdict(rubric, good=False)

    return producer


def paid_usage(cost_usd: float = 0.2, *, model: str = "toy-model") -> Usage:
    return Usage(
        model=model,
        prompt_tokens=1_000,
        completion_tokens=500,
        cost_usd=cost_usd,
        latency_ms=5.0,
    )


def criteria() -> list[Criterion]:
    return [
        Criterion(
            id="grounding",
            title="Grounding",
            description="Every value is supported by the input.",
            weight=0.7,
        ),
        Criterion(
            id="completeness",
            title="Completeness",
            description="Every fact in the input was extracted.",
            weight=0.3,
        ),
    ]


def eval_case(
    case_id: str = "case-1",
    *,
    category: str = "happy_path",
    payload: str = "two boxes",
    expected: dict[str, Any] | None = None,
    traps: list[Any] | None = None,
    human_verdict: bool | None = None,
    notes: str | None = None,
) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        category=category,  # type: ignore[arg-type]
        payload=payload,
        expected={"qty": 2, "name": "two boxes"} if expected is None else expected,
        traps=traps or [],
        human_verdict=human_verdict,
        notes=notes,
    )


def toy_scoring() -> ScoringSpec:
    return ScoringSpec(
        fields=[
            FieldSpec(path="qty", comparator="numeric"),
            FieldSpec(path="name"),
        ]
    )


GOOD_SCORE = CaseScore(field_accuracy=1.0, passed=True)
BAD_SCORE = CaseScore(field_accuracy=0.5, passed=False, wrong=["qty"])


def make_result(
    case_id: str = "case-1",
    *,
    status: str = "passed",
    mode: str = "guarded",
    attempts: int = 1,
    cost: float = 0.01,
    latency: float = 100.0,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    output: BaseModel | None = None,
    gate: Any = None,
    verdict: JudgeVerdict | None = None,
    reason: str | None = None,
) -> RunResult[ToyOutput]:
    if status in ("passed", "passed_after_retry") and output is None:
        output = ToyOutput(qty=2, name="two boxes")
    if status == "escalated" and reason is None:
        reason = "max_attempts_exhausted"
    return RunResult[ToyOutput](
        case_id=case_id,
        mode=mode,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        output=output,  # type: ignore[arg-type]
        gate=gate,
        verdict=verdict,
        escalation_reason=reason,  # type: ignore[arg-type]
        would_have_blocked=(status in ("escalated", "failed") if mode == "shadow" else None),
        totals=Totals(
            latency_ms=latency,
            attempts=attempts,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        ),
    )


def case_record(
    case_id: str = "case-1",
    *,
    category: str = "happy_path",
    score: CaseScore = GOOD_SCORE,
    status: str = "passed",
    attempts: int = 1,
    cost: float = 0.01,
    latency: float = 100.0,
) -> CaseRecord:
    case = eval_case(case_id, category=category)
    return CaseRecord(
        case=case,
        result=make_result(
            case_id,
            status=status,
            attempts=attempts,
            cost=cost,
            latency=latency,
            gate=_gate_for(status),
        ),
        score=score,
    )


def _gate_for(status: str) -> Any:
    if status == "escalated":
        from qagent import CheckResult, GateResult

        issue = qty_issue()
        return GateResult(
            passed=False,
            results=[CheckResult(check_id="qty", passed=False, issues=[issue])],
            issues=[issue],
        )
    return None
