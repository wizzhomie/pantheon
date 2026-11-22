"""Scoring one output against the golden values.

This is the part of evaluation that cannot be delegated to a model: the preset states which
fields matter, how strictly they are compared, and which values must never appear. The
arithmetic is deliberately small and readable — a report whose numbers nobody can recompute
by hand is a report nobody will trust.

Two decisions worth knowing:

* ``passed`` means **ground truth**, not "the loop accepted it". A gate that approves a
  wrong answer is a false pass, and the report has a separate rate for exactly that.
* a trap hit fails the case outright. Traps exist for fabrication bait, and a fabricated
  value is not compensated by nine correct fields.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import cast

from pydantic import BaseModel

from ..checks.paths import MISSING, get_path
from ..contracts.base import JsonValue
from ..contracts.evals import EvalCase, FieldSpec, ScoringSpec, Trap

FUZZY_FLOOR = 0.9
"""Default similarity for the ``fuzzy`` comparator when the spec gives no tolerance."""


@dataclass(frozen=True, slots=True)
class CaseScore:
    """How one output compares with the golden values."""

    field_accuracy: float
    passed: bool
    missing: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)
    hallucinated: list[str] = field(default_factory=list)


def score_case(
    output: BaseModel | None,
    case: EvalCase,
    spec: ScoringSpec,
) -> CaseScore:
    """Score ``output`` against ``case.expected`` and the combined trap list."""
    total_weight = 0.0
    matched_weight = 0.0
    missing: list[str] = []
    wrong: list[str] = []

    for field_spec in spec.fields:
        total_weight += field_spec.weight
        actual = get_path(output, field_spec.path) if output is not None else MISSING
        expected = get_path(case.expected, field_spec.path)
        if actual is MISSING:
            missing.append(field_spec.path)
            continue
        if expected is MISSING:
            # A spec that points at a field the golden case does not have is a preset bug,
            # not an agent failure: count it as wrong so it cannot pass silently.
            wrong.append(field_spec.path)
            continue
        if matches(actual, cast(JsonValue, expected), field_spec):
            matched_weight += field_spec.weight
        else:
            wrong.append(field_spec.path)

    accuracy = matched_weight / total_weight if total_weight else 1.0
    accuracy = round(accuracy, 6)
    hallucinated = _trap_hits(output, [*spec.traps, *case.traps])
    threshold = spec.min_field_accuracy if spec.min_field_accuracy is not None else 1.0
    passed = accuracy >= threshold and not hallucinated
    return CaseScore(
        field_accuracy=round(accuracy, 4),
        passed=passed,
        missing=missing,
        wrong=wrong,
        hallucinated=hallucinated,
    )


def matches(actual: object, expected: JsonValue, spec: FieldSpec) -> bool:
    """Compare one value using the comparator named by the spec."""
    if spec.comparator == "exact":
        return _jsonable(actual) == _jsonable(expected)
    if spec.comparator == "fuzzy":
        return _fuzzy(actual, expected, spec.tolerance)
    if spec.comparator == "set":
        left = _as_set(actual)
        return left is not None and left == _as_set(expected)
    if spec.comparator == "numeric":
        return _numeric(actual, expected, spec.tolerance)
    return _normalise(actual) == _normalise(expected)


def _trap_hits(output: BaseModel | None, traps: Sequence[Trap]) -> list[str]:
    hits: list[str] = []
    for trap in traps:
        actual = get_path(output, trap.path) if output is not None else MISSING
        if actual is MISSING:
            continue
        candidates: Iterable[object] = (
            actual if isinstance(actual, (list, tuple)) else [actual]
        )
        hit = any(
            _trap_equal(value, forbidden)
            for value in candidates
            for forbidden in trap.forbidden
        )
        if hit and trap.path not in hits:
            hits.append(trap.path)
    return hits


def _trap_equal(actual: object, forbidden: JsonValue) -> bool:
    return _normalise(actual) == _normalise(forbidden)


def _normalise(value: object) -> object:
    """Case- and whitespace-insensitive form; numbers compare numerically."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return _jsonable(value)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _as_set(value: object) -> frozenset[str] | None:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    return frozenset(json.dumps(_jsonable(item), sort_keys=True, default=str) for item in value)


def _numeric(actual: object, expected: JsonValue, tolerance: float | None) -> bool:
    # ``None`` is a value the golden answer can legitimately require ("no amount was
    # stated"); treating it as not-a-number would make every nullable numeric field fail.
    if actual is None or expected is None:
        return actual is None and expected is None
    left = _as_float(actual)
    right = _as_float(expected)
    if left is None or right is None:
        return False
    return abs(left - right) <= (tolerance or 0.0)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _fuzzy(actual: object, expected: JsonValue, tolerance: float | None) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        left = " ".join(actual.split()).casefold()
        right = " ".join(expected.split()).casefold()
        return SequenceMatcher(None, left, right).ratio() >= (tolerance or FUZZY_FLOOR)
    return _normalise(actual) == _normalise(expected)
