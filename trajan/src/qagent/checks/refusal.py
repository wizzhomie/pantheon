"""Refusals: the model declined instead of doing the task.

A refusal that reaches the business layer looks like a successful call with odd text, so
it is reported as ``critical``: retrying usually does not help, and a human should look.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from ..contracts.issues import Issue, Severity
from .base import CheckContext, Tier
from .paths import get_path, iter_leaves

DEFAULT_MARKERS: tuple[str, ...] = (
    "i can't assist",
    "i cannot assist",
    "i can't help with that",
    "i'm unable to",
    "as an ai language model",
    "я не могу помочь",
    "не могу выполнить",
    "не могу предоставить",
)


@dataclass(frozen=True, slots=True)
class RefusalCheck:
    """Look for refusal phrasing in string fields."""

    markers: Sequence[str] = DEFAULT_MARKERS
    paths: Sequence[str] = ()
    severity: Severity = "critical"
    id: str = "refusal"
    tier: Tier = "fast"

    def __call__(self, output: BaseModel, ctx: CheckContext) -> list[Issue]:
        targets = (
            list(self.paths)
            if self.paths
            else [path for path, value in iter_leaves(output) if isinstance(value, str)]
        )
        lowered = tuple(marker.casefold() for marker in self.markers)
        issues: list[Issue] = []
        for path in targets:
            value = get_path(output, path)
            if not isinstance(value, str):
                continue
            text = value.casefold()
            if any(marker in text for marker in lowered):
                issues.append(
                    Issue(
                        code="G6_REFUSAL",
                        severity=self.severity,
                        source="check",
                        field_path=path,
                        message=f"the agent refused instead of returning data ({path})",
                        hint="route to a human or rephrase the task; retrying rarely helps",
                        details={"path": path},
                    )
                )
        return issues
