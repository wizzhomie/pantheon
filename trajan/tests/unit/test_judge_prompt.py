"""Prompt assembly: rubric as data, instructions as preset context."""

from __future__ import annotations

from trajan.contracts import Case, Criterion, Rubric
from trajan.judge import build_messages, render_rubric
from trajan.judge.prompt import OUTPUT_LIMIT


def rubric() -> Rubric:
    return Rubric(
        criteria=[
            Criterion(
                id="grounding",
                title="Grounding",
                description="Every value is supported by the input.",
                weight=0.7,
            ),
            Criterion(
                id="completeness",
                title="Completeness",
                description="All stated facts were extracted.",
                weight=0.3,
            ),
        ],
        pass_score=0.75,
    )


def case(payload: object = "cargo: 4 pallets") -> Case:
    return Case(case_id="case-1", payload=payload)  # type: ignore[arg-type]


def test_rubric_renders_ids_weights_and_threshold() -> None:
    rendered = render_rubric(rubric())
    assert "grounding (weight 0.7, Grounding)" in rendered
    assert "completeness (weight 0.3, Completeness)" in rendered
    assert "Pass threshold: 0.75" in rendered


def test_messages_carry_rules_rubric_and_instructions() -> None:
    messages = build_messages(
        case(), '{"a": 1}', rubric(), instructions="The input is a freight request."
    )
    assert [message.role for message in messages] == ["system", "user"]
    system = messages[0].content
    assert "Plausible is not evidence." in system
    assert "The input is a freight request." in system
    assert "grounding (weight 0.7" in system
    assert "Never return a pass/fail verdict" in system
    user = messages[1].content
    assert "cargo: 4 pallets" in user
    assert '{"a": 1}' in user


def test_empty_instructions_are_omitted() -> None:
    system = build_messages(case(), "{}", rubric())[0].content
    assert "Preset instructions" not in system


def test_long_input_is_clipped_with_a_visible_marker() -> None:
    messages = build_messages(case("x" * 500), "{}", rubric(), max_input_chars=100)
    assert "[... input truncated at 100 characters ...]" in messages[1].content


def test_long_output_is_clipped_too() -> None:
    messages = build_messages(case(), "y" * (OUTPUT_LIMIT + 10), rubric())
    assert "[... output truncated" in messages[1].content


def test_the_answer_schema_reaches_the_model() -> None:
    schema = {"type": "object", "properties": {"criterion_scores": {"type": "object"}}}
    system = build_messages(case(), "{}", rubric(), schema=schema)[0].content

    assert "Answer schema (JSON Schema" in system
    assert '"criterion_scores"' in system
