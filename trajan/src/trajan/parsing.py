"""Turning whatever the agent returned into the preset's output model.

Parse failures are issues too, not exceptions: a run that cannot parse an answer is a run
with a defect (``P1_BAD_JSON`` / ``P2_SCHEMA`` / ``P3_TRUNCATED``), and the same retry
machinery should handle it as any other defect. Models also like to wrap JSON in prose or
fences, so a tolerant extraction is attempted before giving up.
"""

from __future__ import annotations

import json
import re
from typing import Generic, TypeVar, cast

from pydantic import BaseModel, Field, ValidationError

from .contracts.agent import Attempt
from .contracts.base import JsonValue, StrictModel
from .contracts.issues import Issue

TOut = TypeVar("TOut", bound=BaseModel)

_FENCES = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class ParseOutcome(StrictModel, Generic[TOut]):
    """Either a validated output or the reasons it could not be produced."""

    output: TOut | None = None
    issues: list[Issue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.output is not None


def parse_attempt(
    attempt: Attempt,
    output_model: type[TOut],
    *,
    truncated: bool = False,
) -> ParseOutcome[TOut]:
    """Validate ``attempt`` against ``output_model``.

    ``truncated`` is supplied by the LLM client (``finish_reason == "length"``); it is not
    inferred here, because a truncated answer often still parses and would otherwise look
    like a clean success.
    """
    if truncated:
        return ParseOutcome[TOut](
            output=None,
            issues=[
                Issue(
                    code="P3_TRUNCATED",
                    severity="critical",
                    source="parse",
                    message="the model stopped at its output limit",
                    hint="raise the output token budget or shorten the input",
                )
            ],
        )

    if isinstance(attempt, output_model):
        return ParseOutcome[TOut](output=attempt)

    try:
        payload = _as_mapping(attempt)
    except ValueError as exc:
        return ParseOutcome[TOut](
            output=None,
            issues=[
                Issue(
                    code="P1_BAD_JSON",
                    severity="error",
                    source="parse",
                    message="the response is not a JSON object",
                    hint="return a single JSON object that matches the schema",
                    details={"error": str(exc)[:200]},
                )
            ],
        )

    try:
        parsed = output_model.model_validate(payload)
    except ValidationError as exc:
        return ParseOutcome[TOut](
            output=None,
            issues=[
                Issue(
                    code="P2_SCHEMA",
                    severity="error",
                    source="parse",
                    message=f"the response does not match {output_model.__name__}",
                    hint="fix the listed fields; do not add fields the schema does not declare",
                    # cast: mypy cannot see through a recursive JSON alias
                    details={"errors": cast(JsonValue, _schema_errors(exc)[:10])},
                )
            ],
        )
    return ParseOutcome[TOut](output=parsed)


def _as_mapping(attempt: Attempt) -> dict[str, JsonValue]:
    if isinstance(attempt, BaseModel):
        dumped = attempt.model_dump(mode="json")
        if not isinstance(dumped, dict):
            raise ValueError("a model instance did not serialise to an object")
        return dumped
    if isinstance(attempt, dict):
        return attempt
    if isinstance(attempt, str):
        return _decode(attempt)
    raise ValueError(f"unsupported attempt type: {type(attempt).__name__}")


def _decode(text: str) -> dict[str, JsonValue]:
    stripped = text.strip()
    for candidate in _candidates(stripped):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
        raise ValueError(f"expected a JSON object, got {type(decoded).__name__}")
    raise ValueError("no JSON object found in the response")


def _candidates(text: str) -> list[str]:
    candidates = [text]
    fenced = _FENCES.search(text)
    if fenced is not None:
        candidates.append(fenced.group(1))
    braced = _OBJECT.search(text)
    if braced is not None:
        candidates.append(braced.group(0))
    return candidates


def _schema_errors(exc: ValidationError) -> list[dict[str, JsonValue]]:
    """Sanitised validation errors: JSON-safe, and without user values."""
    sanitised: list[dict[str, JsonValue]] = []
    for raw in exc.errors(include_url=False):
        location = raw.get("loc", ())
        sanitised.append(
            {
                "path": ".".join(str(part) for part in location),
                "type": str(raw.get("type", "")),
                "msg": str(raw.get("msg", "")),
            }
        )
    return sanitised
