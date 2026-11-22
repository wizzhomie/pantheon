"""Shared building blocks for every agentqa contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue

__all__ = ["JsonValue", "StrictModel"]

"""Arbitrary JSON data.

The core never interprets untyped payloads, so this is the only shape it accepts for
"whatever the caller has". Typed data is described by the preset's output model.
``JsonValue`` is reused from Pydantic rather than re-declared: a hand-rolled recursive
alias makes Pydantic's schema builder recurse forever.
"""


class StrictModel(BaseModel):
    """Base class for all public contracts.

    Contracts are **immutable** (a run result is evidence, not a scratch object) and
    **closed** (an unknown field is either schema drift or smuggled data, so it is
    rejected instead of silently ignored).
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )
