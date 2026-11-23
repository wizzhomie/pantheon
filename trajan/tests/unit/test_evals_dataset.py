"""The dataset: JSONL in, cases plus a hash that guards comparisons."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from support import eval_case

from trajan import Dataset, EvalCase, Trap


def test_jsonl_round_trip_keeps_cases_and_hash() -> None:
    dataset = Dataset(
        name="intake",
        cases=[
            eval_case("c1"),
            eval_case("c2", category="adversarial", human_verdict=False, notes="trap bait"),
        ],
    )

    restored = Dataset.from_jsonl(dataset.to_jsonl(), name="intake")

    assert restored.cases == dataset.cases
    assert restored.digest() == dataset.digest()


def test_the_hash_ignores_notes_and_hand_labels() -> None:
    plain = Dataset(cases=[eval_case("c1")])
    annotated = Dataset(
        cases=[eval_case("c1", notes="copy from the packing list", human_verdict=True)]
    )

    assert plain.digest() == annotated.digest()


def test_the_hash_changes_with_the_labelled_content() -> None:
    base = Dataset(cases=[eval_case("c1")])
    other_expected = Dataset(cases=[eval_case("c1", expected={"qty": 3})])
    other_payload = Dataset(cases=[eval_case("c1", payload="three boxes")])
    other_tags = Dataset(
        cases=[eval_case("c1", traps=[Trap(path="qty", forbidden=[0], reason="invented")])]
    )

    digests = {
        base.digest(),
        other_expected.digest(),
        other_payload.digest(),
        other_tags.digest(),
    }
    assert len(digests) == 4


def test_the_hash_does_not_depend_on_case_order() -> None:
    forward = Dataset(cases=[eval_case("c1"), eval_case("c2")])
    backward = Dataset(cases=[eval_case("c2"), eval_case("c1")])

    assert forward.digest() == backward.digest()


def test_duplicate_case_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate case ids"):
        Dataset(cases=[eval_case("c1"), eval_case("c1")])


def test_an_empty_dataset_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Dataset(cases=[])


def test_an_empty_file_is_rejected_with_its_path() -> None:
    with pytest.raises(ValueError, match=r"dataset.jsonl: the dataset is empty"):
        Dataset.from_jsonl("\n\n# just a comment\n", source="dataset.jsonl")


def test_a_broken_line_names_its_line_number() -> None:
    text = '{"case_id": "c1", "category": "happy_path", "payload": "x", "expected": {}}\n{broken}\n'

    with pytest.raises(ValueError, match=r"dataset.jsonl:2"):
        Dataset.from_jsonl(text, source="dataset.jsonl")


def test_save_and_load_through_a_file(tmp_path: object) -> None:
    from pathlib import Path

    dataset = Dataset(name="intake", cases=[eval_case("c1"), eval_case("c2")])
    target: Path = Path(str(tmp_path)) / "nested" / "dataset.jsonl"

    dataset.save(target)
    restored = Dataset.load(target)

    assert restored.cases == dataset.cases
    assert restored.name == "dataset"
    assert restored.digest() == dataset.digest()


def test_jsonl_tolerates_extra_whitespace() -> None:
    case = EvalCase(
        case_id="c1", category="happy_path", payload="x", expected={"qty": 1}
    )
    text = f"\n   {case.model_dump_json()}   \n"

    assert Dataset.from_jsonl(text).cases == [case]
