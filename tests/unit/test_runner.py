"""The gate runner: cost order, isolation, aggregation."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agentqa.checks import CheckContext, run_checks
from agentqa.contracts import Issue


class Toy(BaseModel):
    value: int = 0


CtxFactory = Callable[..., CheckContext]


class Stub:
    def __init__(self, check_id: str, tier: str, issues: list[Issue] | None = None) -> None:
        self.id = check_id
        self.tier = tier
        self.issues = issues or []
        self.calls = 0

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        self.calls += 1
        return list(self.issues)


class Boom:
    id = "crash"
    tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        raise RuntimeError("nope")


def issue(code: str, severity: str = "error") -> Issue:
    return Issue(code=code, severity=severity, source="check", message="problem")  # type: ignore[arg-type]


def test_fast_checks_run_before_deep_ones(make_ctx: CtxFactory) -> None:
    deep = Stub("z_deep", "deep")
    fast = Stub("a_fast", "fast")
    gate = run_checks([deep, fast], Toy(), make_ctx("text"))
    assert [result.check_id for result in gate.results] == ["a_fast", "z_deep"]
    assert gate.passed is True


def test_every_check_is_recorded_even_when_clean(make_ctx: CtxFactory) -> None:
    gate = run_checks([Stub("a", "fast"), Stub("b", "fast")], Toy(), make_ctx("text"))
    recorded = [(result.check_id, result.passed) for result in gate.results]
    assert recorded == [("a", True), ("b", True)]
    assert all(result.duration_ms >= 0 for result in gate.results)


def test_blocking_issue_fails_the_gate(make_ctx: CtxFactory) -> None:
    gate = run_checks([Stub("a", "fast", [issue("G1_REQUIRED_MISSING")])], Toy(), make_ctx("text"))
    assert gate.passed is False
    assert gate.results[0].passed is False
    assert gate.codes() == ["G1_REQUIRED_MISSING"]


def test_warnings_do_not_fail_the_gate(make_ctx: CtxFactory) -> None:
    stub = Stub("a", "fast", [issue("G3_OUT_OF_RANGE", "warning")])
    gate = run_checks([stub], Toy(), make_ctx("text"))
    assert gate.passed is True
    assert gate.highest_severity == "warning"


def test_a_crashing_check_becomes_an_issue(make_ctx: CtxFactory) -> None:
    gate = run_checks([Boom()], Toy(), make_ctx("text"))  # type: ignore[list-item]
    assert gate.passed is False
    assert gate.codes() == ["G0_CHECK_CRASHED"]
    assert gate.issues[0].details["check_id"] == "crash"


def test_no_checks_means_a_passing_gate(make_ctx: CtxFactory) -> None:
    gate = run_checks([], Toy(), make_ctx("text"))
    assert gate.passed is True
    assert gate.results == []
