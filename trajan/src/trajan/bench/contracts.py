"""The holdout bench: a suite of changes whose verdict is known before the run.

A contour graded on the changes it was built for measures nothing: the cases are already in
its head. The bench exists to make the opposite cheap — a fixed set of planted defects and
clean controls, each with the answer written down in the case, so a regression shows up as
a number instead of an opinion.

The case file is data, in the project that owns the suite, and it is reviewed like code: a
case whose expected verdict no longer matches reality is a decision, not a test to adjust.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..contracts.base import StrictModel
from ..verify.verdict import VerdictStatus

BenchExpected = Literal["pass", "pass_with_conditions", "hold", "inconclusive", "finding"]
"""The answer the suite committed to.

Four values name a verdict; ``finding`` names the reviewer layer's question instead — "was the
defect found at all", not "how severe did the model call it". Grading a reviewer on the verdict
would measure its severity calibration rather than its eyes, and a confirmed finding is what a
human can act on either way.
"""

CASE_FILE = "case.toml"
CHANGE_FILE = "change.diff"
BASE_DIR = "base"
CASES_DIR = "cases"

BENCH_JSON = "bench.json"
BENCH_MD = "bench.md"


class BenchCase(StrictModel):
    """One change the suite knows the answer to."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$", description="case id, also the folder")
    expected: BenchExpected = Field(description="the verdict this change must produce")
    note: str = Field(default="", max_length=300, description="why this answer is right")
    policy: str = Field(default=".trajan/policy.toml", max_length=200)
    needs_reviewer: bool = Field(
        default=False,
        description="invisible to the project's own checks: only a reviewer can catch it",
    )


BenchLayer = Literal["checks", "reviewer", "all"]
"""Which half of the contour a run measures: the checks alone, the reviewer, or both."""


class CaseResult(StrictModel):
    """What the contour said about one case, next to what the suite said it should say."""

    id: str
    expected: BenchExpected
    actual: VerdictStatus
    matched: bool = Field(description="the case produced the answer the suite committed to")
    confirmed: int = Field(default=0, ge=0, description="reviewer findings the anchor accepted")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cached: bool = Field(default=False, description="the review was served from the cache")
    reasons: list[str] = Field(default_factory=list, description="the verdict's own reasons")
    spent_seconds: float = Field(default=0.0, ge=0)

    @property
    def caught(self) -> bool:
        """A defect the contour held. `PASS WITH CONDITIONS` does not count: it ships."""
        return self.expected == "hold" and self.actual == "hold"

    @property
    def detected(self) -> bool:
        """A reviewer-layer case: at least one confirmed finding, whatever the severity."""
        return self.expected == "finding" and self.confirmed > 0

    @property
    def missed(self) -> bool:
        """A planted defect, or a reviewer case, that the contour did not catch at all."""
        if self.expected == "finding":
            return not self.detected
        if self.expected == "hold":
            return not self.caught
        return False

    @property
    def false_alarm(self) -> bool:
        """A clean change the contour held — the cost of catching defects too eagerly."""
        return self.expected in ("pass", "pass_with_conditions") and self.actual == "hold"

    @property
    def unexpected(self) -> bool:
        """Neither caught nor a false alarm: the verdict is not the one the suite named."""
        return not self.matched and not self.missed and not self.false_alarm


def matches(expected: BenchExpected, actual: VerdictStatus, confirmed: int) -> bool:
    """Did the case produce the answer it committed to?"""
    if expected == "finding":
        return confirmed > 0
    return actual == expected


class BenchReport(StrictModel):
    """One run of the suite, and the numbers a reader is allowed to quote."""

    suite: str
    reviewer: str
    started_at: datetime
    cases: list[CaseResult] = Field(default_factory=list)

    @property
    def defects(self) -> list[CaseResult]:
        return [case for case in self.cases if case.expected == "hold"]

    @property
    def controls(self) -> list[CaseResult]:
        return [
            case for case in self.cases if case.expected in ("pass", "pass_with_conditions")
        ]

    @property
    def detected(self) -> int:
        return sum(1 for case in self.cases if case.detected)

    @property
    def caught(self) -> int:
        return sum(1 for case in self.cases if case.caught)

    @property
    def missed(self) -> int:
        return sum(1 for case in self.cases if case.missed)

    @property
    def false_alarms(self) -> int:
        return sum(1 for case in self.cases if case.false_alarm)

    @property
    def unexpected(self) -> int:
        return sum(1 for case in self.cases if case.unexpected)

    @property
    def prompt_tokens(self) -> int:
        return sum(case.prompt_tokens for case in self.cases)

    @property
    def completion_tokens(self) -> int:
        return sum(case.completion_tokens for case in self.cases)

    @property
    def passed(self) -> bool:
        return all(case.matched for case in self.cases)

    def summary(self) -> str:
        """One sentence fit for a terminal and for the head of the report."""
        parts: list[str] = []
        if self.defects:
            parts.append(f"{self.caught} of {len(self.defects)} planted defects held")
        reviewer_cases = [case for case in self.cases if case.expected == "finding"]
        if reviewer_cases:
            parts.append(f"{self.detected} of {len(reviewer_cases)} reviewer cases detected")
        if self.controls:
            parts.append(f"{self.false_alarms} of {len(self.controls)} clean changes held")
        return ", ".join(parts) or "no cases"
