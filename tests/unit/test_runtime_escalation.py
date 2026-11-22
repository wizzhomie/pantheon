"""Escalation policy: a pure decision table, so it can be read and tested in isolation."""

from __future__ import annotations

from collections.abc import Sequence

from agentqa import EscalationPolicy, Issue, JudgeVerdict, RetryPolicy, Rubric
from agentqa.runtime.escalation import budget_outcome, decide


def issue(severity: str = "error", code: str = "G1_REQUIRED_MISSING") -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        source="check",
        message="problem",
    )


def judge(rubric: Rubric, *, passed: bool, must_escalate: bool = False) -> JudgeVerdict:
    scores = {"grounding": 5, "completeness": 5} if passed else {"grounding": 1, "completeness": 1}
    return JudgeVerdict.from_rubric(rubric, scores, must_escalate=must_escalate)


def move(
    issues: Sequence[Issue] = (),
    *,
    verdict: JudgeVerdict | None = None,
    attempts_used: int = 1,
    retry: RetryPolicy | None = None,
    escalation: EscalationPolicy | None = None,
    critical_retries: int = 0,
) -> tuple[str, str | None]:
    outcome = decide(
        issues=issues,
        verdict=verdict,
        attempts_used=attempts_used,
        retry=retry or RetryPolicy(),
        escalation=escalation or EscalationPolicy(),
        critical_retries=critical_retries,
    )
    return outcome.action, outcome.reason


def test_a_clean_attempt_stops_the_loop() -> None:
    assert move() == ("stop", None)


def test_warnings_do_not_block() -> None:
    assert move([issue("warning")]) == ("stop", None)


def test_a_blocking_issue_is_retried_while_attempts_remain() -> None:
    assert move([issue()], attempts_used=1) == ("retry", None)


def test_exhausted_attempts_escalate() -> None:
    assert move([issue()], attempts_used=2) == ("escalate", "max_attempts_exhausted")


def test_a_critical_issue_stops_even_though_critical_is_retryable_in_the_policy() -> None:
    retry = RetryPolicy(retry_on=frozenset({"error", "critical"}))
    assert move([issue("critical")], retry=retry) == ("escalate", "critical_issue")


def test_critical_can_be_retried_once_when_the_policy_allows_it() -> None:
    policy = EscalationPolicy(on_critical="retry_once")
    assert move([issue("critical")], escalation=policy) == ("retry", None)
    assert move([issue("critical")], escalation=policy, critical_retries=1) == (
        "escalate",
        "critical_issue",
    )


def test_an_issue_the_policy_does_not_retry_escalates_immediately() -> None:
    retry = RetryPolicy(retry_on=frozenset({"critical"}))
    assert move([issue("error")], retry=retry) == ("escalate", "not_retryable")


def test_the_judge_can_override_a_passing_score(rubric: Rubric) -> None:
    verdict = judge(rubric, passed=True, must_escalate=True)
    assert move(verdict=verdict) == ("escalate", "judge_escalate")


def test_a_rejected_verdict_retries_even_without_blocking_issues(rubric: Rubric) -> None:
    verdict = judge(rubric, passed=False)
    assert move([issue("warning")], verdict=verdict) == ("retry", None)


def test_a_rejected_verdict_on_the_last_attempt_escalates(rubric: Rubric) -> None:
    verdict = judge(rubric, passed=False)
    assert move(verdict=verdict, attempts_used=2) == ("escalate", "max_attempts_exhausted")


def test_return_last_turns_exhaustion_into_a_failed_result() -> None:
    policy = EscalationPolicy(on_exhausted="return_last")
    assert move([issue()], attempts_used=2, escalation=policy) == ("give_up", None)


def test_the_budget_outcome_follows_its_own_policy() -> None:
    assert budget_outcome(EscalationPolicy()).action == "escalate"
    assert budget_outcome(EscalationPolicy()).reason == "budget_exhausted"
    assert budget_outcome(EscalationPolicy(on_budget="return_last")).action == "give_up"
