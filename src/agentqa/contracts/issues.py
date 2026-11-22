"""Problems found by checks, and the aggregate gate result."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import JsonValue, StrictModel

Severity = Literal["info", "warning", "error", "critical"]
IssueSource = Literal["input", "parse", "check", "judge", "budget"]

SEVERITY_RANK: dict[Severity, int] = {"info": 0, "warning": 1, "error": 2, "critical": 3}

CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,39}$"


class Issue(StrictModel):
    """One concrete defect.

    ``code`` is a stable taxonomy id (``G2_BAD_COUNTRY``, ``P1_BAD_JSON``,
    ``C_EVIDENCE_MISSING``): it is what reports aggregate, what tests assert on, and
    what users grep for, so changing a code is a breaking change.
    """

    code: str = Field(pattern=CODE_PATTERN, description="stable taxonomy id")
    severity: Severity
    source: IssueSource
    field_path: str | None = Field(
        default=None, description="dotted path inside the agent output, when applicable"
    )
    message: str = Field(min_length=1, max_length=500)
    hint: str | None = Field(
        default=None, max_length=300, description="what to do instead, for the retry prompt"
    )
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        """Whether this issue must stop the result from passing."""
        return self.severity in ("error", "critical")


class CheckResult(StrictModel):
    """Outcome of a single check.

    Recorded even when a check finds nothing: "which checks actually ran" is part of
    the audit trail, and a silently skipped check is indistinguishable from a passing
    one otherwise.
    """

    check_id: str = Field(min_length=1, max_length=64)
    passed: bool
    issues: list[Issue] = Field(default_factory=list)
    duration_ms: float = Field(default=0.0, ge=0)


class GateResult(StrictModel):
    """Aggregate verdict of all deterministic checks for one attempt."""

    passed: bool
    results: list[CheckResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> GateResult:
        if self.passed != (not self.blocking_issues):
            raise ValueError(
                "GateResult.passed must equal 'no blocking issues'; "
                "passing a flag that contradicts the issues hides a defect"
            )
        return self

    @property
    def blocking_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.blocking]

    @property
    def highest_severity(self) -> Severity | None:
        if not self.issues:
            return None
        return max((issue.severity for issue in self.issues), key=lambda s: SEVERITY_RANK[s])

    def codes(self) -> list[str]:
        """Distinct issue codes, sorted: the compact form used in reports."""
        return sorted({issue.code for issue in self.issues})
