"""Correction: the retry payload must be small, specific and never dishonest."""

from __future__ import annotations

from agentqa import Feedback, Issue, JudgeVerdict, Rubric
from agentqa.runtime.correction import (
    DEFAULT_IMPERATIVES,
    MAX_FEEDBACK_ISSUES,
    MAX_PREVIOUS_OUTPUT_CHARS,
    build_feedback,
    distill_instructions,
    render_feedback,
    select_issues,
)


def issue(code: str = "G1_REQUIRED_MISSING", severity: str = "error", **kwargs: object) -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        source="check",
        message="problem",
        **kwargs,  # type: ignore[arg-type]
    )


def test_selection_keeps_the_most_severe_issues_and_caps_the_list() -> None:
    issues = [issue(code=f"G1_MISSING_{index:02d}", severity="warning") for index in range(20)]
    issues.append(issue(code="G5_PII_LEAK", severity="critical"))
    issues.append(issue(code="G2_BAD_DATE", severity="error"))

    selected = select_issues(issues)

    assert len(selected) == MAX_FEEDBACK_ISSUES
    assert selected[0].code == "G5_PII_LEAK"
    assert selected[1].code == "G2_BAD_DATE"


def test_instructions_put_the_judge_first_then_the_non_negotiables(rubric: Rubric) -> None:
    verdict = JudgeVerdict.from_rubric(
        rubric,
        {"grounding": 2, "completeness": 4},
        fix_instructions=["Copy the weight from the packing list"],
    )
    issues = [
        issue(hint="Fix the country code"),
        issue(code="G2_BAD_DATE", hint="Fix the country code"),  # duplicate hint
    ]

    instructions = distill_instructions(issues, verdict)

    assert instructions[0] == "Copy the weight from the packing list"
    assert DEFAULT_IMPERATIVES[0] in instructions
    assert instructions.count("Fix the country code") == 1
    assert len(instructions) <= 10


def test_instructions_are_capped_and_trimmed() -> None:
    issues = [
        issue(code=f"G1_MISSING_{index:02d}", hint=f"Fix field {index}")
        for index in range(12)
    ]

    instructions = distill_instructions(issues, None)

    assert len(instructions) == 10
    assert len(set(instructions)) == 10


def test_feedback_carries_the_previous_answer_truncated() -> None:
    huge = "x" * (MAX_PREVIOUS_OUTPUT_CHARS + 500)
    feedback = build_feedback(
        attempt=1, issues=[issue()], verdict=None, previous_output=huge
    )

    assert feedback.previous_output is not None
    assert len(feedback.previous_output) == MAX_PREVIOUS_OUTPUT_CHARS


def test_the_policy_can_drop_the_previous_answer() -> None:
    feedback = build_feedback(
        attempt=2,
        issues=[issue()],
        verdict=None,
        previous_output='{"a": 1}',
        include_previous_output=False,
    )

    assert feedback.previous_output is None
    assert feedback.attempt == 2


def test_an_empty_previous_answer_is_not_carried() -> None:
    feedback = build_feedback(attempt=1, issues=[], verdict=None, previous_output="")

    assert feedback.previous_output is None


def test_rendered_feedback_reads_like_a_brief(rubric: Rubric) -> None:
    verdict = JudgeVerdict.from_rubric(
        rubric,
        {"grounding": 1, "completeness": 1},
        fix_instructions=["Copy the weight from the packing list"],
    )
    feedback = Feedback(
        attempt=2,
        issues=[issue(field_path="qty", hint="extract a positive quantity")],
        judge=verdict,
        instructions=["Copy the weight from the packing list"],
        previous_output='{"qty": 0}',
    )

    text = render_feedback(feedback)

    assert "Attempt 2 did not pass QA" in text
    assert "Judge verdict: failed, score 0.00" in text
    assert "at qty" in text
    assert "fix: extract a positive quantity" in text
    assert "Copy the weight from the packing list" in text
    assert '"qty": 0' in text


def test_rendered_feedback_without_a_judge_has_no_verdict_line() -> None:
    feedback = build_feedback(
        attempt=1, issues=[issue()], verdict=None, previous_output=None
    )

    text = render_feedback(feedback)

    assert "Judge verdict" not in text
    assert "Previous answer" not in text
