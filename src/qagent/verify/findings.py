"""Turning check results into findings — the deterministic half of the finding model.

The contract itself lives in :mod:`qagent.contracts.findings`, because the reviewer proposes
findings too and must not import the verification engine to do it. What stays here is the one
piece that needs the engine: a failed check becomes a confirmed finding, because the exit code
is evidence, not a claim.

A check that did not run is deliberately *not* a finding: it says nothing about the project,
and the verdict already sees its status. Turning a skip into "something is wrong" would make
the report argue with itself.
"""

from __future__ import annotations

from ..contracts.findings import (
    FINDING_ID_PATTERN,
    Finding,
    FindingKind,
    FindingSeverity,
    FindingSource,
    FindingStatus,
    Location,
)
from .contracts import CheckRun, VerificationRun

__all__ = [
    "FINDING_ID_PATTERN",
    "Finding",
    "FindingKind",
    "FindingSeverity",
    "FindingSource",
    "FindingStatus",
    "Location",
    "findings_from_run",
]


def findings_from_run(run: VerificationRun) -> list[Finding]:
    """Turn failed checks into findings; a check that did not run stays a gap."""
    findings: list[Finding] = []
    for check in run.checks:
        if check.status not in ("failed", "timeout"):
            continue
        findings.append(_from_check(check))
    return findings


def _from_check(check: CheckRun) -> Finding:
    severity: FindingSeverity = "major" if check.optional else "blocking"
    evidence = [check.check_id]
    evidence.extend(path for path in (check.stdout_path, check.stderr_path) if path is not None)
    what = "timed out" if check.status == "timeout" else "failed"
    return Finding(
        id=f"check-{check.check_id}",
        title=f"{check.name or check.check_id} {what}",
        kind="check-failure",
        status="confirmed",
        severity=severity,
        source="check",
        detail=check.reason or "",
        evidence=evidence,
    )
