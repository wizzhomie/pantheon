"""Contract invariants that the whole library relies on."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from qagent.contracts import Case, CheckResult, Feedback, GateResult, Issue


def test_case_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Case(case_id="case-1", payload="text", unexpected=1)  # type: ignore[call-arg]


def test_case_is_frozen() -> None:
    case = Case(case_id="case-1", payload="text")
    with pytest.raises(ValidationError):
        case.case_id = "case-2"  # type: ignore[misc]


def test_case_strips_whitespace() -> None:
    assert Case(case_id="  case-1  ", payload="text").case_id == "case-1"


@pytest.mark.parametrize("bad_id", ["", "has space", "a" * 65, "bad/id"])
def test_case_id_pattern(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Case(case_id=bad_id, payload="text")


def test_case_as_text_returns_string_payload() -> None:
    assert Case(case_id="case-1", payload="Добрый день").as_text() == "Добрый день"


def test_case_as_text_serialises_structured_payload_deterministically() -> None:
    case = Case(case_id="case-1", payload={"b": 1, "a": "ё"})
    assert case.as_text() == '{"a": "ё", "b": 1}'


def test_issue_blocking_depends_on_severity(issue: Callable[..., Issue]) -> None:
    assert issue(severity="info").blocking is False
    assert issue(severity="warning").blocking is False
    assert issue(severity="error").blocking is True
    assert issue(severity="critical").blocking is True


@pytest.mark.parametrize("bad_code", ["bad", "g1_lower", "1_START", "G1-REQUIRED"])
def test_issue_code_pattern(bad_code: str) -> None:
    with pytest.raises(ValidationError):
        Issue(code=bad_code, severity="error", source="check", message="problem")


def test_issue_requires_message(issue: Callable[..., Issue]) -> None:
    with pytest.raises(ValidationError):
        issue(message="")


def test_gate_result_rejects_a_contradictory_flag(issue: Callable[..., Issue]) -> None:
    # passed=True while an error is present would hide a defect.
    with pytest.raises(ValidationError):
        GateResult(passed=True, issues=[issue(severity="error")])
    # passed=False with nothing blocking makes the flag meaningless.
    with pytest.raises(ValidationError):
        GateResult(passed=False, issues=[])


def test_gate_result_passes_with_warnings_only(issue: Callable[..., Issue]) -> None:
    gate = GateResult(passed=True, issues=[issue(severity="warning", code="G3_OUT_OF_RANGE")])
    assert gate.passed is True
    assert gate.blocking_issues == []
    assert gate.highest_severity == "warning"
    assert gate.codes() == ["G3_OUT_OF_RANGE"]


def test_gate_result_aggregates_checks(issue: Callable[..., Issue]) -> None:
    results = [
        CheckResult(check_id="formats", passed=True),
        CheckResult(check_id="required", passed=False, issues=[issue()]),
    ]
    gate = GateResult(passed=False, results=results, issues=[issue()])
    assert [result.check_id for result in gate.results] == ["formats", "required"]
    assert gate.highest_severity == "error"
    assert gate.blocking_issues


def test_feedback_attempt_must_be_positive(issue: Callable[..., Issue]) -> None:
    with pytest.raises(ValidationError):
        Feedback(attempt=0)
    feedback = Feedback(attempt=1, issues=[issue()], instructions=["set origin from the quote"])
    assert feedback.instructions == ["set origin from the quote"]


def test_feedback_attention_limit() -> None:
    with pytest.raises(ValidationError):
        Feedback(attempt=1, instructions=[f"fix {index}" for index in range(11)])
