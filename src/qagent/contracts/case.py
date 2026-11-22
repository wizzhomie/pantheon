"""The unit of work: one case in, one structured result out."""

from __future__ import annotations

import json

from pydantic import Field

from .base import JsonValue, StrictModel

CASE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$"


class Case(StrictModel):
    """One input handed to the wrapped agent.

    The payload is deliberately untyped: the preset decides what it means (an email,
    a scanned document, a tool-call request, a dialogue transcript). The core only
    needs a stable id and something it can search when evidence checks are enabled,
    which is what :meth:`as_text` provides.
    """

    case_id: str = Field(
        pattern=CASE_ID_PATTERN,
        description="stable identifier, reused in traces and reports",
    )
    payload: JsonValue = Field(description="raw input for the agent")
    meta: dict[str, str] = Field(
        default_factory=dict,
        description="free-form labels such as source channel or language",
    )

    def as_text(self) -> str:
        """Flatten the payload into searchable text for evidence checks.

        A string payload is returned as is (the common case: a document body); anything
        else is serialised deterministically so quotes can still be located.
        """
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
