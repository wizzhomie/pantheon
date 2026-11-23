"""Parsing: tolerant extraction, schema errors and truncation."""

from __future__ import annotations

from pydantic import BaseModel

from qagent.parsing import ParseOutcome, parse_attempt


class Toy(BaseModel):
    name: str
    count: int = 0


def test_dict_attempt_is_validated() -> None:
    outcome = parse_attempt({"name": "a", "count": 2}, Toy)
    assert isinstance(outcome, ParseOutcome)
    assert outcome.ok is True
    assert outcome.output is not None
    assert outcome.output.count == 2


def test_model_instance_is_passed_through() -> None:
    model = Toy(name="a")
    outcome = parse_attempt(model, Toy)
    assert outcome.output is model


def test_json_string_is_parsed() -> None:
    outcome = parse_attempt('{"name": "a"}', Toy)
    assert outcome.output is not None
    assert outcome.output.name == "a"


def test_fenced_json_is_parsed() -> None:
    outcome = parse_attempt('```json\n{"name": "a"}\n```', Toy)
    assert outcome.output is not None


def test_json_inside_prose_is_parsed() -> None:
    outcome = parse_attempt('Here you go:\n```json\n{"name": "a"}\n```\nDone.', Toy)
    assert outcome.output is not None


def test_broken_json_is_an_issue() -> None:
    outcome = parse_attempt('{"name": ', Toy)
    assert outcome.output is None
    assert [issue.code for issue in outcome.issues] == ["P1_BAD_JSON"]


def test_json_array_is_not_an_object() -> None:
    outcome = parse_attempt("[1, 2, 3]", Toy)
    assert [issue.code for issue in outcome.issues] == ["P1_BAD_JSON"]


def test_schema_mismatch_is_an_issue_with_paths() -> None:
    outcome = parse_attempt({"count": "many"}, Toy)
    assert [issue.code for issue in outcome.issues] == ["P2_SCHEMA"]
    errors = outcome.issues[0].details["errors"]
    assert isinstance(errors, list)
    paths = {str(entry["path"]) for entry in errors if isinstance(entry, dict)}
    assert "name" in paths


def test_truncation_wins_over_parsing() -> None:
    outcome = parse_attempt('{"name": "a"}', Toy, truncated=True)
    assert outcome.output is None
    assert [issue.code for issue in outcome.issues] == ["P3_TRUNCATED"]
    assert outcome.issues[0].severity == "critical"


def test_markdown_fence_without_json_language_is_parsed() -> None:
    outcome = parse_attempt("```\n{\"name\": \"a\"}\n```", Toy)
    assert outcome.output is not None
