"""Shared fixtures: a two-criteria rubric and an issue factory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from agentqa.checks import CheckContext
from agentqa.contracts import Case, Criterion, Issue, JsonValue, Rubric

FIXED_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def issue() -> Callable[..., Issue]:
    """Build an issue with sensible defaults; override only what the test is about."""

    def make(
        code: str = "G1_REQUIRED_MISSING",
        severity: str = "error",
        message: str = "problem",
        **kwargs: object,
    ) -> Issue:
        return Issue(
            code=code,
            severity=severity,  # type: ignore[arg-type]
            source="check",
            message=message,
            **kwargs,  # type: ignore[arg-type]
        )

    return make


@pytest.fixture
def rubric() -> Rubric:
    """Grounding-heavy rubric: a 5/5/1 mix lands exactly on 0.75."""

    return Rubric(
        criteria=[
            Criterion(
                id="grounding",
                title="Grounding",
                description="Every value is supported by the input; nothing was added.",
                weight=0.7,
            ),
            Criterion(
                id="completeness",
                title="Completeness",
                description="Facts present in the input were all extracted.",
                weight=0.3,
            ),
        ],
        pass_score=0.75,
    )


@pytest.fixture
def make_ctx() -> Callable[..., CheckContext]:
    """Build a deterministic check context: the clock is frozen, not read."""

    def factory(payload: JsonValue, *, now: datetime | None = None) -> CheckContext:
        return CheckContext(
            case=Case(case_id="case-1", payload=payload),
            now=now or FIXED_NOW,
        )

    return factory
