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

BenchExpected = Literal["pass", "pass_with_conditions", "hold", "inconclusive"]
"""The answer the suite committed to, in the same vocabulary the verdict uses."""

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
    policy: str = Field(default=".qagent/policy.toml", max_length=200)


class CaseResult(StrictModel):
    """What the contour said about one case, next to what the suite said it should say."""

    id: str
    expected: BenchExpected
    actual: VerdictStatus
    matched: bool = Field(description="the verdict is exactly the expected one")
    reasons: list[str] = Field(default_factory=list, description="the verdict's own reasons")
    spent_seconds: float = Field(default=0.0, ge=0)

    @property
    def caught(self) -> bool:
        """A defect the contour held. `PASS WITH CONDITIONS` does not count: it ships."""
        return self.expected == "hold" and self.actual == "hold"

    @property
    def missed(self) -> bool:
        return self.expected == "hold" and self.actual != "hold"

    @property
    def false_alarm(self) -> bool:
        """A clean change the contour held — the cost of catching defects too eagerly."""
        return self.expected != "hold" and self.actual == "hold"

    @property
    def unexpected(self) -> bool:
        """Neither caught nor a false alarm: the verdict is not the one the suite named."""
        return not self.matched and not self.missed and not self.false_alarm


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
        return [case for case in self.cases if case.expected != "hold"]

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
    def passed(self) -> bool:
        return all(case.matched for case in self.cases)

    def summary(self) -> str:
        """One sentence fit for a terminal and for the head of the report."""
        return (
            f"{self.caught} of {len(self.defects)} planted defects held, "
            f"{self.false_alarms} of {len(self.controls)} clean changes held"
        )
