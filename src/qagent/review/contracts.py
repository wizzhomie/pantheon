"""What a reviewer is allowed to say, and what the run keeps of it.

The model's output is not the result. It is a *proposal*: a claim about a file and a line,
which the anchor either confirms or leaves unverified. Keeping the proposal and the verdict
in one object is what makes the report honest — a rejected claim stays visible instead of
disappearing between the prompt and the verdict.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..contracts.base import StrictModel
from ..contracts.findings import Finding

ProposedKind = Literal["defect", "risk", "test-gap"]
ProposedSeverity = Literal["blocking", "major", "minor"]

ReviewStatus = Literal["ok", "failed"]
"""``failed`` means the review itself did not happen — a gap, not a clean bill of health."""


class ProposedFinding(StrictModel):
    """One finding as the model wrote it, before anything in the repository confirmed it."""

    file: str = Field(min_length=1, max_length=400, description="path as it appears in the diff")
    line: int = Field(ge=1, description="line number in the new file")
    quote: str = Field(min_length=1, max_length=300, description="the line's text, verbatim")
    title: str = Field(min_length=1, max_length=200)
    severity: ProposedSeverity
    kind: ProposedKind = "defect"
    requirement: str | None = Field(
        default=None,
        max_length=12,
        description="id of the approved requirement this finding violates, if any",
    )
    detail: str = Field(default="", max_length=2000)


class ProposedReview(StrictModel):
    """The whole answer expected from one review call."""

    summary: str = Field(default="", max_length=1000)
    findings: list[ProposedFinding] = Field(default_factory=list, max_length=20)


class ReviewerConfig(StrictModel):
    """How much the reviewer may read and say. Every field here is a cost."""

    model: str = Field(min_length=1, max_length=120)
    instructions: str = Field(
        default="", max_length=4000, description="project-specific rules appended to the prompt"
    )
    max_tokens: int = Field(default=1500, ge=200, le=8000)
    temperature: float = Field(default=0.0, ge=0, le=1)
    max_files: int = Field(default=20, ge=1, le=200)
    max_diff_bytes: int = Field(default=120_000, ge=2_000, le=1_000_000)
    second_look: bool = Field(
        default=False,
        description="a second reader checks every claim before the anchor; costs one more call",
    )


class ReviewOutcome(StrictModel):
    """What one review produced: the proposals, their anchors, and what it cost."""

    status: ReviewStatus
    model: str = ""
    diff_digest: str = ""
    files_reviewed: int = Field(default=0, ge=0)
    proposed: int = Field(default=0, ge=0)
    confirmed: int = Field(default=0, ge=0, description="findings the anchor accepted")
    unverified: int = Field(default=0, ge=0, description="proposals the anchor refused")
    linked: int = Field(
        default=0, ge=0, description="findings tied to an approved requirement"
    )
    findings: list[Finding] = Field(default_factory=list)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    spent_seconds: float = Field(default=0.0, ge=0)
    second_look: Literal["off", "ok", "failed"] = Field(
        default="off", description="whether a second reader checked the claims, and whether it ran"
    )
    refuted: int = Field(default=0, ge=0, description="claims the second look marked false")
    cached: bool = Field(
        default=False,
        description="served from the review cache; the tokens were paid on the original call",
    )
    error: str | None = Field(default=None, description="why the review did not happen")
