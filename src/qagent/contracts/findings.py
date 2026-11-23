"""Findings: a claimed problem with a place, an evidence trail and a state.

This lives in ``contracts`` rather than in the verification engine because both sides of the
loop need it: the reviewer proposes findings, the verdict consumes them, and a report stores
them. Keeping it here also keeps the import graph acyclic — the reviewer must not have to
import the whole verification package to name what it found.

The statuses are the whole difference between "we know this is broken" and "we suspect it":

``confirmed``
    evidence exists: a check failed, or an anchor proved the claim about the code.
``unverified``
    claimed, but nothing has checked the claim yet. Visible, never blocking.
``ambiguous``
    the evidence points both ways; a human has to decide. Never blocking, always a condition.
``false-positive``
    the claim was checked and did not hold up. Kept in the record: a rejected claim is also
    information, and deleting it would hide that the reviewer was wrong.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from .base import StrictModel

FINDING_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,39}$"

FindingKind = Literal["check-failure", "defect", "risk", "test-gap"]
"""What sort of problem the finding describes, not how sure we are of it."""

FindingStatus = Literal["confirmed", "unverified", "ambiguous", "false-positive"]
"""How much is actually known; only ``confirmed`` is evidence."""

FindingSeverity = Literal["blocking", "major", "minor", "info"]
"""What it would cost to ship with this finding still open."""

FindingSource = Literal["check", "reviewer", "human"]
"""Where the claim came from. A source is not a reason to believe it."""


class Location(StrictModel):
    """Where the claim points: a file, a line and the line's text, for re-checking.

    The three belong together. An anchor without a quote is a line number that will drift;
    a quote without a line is a text search; a line without a file is a guess.

    The quote keeps its whitespace: indentation is part of the line, and the comparison
    normalises it later. Only a quote that is *entirely* whitespace is refused.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=False, validate_default=True
    )

    file: str = Field(min_length=1, max_length=400, description="path relative to the project")
    line: int = Field(ge=1, description="1-based line number the claim is about")
    quote: str = Field(
        min_length=1, max_length=300, description="the line's text, verbatim"
    )

    @field_validator("quote")
    @classmethod
    def _not_only_whitespace(cls, quote: str) -> str:
        if not quote.strip():
            raise ValueError("a quote must contain something other than whitespace")
        return quote


class Finding(StrictModel):
    """One claimed problem, with its place, its evidence and how much is known about it."""

    id: str = Field(pattern=FINDING_ID_PATTERN, description="stable id, referenced by reports")
    title: str = Field(min_length=1, max_length=200)
    kind: FindingKind
    status: FindingStatus
    severity: FindingSeverity
    source: FindingSource
    detail: str = Field(default="", max_length=2000)
    location: Location | None = Field(
        default=None, description="unset for a failed check: a command has no line"
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="check ids and artifact paths that support the finding",
    )

    @property
    def blocking(self) -> bool:
        """Only a *confirmed* blocking claim may stop a release; a suspicion may not."""
        return self.status == "confirmed" and self.severity == "blocking"
