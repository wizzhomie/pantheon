"""The output contract: what a code review is allowed to say.

Two decisions worth knowing:

* a finding carries the **line** and the **verbatim line text**. A reviewer that cannot
  point at the code is guessing, and a guessed line is what the checks are built to catch;
* ``verdict`` is separate from the findings so "no problems" is a statement of its own —
  otherwise an empty list means both "clean" and "I did not look".
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from qagent import StrictModel


class Finding(StrictModel):
    """One defect, anchored to a line of the file under review."""

    line: int = Field(ge=1, le=400, description="1-based line number in the reviewed code")
    severity: Literal["error", "warning"] = "error"
    message: str = Field(min_length=1, max_length=400, description="what is wrong, in one sentence")
    quote: str = Field(
        min_length=1,
        max_length=400,
        description="the line text, copied verbatim from the code (no line numbers)",
    )


class Review(StrictModel):
    """The whole answer for one file."""

    verdict: Literal["ok", "problems"]
    findings: list[Finding] = Field(default_factory=list, max_length=10)
