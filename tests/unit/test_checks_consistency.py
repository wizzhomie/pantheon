"""Domain rules: predicates, hints and crash isolation."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import BaseModel

from agentqa.checks import CheckContext, ConsistencyCheck, Rule


class Toy(BaseModel):
    weight: float = 0.0
    cooled: bool = False
    temperature: float | None = None


CtxFactory = Callable[..., CheckContext]


def test_passing_rule_produces_nothing(make_ctx: CtxFactory) -> None:
    rule = Rule(
        code="X_COOLED_NEEDS_RANGE",
        message="cooled cargo needs a temperature",
        predicate=lambda output: (not output.cooled) or output.temperature is not None,
    )
    assert ConsistencyCheck(rules=[rule])(Toy(), make_ctx("text")) == []


def test_failing_rule_reports_its_own_code(make_ctx: CtxFactory) -> None:
    rule = Rule(
        code="X_COOLED_NEEDS_RANGE",
        message="cooled cargo needs a temperature",
        predicate=lambda output: (not output.cooled) or output.temperature is not None,
        hint="ask the sender for the temperature window",
        field_path="temperature",
    )
    issues = ConsistencyCheck(rules=[rule])(Toy(cooled=True), make_ctx("text"))
    assert [issue.code for issue in issues] == ["X_COOLED_NEEDS_RANGE"]
    assert issues[0].hint == "ask the sender for the temperature window"
    assert issues[0].field_path == "temperature"


def test_a_crashing_rule_is_reported_not_raised(make_ctx: CtxFactory) -> None:
    def boom(output: BaseModel) -> bool:
        raise ZeroDivisionError("bad predicate")

    rule = Rule(code="X_BROKEN", message="never", predicate=boom)
    issues = ConsistencyCheck(rules=[rule])(Toy(), make_ctx("text"))
    assert [issue.code for issue in issues] == ["G8_RULE_FAILED"]
    assert issues[0].severity == "warning"
    assert issues[0].details["rule"] == "X_BROKEN"


def test_rule_codes_must_match_the_taxonomy() -> None:
    with pytest.raises(ValueError, match="must match"):
        Rule(code="bad code", message="x", predicate=lambda output: True)


def test_severity_is_configurable(make_ctx: CtxFactory) -> None:
    rule = Rule(
        code="X_SOFT",
        message="soft rule",
        predicate=lambda output: False,
        severity="info",
    )
    issues = ConsistencyCheck(rules=[rule])(Toy(), make_ctx("text"))
    assert issues[0].severity == "info"
