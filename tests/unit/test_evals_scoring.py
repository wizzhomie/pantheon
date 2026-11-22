"""Scoring: comparators, weights, traps and the ground-truth meaning of "passed"."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from support import ToyOutput, eval_case

from qagent import FieldSpec, ScoringSpec, Trap
from qagent.evals.scoring import score_case


def spec(**overrides: object) -> ScoringSpec:
    fields = [
        FieldSpec(path="qty", comparator="numeric"),
        FieldSpec(path="name"),
    ]
    return ScoringSpec(fields=fields, **overrides)  # type: ignore[arg-type]


def output(qty: int = 2, name: str = "two boxes") -> ToyOutput:
    return ToyOutput(qty=qty, name=name)


def test_a_matching_output_passes_with_full_accuracy() -> None:
    score = score_case(output(), eval_case(), spec())

    assert score.passed is True
    assert score.field_accuracy == 1.0
    assert score.missing == [] and score.wrong == [] and score.hallucinated == []


def test_a_normalised_string_ignores_case_and_whitespace() -> None:
    score = score_case(output(name="  TWO   Boxes "), eval_case(), spec())

    assert score.field_accuracy == 1.0


def test_a_wrong_value_is_reported_by_path() -> None:
    score = score_case(output(qty=3), eval_case(), spec())

    assert score.passed is False
    assert score.wrong == ["qty"]
    assert score.missing == []
    assert score.field_accuracy == 0.5


def test_a_missing_value_is_reported_separately() -> None:
    three_fields = ScoringSpec(
        fields=[
            FieldSpec(path="qty", comparator="numeric"),
            FieldSpec(path="name"),
            FieldSpec(path="note"),
        ]
    )
    case = eval_case(expected={"qty": 2, "name": "two boxes", "note": "x"})

    score = score_case(output(), case, three_fields)

    assert score.missing == ["note"]
    assert score.field_accuracy == pytest.approx(2 / 3, abs=1e-4)


def test_a_field_the_golden_case_does_not_have_is_a_wrong_field() -> None:
    case = eval_case(expected={"qty": 2})
    score = score_case(output(), case, spec())

    assert score.wrong == ["name"]
    assert score.passed is False


def test_weights_are_normalised() -> None:
    weighted = ScoringSpec(
        fields=[
            FieldSpec(path="qty", comparator="numeric", weight=3),
            FieldSpec(path="name", weight=1),
        ]
    )
    score = score_case(output(name="wrong"), eval_case(), weighted)

    assert score.field_accuracy == 0.75


def test_the_minimum_accuracy_is_a_hard_floor() -> None:
    weighted = ScoringSpec(
        fields=[
            FieldSpec(path="qty", comparator="numeric", weight=3),
            FieldSpec(path="name", weight=1),
        ],
        min_field_accuracy=0.8,
    )
    fact_of_life = ScoringSpec(
        fields=[
            FieldSpec(path="qty", comparator="numeric", weight=3),
            FieldSpec(path="name", weight=1),
        ],
        min_field_accuracy=0.75,
    )

    assert score_case(output(name="wrong"), eval_case(), weighted).passed is False
    assert score_case(output(name="wrong"), eval_case(), fact_of_life).passed is True


def test_numeric_tolerance_is_absolute() -> None:
    tolerant = ScoringSpec(fields=[FieldSpec(path="qty", comparator="numeric", tolerance=0.5)])
    strict = ScoringSpec(fields=[FieldSpec(path="qty", comparator="numeric", tolerance=0.1)])

    assert score_case(output(qty=2), eval_case(expected={"qty": 2.4}), tolerant).passed is True
    assert score_case(output(qty=2), eval_case(expected={"qty": 2.4}), strict).passed is False


def test_a_nullable_numeric_field_matches_null_with_null() -> None:
    """Absence can be the correct answer; a numeric comparator must not call it a miss."""

    class Maybe(BaseModel):
        qty: int | None

    nullable = ScoringSpec(fields=[FieldSpec(path="qty", comparator="numeric")])

    assert score_case(Maybe(qty=None), eval_case(expected={"qty": None}), nullable).passed is True
    assert score_case(Maybe(qty=1), eval_case(expected={"qty": None}), nullable).passed is False


def test_set_comparison_ignores_order() -> None:
    case = eval_case(expected={"tags": ["a", "b"]})
    ordered = ScoringSpec(fields=[FieldSpec(path="tags", comparator="set")])

    class Tagged(BaseModel):
        tags: list[str]

    score = score_case(Tagged(tags=["b", "a"]), case, ordered)

    assert score.passed is True


def test_fuzzy_comparison_tolerates_small_noise_above_the_floor() -> None:
    case = eval_case(expected={"name": "Shipping to Kyiv"})
    fuzzy = ScoringSpec(fields=[FieldSpec(path="name", comparator="fuzzy", tolerance=0.85)])

    score = score_case(output(name="Shipping to Kiev"), case, fuzzy)

    assert score.passed is True


def test_exact_comparison_is_strict_about_case() -> None:
    exact = ScoringSpec(fields=[FieldSpec(path="name", comparator="exact")])

    assert score_case(output(name="two boxes"), eval_case(), exact).passed is True
    assert score_case(output(name="Two Boxes"), eval_case(), exact).passed is False


def test_a_trap_hit_fails_the_case_even_with_perfect_fields() -> None:
    case = eval_case(traps=[Trap(path="qty", forbidden=[0], reason="invented quantity")])
    score = score_case(output(qty=0), case, spec())

    assert score.hallucinated == ["qty"]
    assert score.passed is False
    assert score.field_accuracy < 1.0  # qty=0 is also wrong, and the trap adds the verdict


def test_a_trap_hit_inside_a_list_is_found() -> None:
    class Listing(BaseModel):
        names: list[str]

    case = eval_case(
        expected={"names": ["bolts"]},
        traps=[Trap(path="names", forbidden=["plutonium"], reason="invented cargo")],
    )
    listing = ScoringSpec(fields=[FieldSpec(path="names", comparator="set")])

    score = score_case(Listing(names=["bolts", "plutonium"]), case, listing)

    assert score.hallucinated == ["names"]
    assert score.passed is False


def test_a_trap_can_forbid_null() -> None:
    case = eval_case(traps=[Trap(path="note", forbidden=[None], reason="must never be empty")])

    class WithNull(BaseModel):
        qty: int = 0
        note: str | None = None

    score = score_case(WithNull(qty=2), case, ScoringSpec(fields=[FieldSpec(path="qty")]))

    assert score.hallucinated == ["note"]
    assert score.passed is False


def test_no_output_scores_zero_and_fails() -> None:
    score = score_case(None, eval_case(), spec())

    assert score.field_accuracy == 0.0
    assert score.missing == ["qty", "name"]
    assert score.passed is False
