"""Required fields, including honest abstention.

A missing field is not always a defect: if the agent explicitly listed the path as
unresolved, the pipeline did the right thing and the *business* decision (ask the
customer, escalate) belongs downstream. Marking a field unresolved while also filling it
is a bookkeeping contradiction, and that is reported.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import get_path, is_present


@dataclass(frozen=True, slots=True)
class RequiredRule:
    """``path`` must be present, optionally only when ``when`` holds."""

    path: str
    when: Callable[[BaseModel], bool] | None = None
    hint: str | None = None

    def applies(self, output: BaseModel) -> bool:
        return self.when is None or bool(self.when(output))


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    """Mandatory fields, with optional awareness of the preset's ``unresolved`` list."""

    rules: Sequence[RequiredRule]
    unresolved_path: str | None = None
    severity: Severity = "error"
    id: str = "required"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        unresolved = read_unresolved(output, self.unresolved_path)
        issues: list[Issue] = []
        for rule in self.rules:
            if not rule.applies(output):
                continue
            present = is_present(get_path(output, rule.path))
            if present:
                if rule.path in unresolved:
                    issues.append(
                        Issue(
                            code="G1_UNRESOLVED_BUT_PRESENT",
                            severity="warning",
                            source="check",
                            field_path=rule.path,
                            message=f"{rule.path} is filled but also listed as unresolved",
                            hint="remove it from unresolved once its value is supported",
                        )
                    )
                continue
            if rule.path in unresolved:
                # Honest abstention: the path is known to be unknown. Whether that is
                # acceptable is a policy question, answered downstream, not here.
                continue
            issues.append(
                Issue(
                    code="G1_REQUIRED_MISSING",
                    severity=self.severity,
                    source="check",
                    field_path=rule.path,
                    message=f"required field {rule.path} is missing",
                    hint=rule.hint
                    or (
                        f"extract {rule.path} from the input or list it under "
                        f"{self.unresolved_path or 'unresolved'}"
                    ),
                )
            )
        return issues


def read_unresolved(output: BaseModel, unresolved_path: str | None) -> frozenset[str]:
    """The paths the agent admitted it could not extract."""
    if unresolved_path is None:
        return frozenset()
    raw: object = get_path(output, unresolved_path)
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str))
