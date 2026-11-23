"""Traceability: every approved promise, and what the run actually knows about it.

A verdict answers "may this ship". Traceability answers the harder question a reviewer asks
next: *which promise does this evidence belong to?* It is computed, not written: for each
approved requirement or invariant, the checks that cover it, the findings that cite it, and
one of four states.

The states are deliberately blunt, because a friendly state is a silent gap:

``failed``
    a linked check failed, or a confirmed finding cites this promise.
``not-run``
    the checks exist but none of them ran in this run (skipped, error, missing tool).
``not-selected``
    the checks exist and none was selected for this change: the plan decided they are not
    this change's business. Not a gap, and not coverage either.
``unchecked``
    nothing checks this promise at all.
``covered``
    its checks ran and passed, and nothing cited it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..context.knowledge import ProjectContext
from ..contracts.base import StrictModel
from ..contracts.findings import Finding
from .contracts import CheckRun, VerificationRun

TraceStatus = Literal["covered", "failed", "not-run", "not-selected", "unchecked"]

FAILED_CHECK_STATUSES = ("failed", "timeout")
MISSING_CHECK_STATUSES = ("error", "skipped", "not_applicable")


class TraceRow(StrictModel):
    """One approved promise and what the run can say about it."""

    id: str
    title: str
    status: TraceStatus
    checks: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)


def build_trace(
    context: ProjectContext, run: VerificationRun, findings: list[Finding]
) -> list[TraceRow]:
    """One row per approved requirement and invariant, in declaration order."""
    by_check = {check.check_id: check for check in run.checks}
    rows: list[TraceRow] = []
    for item in [*context.requirements, *context.invariants]:
        if item.status != "approved":
            continue
        checks = [by_check[check_id] for check_id in item.checks if check_id in by_check]
        cited = [finding for finding in findings if finding.requirement == item.id]
        rows.append(
            TraceRow(
                id=item.id,
                title=item.title,
                status=_status(checks, cited),
                checks=item.checks,
                findings=[finding.id for finding in cited],
            )
        )
    return rows


def unlinked_findings(findings: list[Finding]) -> list[Finding]:
    """Confirmed findings that cite no approved promise: real problems, no trace to a why."""
    return [
        finding
        for finding in findings
        if finding.status == "confirmed" and finding.requirement is None
    ]


def _status(checks: list[CheckRun], cited: list[Finding]) -> TraceStatus:
    if any(finding.status == "confirmed" for finding in cited):
        return "failed"
    if any(check.status in FAILED_CHECK_STATUSES for check in checks):
        return "failed"
    if not checks:
        return "unchecked"
    if all(not check.selected for check in checks):
        return "not-selected"
    if all(check.status in MISSING_CHECK_STATUSES for check in checks):
        return "not-run"
    return "covered"


def summarise(rows: list[TraceRow]) -> str:
    """One line for a CLI: how many promises, and in which state."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    parts = [f"{len(rows)} approved"]
    parts.extend(f"{counts[status]} {status}" for status in sorted(counts))
    return ", ".join(parts)
