"""Dotted paths are how presets describe fields, so their edge cases matter."""

from __future__ import annotations

from pydantic import BaseModel

from agentqa.checks.paths import MISSING, get_path, is_present, iter_leaves


class Inner(BaseModel):
    weight: float


class Outer(BaseModel):
    items: list[Inner]
    note: str | None = None


def test_resolves_mapping_path() -> None:
    assert get_path({"a": {"b": 1}}, "a.b") == 1


def test_resolves_list_index() -> None:
    assert get_path({"cargo": [{"w": 2}]}, "cargo.0.w") == 2


def test_resolves_through_models() -> None:
    output = Outer(items=[Inner(weight=1.5)], note="x")
    assert get_path(output, "items.0.weight") == 1.5
    assert get_path(output, "note") == "x"


def test_missing_key_is_missing() -> None:
    assert get_path({"a": 1}, "b") is MISSING


def test_unknown_model_field_is_missing() -> None:
    assert get_path(Outer(items=[]), "nope") is MISSING


def test_non_numeric_index_is_missing() -> None:
    assert get_path({"a": [1]}, "a.x") is MISSING


def test_index_out_of_range_is_missing() -> None:
    assert get_path({"a": [1]}, "a.5") is MISSING


def test_none_is_not_traversable() -> None:
    assert get_path({"a": None}, "a.b") is MISSING


def test_iter_leaves_is_stable_and_prefixed() -> None:
    output = Outer(items=[Inner(weight=1.0)], note="x")
    assert dict(iter_leaves(output)) == {"items.0.weight": 1.0, "note": "x"}


def test_is_present_rejects_empty_containers_and_blank_strings() -> None:
    assert is_present("text") is True
    assert is_present(0) is True
    assert is_present(False) is True
    assert is_present("   ") is False
    assert is_present([]) is False
    assert is_present({}) is False
    assert is_present(None) is False
    assert is_present(MISSING) is False
