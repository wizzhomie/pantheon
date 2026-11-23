"""The verdict: four answers, computed from evidence, by code.

The rule that matters: a verdict is not an opinion, and it is not a model's score. It is a
function of what the run actually saw — which checks passed, which could not run, and which
findings were confirmed. That is why it lives here and not in a prompt: a model that can
change the verdict can be talked into one.

The four answers exist because two are not enough. "Not failed" and "passed" are different
claims, and collapsing them is how an unverified change gets shipped:

``PASS``
    every required gate ran and passed, and nothing blocking was confirmed.
``PASS WITH CONDITIONS``
    shippable, but something is known and unresolved: a non-blocking finding, an unverified
    claim, a gap a human should follow up on.
``HOLD``
    a required gate failed or a blocking defect is confirmed. Do not ship.
``INCONCLUSIVE``
    the run cannot answer: a required gate did not run, the tool is missing, the budget ran
    out, or the run itself broke. Not a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from ..contracts.base import StrictModel
from .contracts import CheckRun, CheckStatus, VerificationRun
from .findings import Finding

VerdictStatus = Literal["pass", "pass_with_conditions", "hold", "inconclusive"]

VerdictCode = Literal[
    "checks-passed",
    "nothing-to-verify",
    "run-failed",
    "check-failed",
    "check-timed-out",
    "check-could-not-run",
    "check-skipped",
    "check-not-applicable",
    "blocking-finding",
    "non-blocking-finding",
    "unresolved-finding",
]
"""Stable ids: a report, a test and a reader all point at the same reason by name."""

SKIP_CODES: dict[str, VerdictCode] = {
    "error": "check-could-not-run",
    "skipped": "check-skipped",
    "not_applicable": "check-not-applicable",
}

HEADLINES: dict[VerdictStatus, str] = {
    "pass": "PASS",
    "pass_with_conditions": "PASS WITH CONDITIONS",
    "hold": "HOLD",
    "inconclusive": "INCONCLUSIVE",
}


class VerdictReason(StrictModel):
    """Why the verdict is what it is, in one line, with the evidence it rests on."""

    code: VerdictCode
    detail: str = Field(min_length=1, max_length=400)


class Verdict(StrictModel):
    """The conclusion of a run, and the reasons it cannot be argued away from."""

    status: VerdictStatus
    reasons: list[VerdictReason] = Field(default_factory=list)
    blocking_findings: list[str] = Field(
        default_factory=list, description="finding ids a human has to deal with"
    )

    @property
    def shippable(self) -> bool:
        """Whether the code may go out as it is. ``INCONCLUSIVE`` never is."""
        return self.status in ("pass", "pass_with_conditions")

    @property
    def headline(self) -> str:
        return HEADLINES[self.status]


def decide(run: VerificationRun, findings: Sequence[Finding] = ()) -> Verdict:
    """Compute the verdict. Deterministic: same run and findings, same answer.

    Order matters. A confirmed blocking defect outranks a missing check: knowing that the
    project is broken is stronger than not knowing whether it is. ``HOLD`` therefore wins
    over ``INCONCLUSIVE``, while an inconclusive run never turns into ``PASS``.
    """
    if run.error:
        return Verdict(
            status="inconclusive",
            reasons=[VerdictReason(code="run-failed", detail=run.error[:400])],
        )
    if not run.checks:
        return Verdict(
            status="inconclusive",
            reasons=[
                VerdictReason(
                    code="nothing-to-verify",
                    detail="the plan contained no checks, so no evidence was collected",
                )
            ],
        )

    required = [check for check in run.checks if not check.optional]
    if not required:
        return Verdict(
            status="inconclusive",
            reasons=[
                VerdictReason(
                    code="nothing-to-verify",
                    detail="the policy declared no required checks; nothing could fail",
                )
            ],
        )

    failed = [check for check in required if check.status in ("failed", "timeout")]
    blocking = [finding for finding in findings if finding.blocking]
    if failed or blocking:
        reasons = [_failure_reason(check) for check in failed]
        reasons.extend(
            VerdictReason(code="blocking-finding", detail=f"{finding.id}: {finding.title}")
            for finding in blocking
            if finding.source != "check"  # the failed check already gave its own reason
        )
        return Verdict(
            status="hold",
            reasons=reasons,
            blocking_findings=[finding.id for finding in blocking],
        )

    skipped = [check for check in required if check.status in SKIP_CODES]
    if skipped:
        return Verdict(
            status="inconclusive",
            reasons=[_skip_reason(check) for check in skipped],
        )

    return _shippable(findings)


def _shippable(findings: Sequence[Finding]) -> Verdict:
    """Every required check passed; decide between PASS and PASS WITH CONDITIONS.

    A finding that is not confirmed is a condition, not a defect: the run cannot hold a
    release over a claim it did not check, but it may not stay silent about one either.
    """
    reasons: list[VerdictReason] = []
    for finding in findings:
        if finding.status in ("unverified", "ambiguous"):
            reasons.append(
                VerdictReason(
                    code="unresolved-finding",
                    detail=f"{finding.id} ({finding.status}): {finding.title}",
                )
            )
        elif finding.status == "confirmed":
            reasons.append(
                VerdictReason(
                    code="non-blocking-finding",
                    detail=f"{finding.id} ({finding.severity}): {finding.title}",
                )
            )
    if reasons:
        return Verdict(status="pass_with_conditions", reasons=reasons)
    return Verdict(
        status="pass",
        reasons=[
            VerdictReason(
                code="checks-passed",
                detail="every required check passed and nothing was left unresolved",
            )
        ],
    )


def _failure_reason(check: CheckRun) -> VerdictReason:
    what = "timed out" if check.status == "timeout" else "failed"
    return VerdictReason(
        code="check-timed-out" if check.status == "timeout" else "check-failed",
        detail=f"required check {check.check_id} {what}: {check.reason or check.status}",
    )


def _skip_reason(check: CheckRun) -> VerdictReason:
    status: CheckStatus = check.status
    return VerdictReason(
        code=SKIP_CODES[status],
        detail=f"required check {check.check_id} did not run: {check.reason or status}",
    )
