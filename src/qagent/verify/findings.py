"""Findings: the one place where "something is wrong" becomes a structure.

A finding is a claim with a place and a state, not a sentence in a report. The state is what
keeps a reviewer's guess from silently becoming a defect: only ``confirmed`` findings may
block, and confirmation is never the model's word — the deterministic checks are what confirm
a failure, and a reviewer finding starts life as ``unverified`` until an anchor agrees with it.

The four statuses are the whole difference between "we know this is broken" and "we suspect
it":

``confirmed``
    evidence exists: a check failed, or an anchor proved the claim about the code.
``unverified``
    claimed, but nothing has checked the claim yet. Visible, never blocking.
``ambiguous``
    the evidence points both ways; a human has to decide. Never blocking, always a condition.
``false-positive``
    the claim was checked and did not hold up. Kept in the record: a rejected claim is also
    information, and deleting it would hide that the reviewer was wrong.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..contracts.base import StrictModel
from .contracts import CheckRun, VerificationRun

FINDING_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,39}$"

FindingKind = Literal["check-failure", "defect", "risk", "test-gap"]
"""What sort of problem the finding describes, not how sure we are of it."""

FindingStatus = Literal["confirmed", "unverified", "ambiguous", "false-positive"]
"""How much is actually known; only ``confirmed`` is evidence."""

FindingSeverity = Literal["blocking", "major", "minor", "info"]
"""What it would cost to ship with this finding still open."""

FindingSource = Literal["check", "reviewer", "human"]
"""Where the claim came from. A source is not a reason to believe it."""


class Location(StrictModel):
    """Where the claim points: a file, a line and the line's text, for re-checking.

    The three belong together. An anchor without a quote is a line number that will drift;
    a quote without a line is a text search; a line without a file is a guess.
    """

    file: str = Field(min_length=1, max_length=400, description="path relative to the project")
    line: int = Field(ge=1, description="1-based line number the claim is about")
    quote: str = Field(
        min_length=1, max_length=300, description="the line's text, verbatim, stripped"
    )


class Finding(StrictModel):
    """One claimed problem, with its place, its evidence and how much is known about it."""

    id: str = Field(pattern=FINDING_ID_PATTERN, description="stable id, referenced by reports")
    title: str = Field(min_length=1, max_length=200)
    kind: FindingKind
    status: FindingStatus
    severity: FindingSeverity
    source: FindingSource
    detail: str = Field(default="", max_length=2000)
    location: Location | None = Field(
        default=None, description="unset for a failed check: a command has no line"
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="check ids and artifact paths that support the finding",
    )

    @property
    def blocking(self) -> bool:
        """Only a *confirmed* blocking claim may stop a release; a suspicion may not."""
        return self.status == "confirmed" and self.severity == "blocking"


def findings_from_run(run: VerificationRun) -> list[Finding]:
    """Turn failed checks into findings, and leave gaps as gaps.

    A check that did not run is not a finding about the project: it says nothing was learned.
    Turning it into a "gap" finding would make the verdict argue with itself, since the
    verdict already sees the check's status. So only failures and timeouts become findings.
    """
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
