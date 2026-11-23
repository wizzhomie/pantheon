"""What the project promises, and which promises are actually agreed.

A profile describes the repository as it is; this file describes what the project says it
*should* be, and — the part that matters — which of those claims are approved. ADR-004: a
verification contour that checks against a proposal will demand a future that nobody agreed
to yet, so only ``approved`` items are obligations; ``proposed`` and ``superseded`` are
visible, queryable, and explicitly not enforceable.

The knowledge lives in the project itself (``.trajan/context.toml``, ADR-002) and is reviewed
like code, because it goes stale at the same speed as the code. Nothing here is inferred: a
requirement nobody wrote down cannot be checked, and that is a feature — the gap is visible
instead of imagined.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, field_validator, model_validator

from ..contracts.base import StrictModel

if TYPE_CHECKING:  # the policy is a policy; importing the engine here would be a cycle
    from ..verify.contracts import VerificationPolicy

CONTEXT_VERSION = 1
"""Shape version; a run records the version of the knowledge it was judged against."""

CONTEXT_DIR = ".trajan"
CONTEXT_NAME = "context.toml"

ITEM_ID_PATTERN = r"^[A-Z][A-Z0-9_-]{0,11}$"

ItemStatus = Literal["approved", "proposed", "superseded"]
"""Only ``approved`` is an obligation. The other two are context, not requirements."""

ItemKind = Literal["requirement", "invariant", "milestone"]

ProblemLevel = Literal["error", "note"]


class ContextItem(StrictModel):
    """One promise, invariant or milestone, with the status that decides whether it binds."""

    id: str = Field(pattern=ITEM_ID_PATTERN, description="R1, I2, M3 — stable, never reused")
    title: str = Field(min_length=1, max_length=200)
    status: ItemStatus
    detail: str = Field(default="", max_length=1000)
    checks: list[str] = Field(
        default_factory=list,
        description="check ids from the project policy that would catch a violation",
    )


class Architecture(StrictModel):
    """Where the project is and where it is going, in the project's own words."""

    baseline: str = Field(default="", max_length=2000, description="what exists today")
    target: str = Field(default="", max_length=2000, description="the agreed direction")


class ContextProblem(StrictModel):
    """One thing wrong (or worth saying) about the project's knowledge."""

    level: ProblemLevel
    code: Literal[
        "unknown-check",
        "approved-without-check",
        "check-without-item",
        "no-policy",
    ]
    detail: str = Field(min_length=1, max_length=400)


class ProjectContext(StrictModel):
    """Everything the project says about itself that is not in its code."""

    schema_version: int = Field(default=CONTEXT_VERSION)
    architecture: Architecture = Field(default_factory=Architecture)
    requirements: list[ContextItem] = Field(default_factory=list)
    invariants: list[ContextItem] = Field(default_factory=list)
    milestones: list[ContextItem] = Field(default_factory=list)

    @field_validator("milestones")
    @classmethod
    def _milestones_are_not_checked(cls, milestones: list[ContextItem]) -> list[ContextItem]:
        if any(item.checks for item in milestones):
            raise ValueError(
                "a milestone is a state, not a checkable promise; link checks to requirements"
            )
        return milestones

    @model_validator(mode="after")
    def _unique_ids(self) -> ProjectContext:
        ids = [item.id for item in self.items()]
        duplicated = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if duplicated:
            raise ValueError(f"duplicate context ids: {', '.join(duplicated)}")
        return self

    def items(self) -> list[ContextItem]:
        """Every item, in a stable order: requirements, invariants, milestones."""
        return [*self.requirements, *self.invariants, *self.milestones]

    def approved(self) -> list[ContextItem]:
        """What the contour may hold a change against, and nothing else."""
        return [item for item in self.items() if item.status == "approved"]

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def summary(self) -> str:
        return (
            f"{len(self.requirements)} requirements, {len(self.invariants)} invariants, "
            f"{len(self.milestones)} milestones "
            f"({len(self.approved())} approved)"
        )


def context_path(root: Path) -> Path:
    return Path(root) / CONTEXT_DIR / CONTEXT_NAME


def read_context(root: Path) -> ProjectContext:
    """Load the project's knowledge. A missing file is an error, not an empty promise."""
    path = context_path(root)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"no project context at {path}") from exc
    return ProjectContext.model_validate(data)


def check_context(
    context: ProjectContext, policy: VerificationPolicy | None
) -> list[ContextProblem]:
    """Cross-check the promises against the checks that are supposed to catch violations.

    An unknown check id is an error: the promise points at something that does not exist, and
    a reader would trust it. Everything else is a note: a promise without a check is a real
    obligation nobody can verify, and a check without a promise is verification that cannot
    say *why*. Both are worth seeing, neither is a lie.
    """
    problems: list[ContextProblem] = []
    if policy is None:
        problems.append(
            ContextProblem(
                level="note",
                code="no-policy",
                detail="no policy file, so the links to checks could not be checked",
            )
        )
        return problems

    known = {check.id for check in policy.checks}
    linked: set[str] = set()
    for item in [*context.requirements, *context.invariants]:
        for check_id in item.checks:
            if check_id not in known:
                problems.append(
                    ContextProblem(
                        level="error",
                        code="unknown-check",
                        detail=(
                            f"{item.id} names check {check_id!r}, "
                            "which the policy does not have"
                        ),
                    )
                )
            linked.add(check_id)
        if item.status == "approved" and not item.checks:
            problems.append(
                ContextProblem(
                    level="note",
                    code="approved-without-check",
                    detail=f"{item.id} is approved but nothing checks it",
                )
            )
    for check_id in sorted(known - linked):
        problems.append(
            ContextProblem(
                level="note",
                code="check-without-item",
                detail=f"check {check_id!r} is not linked to any requirement or invariant",
            )
        )
    return problems
