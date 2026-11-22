"""Where a failed attempt goes: another try, a human, or a best-effort result.

This is a pure decision over the current failure, so the policy can be tested without
running an agent: given these issues, these attempts and this configuration, what happens
next? The loop owns the mechanics; this module owns the rule.

Order of precedence is the part worth arguing about:

1. the judge asking for a human wins over everything, because a model that says "this needs
   a decision" is telling us the rubric does not cover the case;
2. a critical issue stops the loop even though the severity appears in ``retry_on``:
   a leaked PII value or an empty answer is not improved by asking again;
3. a rejected judge verdict is retryable regardless of its issue severities — the verdict
   itself is the signal when the score is below the threshold;
4. when nothing can pass and nothing can be retried, the policy decides between escalating
   and returning the last answer with status ``failed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..config import EscalationPolicy, RetryPolicy
from ..contracts.issues import Issue
from ..contracts.trace import EscalationReason
from ..contracts.verdict import JudgeVerdict

Action = Literal["stop", "retry", "escalate", "give_up"]
"""``stop`` means "this result passes"; ``give_up`` returns the last answer as ``failed``."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """The next move, and why (``reason`` is set only for escalation)."""

    action: Action
    reason: EscalationReason | None = None


def decide(
    *,
    issues: Sequence[Issue],
    verdict: JudgeVerdict | None,
    attempts_used: int,
    retry: RetryPolicy,
    escalation: EscalationPolicy,
    critical_retries: int = 0,
) -> Outcome:
    """Pick the next move after one failed attempt."""
    if verdict is not None and verdict.must_escalate:
        return Outcome("escalate", "judge_escalate")

    blocking = [issue for issue in issues if issue.blocking]
    critical = [issue for issue in blocking if issue.severity == "critical"]
    if critical:
        can_retry_once = (
            escalation.on_critical == "retry_once"
            and critical_retries < 1
            and attempts_used < retry.max_attempts
        )
        if can_retry_once:
            return Outcome("retry")
        return Outcome("escalate", "critical_issue")

    judge_rejected = verdict is not None and not verdict.passed
    if not blocking and not judge_rejected:
        return Outcome("stop")

    retryable = judge_rejected or any(issue.severity in retry.retry_on for issue in blocking)
    if retryable:
        if attempts_used < retry.max_attempts:
            return Outcome("retry")
        return _exhausted(escalation)
    return Outcome("escalate", "not_retryable")


def budget_outcome(escalation: EscalationPolicy) -> Outcome:
    """What to do when money or time ran out before the result could pass."""
    if escalation.on_budget == "return_last":
        return Outcome("give_up")
    return Outcome("escalate", "budget_exhausted")


def _exhausted(escalation: EscalationPolicy) -> Outcome:
    if escalation.on_exhausted == "return_last":
        return Outcome("give_up")
    return Outcome("escalate", "max_attempts_exhausted")
