"""Run checks cheap-first and aggregate them into one gate result.

Two guarantees matter here:

* **cost order** — ``fast`` checks before ``deep`` ones, regardless of the order the preset
  declared them in, so a mistake in the preset costs milliseconds, not a network round
  trip;
* **isolation** — an exception inside one check becomes an issue, never a lost run, because
  a broken validator must not be able to drop a customer request silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter

from pydantic import BaseModel

from ..contracts.issues import CheckResult, GateResult, Issue
from .base import Check, CheckContext

TIER_ORDER: dict[str, int] = {"fast": 0, "deep": 1}


def run_checks(checks: Sequence[Check], output: BaseModel, ctx: CheckContext) -> GateResult:
    """Execute ``checks`` against ``output`` and return the aggregate gate."""
    ordered = sorted(checks, key=lambda check: (TIER_ORDER[check.tier], check.id))
    results: list[CheckResult] = []
    issues: list[Issue] = []
    for check in ordered:
        started = perf_counter()
        found = _safe_call(check, output, ctx)
        duration_ms = round((perf_counter() - started) * 1000, 3)
        results.append(
            CheckResult(
                check_id=check.id,
                passed=not any(issue.blocking for issue in found),
                issues=found,
                duration_ms=duration_ms,
            )
        )
        issues.extend(found)
    return GateResult(
        passed=not any(issue.blocking for issue in issues),
        results=results,
        issues=issues,
    )


def _safe_call(check: Check, output: BaseModel, ctx: CheckContext) -> list[Issue]:
    try:
        return list(check(output, ctx))
    except Exception as exc:  # isolation is the point of this function
        return [
            Issue(
                code="G0_CHECK_CRASHED",
                severity="error",
                source="check",
                message=f"check {check.id} raised {type(exc).__name__}",
                hint="fix the check; it was skipped for this attempt",
                details={"check_id": check.id, "error": str(exc)[:200]},
            )
        ]
