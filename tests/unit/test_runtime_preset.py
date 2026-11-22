"""The preset: a plain container with the few checks that are cheap to make."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from support import QtyCheck, ToyOutput

from qagent import Criterion, Preset, Rubric


class OtherOutput(BaseModel):
    value: int = 0


class SecondCheck:
    id = "second"
    tier = "deep"

    def __call__(self, output: BaseModel, ctx: object) -> list[object]:
        return []


def test_a_preset_normalises_its_checks_to_a_tuple(rubric: Rubric) -> None:
    preset = Preset(
        name="toy",
        output_model=ToyOutput,
        rubric=rubric,
        checks=[QtyCheck(), SecondCheck()],
    )

    assert isinstance(preset.checks, tuple)
    assert preset.check_ids() == ["qty", "second"]
    assert "properties" in preset.output_schema


def test_a_preset_needs_a_name(rubric: Rubric) -> None:
    with pytest.raises(ValueError, match="needs a non-empty name"):
        Preset(name="  ", output_model=ToyOutput, rubric=rubric)


def test_the_output_model_must_be_pydantic(rubric: Rubric) -> None:
    with pytest.raises(TypeError, match="BaseModel"):
        Preset(name="toy", output_model=dict, rubric=rubric)  # type: ignore[arg-type]


def test_duplicate_check_ids_are_rejected(rubric: Rubric) -> None:
    with pytest.raises(ValueError, match="duplicate check ids"):
        Preset(
            name="toy",
            output_model=ToyOutput,
            rubric=rubric,
            checks=[QtyCheck(), QtyCheck()],
        )


def test_the_fingerprint_is_stable_for_equivalent_presets(rubric: Rubric) -> None:
    first = Preset(name="toy", output_model=ToyOutput, rubric=rubric, checks=[QtyCheck()])
    second = Preset(name="toy", output_model=ToyOutput, rubric=rubric, checks=[QtyCheck()])

    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64


def test_the_fingerprint_changes_when_the_rubric_changes(rubric: Rubric) -> None:
    changed = Rubric(
        criteria=[
            Criterion(
                id="grounding",
                title="Grounding",
                description="Every value is supported by the input.",
                weight=0.5,
            ),
            Criterion(
                id="completeness",
                title="Completeness",
                description="Every fact in the input was extracted.",
                weight=0.5,
            ),
        ],
        pass_score=rubric.pass_score,
    )
    base = Preset(name="toy", output_model=ToyOutput, rubric=rubric)
    other = Preset(name="toy", output_model=ToyOutput, rubric=changed)

    assert base.fingerprint() != other.fingerprint()


def test_the_fingerprint_changes_when_the_prompt_or_schema_changes(rubric: Rubric) -> None:
    base = Preset(name="toy", output_model=ToyOutput, rubric=rubric)

    reworded = Preset(name="toy", output_model=ToyOutput, rubric=rubric, agent_prompt="new")
    other_schema = Preset(name="toy", output_model=OtherOutput, rubric=rubric)
    other_check = Preset(name="toy", output_model=ToyOutput, rubric=rubric, checks=[SecondCheck()])

    fingerprints = {base.fingerprint(), reworded.fingerprint(), other_schema.fingerprint(),
                    other_check.fingerprint()}
    assert len(fingerprints) == 4


def test_retry_and_input_hooks_are_kept_as_given(rubric: Rubric) -> None:
    def build_retry(case: object, feedback: object) -> object:
        return feedback

    def validate_input(case: object) -> list[object]:
        return []

    preset = Preset(
        name="toy",
        output_model=ToyOutput,
        rubric=rubric,
        build_retry=build_retry,
        validate_input=validate_input,
    )

    assert preset.build_retry is build_retry
    assert preset.validate_input is validate_input
