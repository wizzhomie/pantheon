"""Domain rules as predicates.

The core cannot know that a cooled cargo needs a temperature range, that a 40-foot
container does not weigh two tonnes, or that a budget is not an insured value. Those are
one predicate each, carrying their own code and hint, so they show up in reports and in
the retry feedback like any built-in check.

A crashing predicate is reported, not raised: a bug in one rule must not lose the run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from ..contracts.issues import CODE_PATTERN, Issue, Severity
from .base import CheckContext, Tier

Predicate = Callable[[BaseModel], bool]
"""Return True when the rule holds."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One domain invariant."""

    code: str
    message: str
    predicate: Predicate
    severity: Severity = "error"
    hint: str | None = None
    field_path: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(CODE_PATTERN, self.code):
            raise ValueError(f"rule code {self.code!r} must match {CODE_PATTERN}")


@dataclass(frozen=True, slots=True)
class ConsistencyCheck:
    """Run the preset's invariants over one output."""

    rules: Sequence[Rule]
    id: str = "consistency"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        issues: list[Issue] = []
        for rule in self.rules:
            try:
                holds = rule.predicate(output)
            except Exception as exc:  # a broken rule must not lose the run
                issues.append(
                    Issue(
                        code="G8_RULE_FAILED",
                        severity="warning",
                        source="check",
                        field_path=rule.field_path,
                        message=f"rule {rule.code} raised {type(exc).__name__}",
                        hint="fix the predicate; it was skipped for this attempt",
                        details={"rule": rule.code},
                    )
                )
                continue
            if not holds:
                issues.append(
                    Issue(
                        code=rule.code,
                        severity=rule.severity,
                        source="check",
                        field_path=rule.field_path,
                        message=rule.message,
                        hint=rule.hint,
                    )
                )
        return issues
