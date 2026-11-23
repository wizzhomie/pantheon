"""Contracts for a deterministic verification run: the policy, the plan, the evidence.

A policy is data the project owns; a run is what actually happened. Both are frozen and
strict, because a report that cannot be parsed back is not evidence. The one rule that is
enforced in the model rather than in prose: a command is an argv list, so no component can
be a shell expression by construction.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..contracts.base import StrictModel

CHECK_ID_PATTERN = r"^[a-z][a-z0-9_-]{1,39}$"

CheckStatus = Literal["passed", "failed", "timeout", "error", "skipped", "not_applicable"]
"""``error`` means "could not run". It is not a pass and not a failure of the project."""

PlanReason = Literal["required", "optional", "tool-missing", "budget", "not-selected"]

MAX_OUTPUT_TAIL = 2_000
"""How much of stdout/stderr ends up in the run record; the rest lives in the artifact."""


class Budget(StrictModel):
    """What a run is allowed to spend. Money arrives with the reviewer; time is already real."""

    max_seconds: float = Field(default=600.0, gt=0, description="wall clock for all checks")
    max_checks: int = Field(default=40, gt=0, description="how many checks may run")


class CommandSpec(StrictModel):
    """One check the project already knows how to run."""

    id: str = Field(pattern=CHECK_ID_PATTERN, description="stable id used in reports and skips")
    name: str = Field(default="", max_length=80, description="human name; defaults to the id")
    argv: list[str] = Field(min_length=1, description="argv, never a shell string")
    timeout_s: float = Field(default=300.0, gt=0, description="this check's own ceiling")
    expect_exit: int = Field(default=0, description="which exit code counts as a pass")
    optional: bool = Field(
        default=False, description="an optional check never blocks; it only reports"
    )
    requires: str | None = Field(
        default=None, description="tool name from the project profile; absent means not applicable"
    )
    purpose: str = Field(default="", max_length=200, description="why this check exists")
    env: dict[str, str] = Field(
        default_factory=dict,
        description="extra environment for this check, merged over the whitelist",
    )
    when_paths: list[str] = Field(
        default_factory=list,
        description=(
            "the paths this check guards; empty means every run. A run with a change plans "
            "the check only when a changed path matches one of these globs."
        ),
    )

    @field_validator("when_paths")
    @classmethod
    def _selectors_are_relative_globs(cls, patterns: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in patterns:
            pattern = raw.strip().replace("\\", "/")
            if not pattern or len(pattern) > 200:
                raise ValueError("a path selector must be a non-empty glob under 200 characters")
            if pattern.startswith("/") or ".." in pattern.split("/"):
                raise ValueError(f"a path selector must stay inside the project: {raw!r}")
            cleaned.append(pattern)
        return cleaned

    @field_validator("argv")
    @classmethod
    def _no_empty_arguments(cls, argv: list[str]) -> list[str]:
        if any(not part.strip() for part in argv):
            raise ValueError("argv parts must be non-empty")
        return argv

    @field_validator("argv")
    @classmethod
    def _not_a_shell_string(cls, argv: list[str]) -> list[str]:
        # A single element that appends another command is a shell expression that slipped
        # into a list; reject it loudly instead of letting it run as one program.
        if len(argv) == 1 and re.search(r"[;&|`$>\n]", argv[0]):
            raise ValueError(
                "argv must be a command and its arguments, not a shell string; "
                "run an explicit shell if that is what you mean"
            )
        return argv


class Sandbox(StrictModel):
    """Resource ceilings applied to every check, and nothing more than ceilings.

    This is not isolation. A limit caps what a check may *consume*; it does not decide what
    the check may *reach*. Saying "sandboxed" while a check still has the network and the
    whole file system would be the kind of claim this project exists to refuse, so the
    report repeats the distinction instead of hiding it.

    Zero means "no limit", which is why the default is fully off: a policy that says nothing
    about limits must not pretend it applied some.
    """

    memory_mb: int = Field(
        default=0, ge=0, le=1_048_576, description="address space per check; 0 = no limit"
    )
    file_mb: int = Field(
        default=0, ge=0, le=1_048_576, description="largest file a check may write; 0 = no limit"
    )

    @property
    def enabled(self) -> bool:
        return self.memory_mb > 0 or self.file_mb > 0


class SandboxRecord(StrictModel):
    """What the run actually did about limits — including when it could do nothing.

    ``backend = "unsupported"`` means the policy asked for limits the platform cannot apply;
    the checks are then recorded as errors rather than run without the ceiling they declared,
    so a report can never show a limit it did not enforce.
    """

    backend: Literal["rlimit", "unsupported"] = Field(description="how the limits were applied")
    memory_mb: int = Field(default=0, ge=0, description="applied, or requested when unsupported")
    file_mb: int = Field(default=0, ge=0, description="applied, or requested when unsupported")


class VerificationPolicy(StrictModel):
    """What this project considers its checks, and what a run may spend on them."""

    schema_version: int = Field(default=1)
    name: str = Field(min_length=1, max_length=80)
    checks: list[CommandSpec] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    sandbox: Sandbox = Field(default_factory=Sandbox)

    @field_validator("checks")
    @classmethod
    def _unique_ids(cls, checks: list[CommandSpec]) -> list[CommandSpec]:
        ids = [check.id for check in checks]
        if len(ids) != len(set(ids)):
            raise ValueError("check ids must be unique within a policy")
        return checks

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CheckRun(StrictModel):
    """What happened when one check ran — or why it did not."""

    check_id: str
    name: str
    status: CheckStatus
    exit_code: int | None = Field(default=None, description="None when no process exited")
    duration_ms: float = Field(default=0.0, ge=0)
    stdout_path: str | None = Field(default=None, description="artifact, relative to the run dir")
    stderr_path: str | None = Field(default=None, description="artifact, relative to the run dir")
    output_tail: str = Field(default="", description=f"last {MAX_OUTPUT_TAIL} chars of output")
    optional: bool = Field(default=False, description="copied from the check that produced it")
    selected: bool = Field(
        default=True,
        description="false when the change did not touch this check's paths: not a gap",
    )
    reason: str | None = Field(default=None, description="why it was skipped or could not run")

    @property
    def blocking(self) -> bool:
        """A result a verdict may not ignore: not a pass, not a skip, not optional."""
        return (
            self.selected
            and not self.optional
            and self.status in ("failed", "timeout", "error")
        )


class PlanEntry(StrictModel):
    """One line of the plan: what will run, whether it is mandatory, and why."""

    check: CommandSpec
    reason: PlanReason
    detail: str = Field(default="", max_length=200)


class VerificationRequest(StrictModel):
    """What was asked for, as a value: which project, which checks, which change, which budget.

    A request is data so that a run can be restored and explained: the report carries the
    request it answered, and re-running it is a decision, not an archaeology exercise.

    Paths are relative to the project root, never to the file that holds the request: the
    request describes *the project*, and moving the file must not change what it points at.
    """

    schema_version: int = Field(default=1)
    name: str = Field(default="ad-hoc", min_length=1, max_length=120)
    policy: str = Field(
        default=".trajan/policy.toml", min_length=1, max_length=400, description="policy path"
    )
    diff: str | None = Field(default=None, max_length=400, description="a unified diff to review")
    since: str | None = Field(
        default=None, max_length=200, description="review `git diff REV` instead of a file"
    )
    reviewer: str | None = Field(
        default=None,
        max_length=200,
        description="module:factory returning an LLMClient; required for a review",
    )
    instructions: str | None = Field(
        default=None, max_length=400, description="file with project rules for the reviewer"
    )
    model: str | None = Field(default=None, max_length=120, description="review model override")
    max_tokens: int = Field(default=1500, ge=200, le=8000)
    second_look: bool = Field(
        default=False,
        description="a second reader checks the reviewer's claims; costs one more call each",
    )
    notes: str = Field(default="", max_length=500, description="why this request exists")

    @model_validator(mode="after")
    def _review_parameters_agree(self) -> VerificationRequest:
        if self.diff is not None and self.since is not None:
            raise ValueError("a request reviews either a diff file or a revision, not both")
        if self.needs_review and self.reviewer is None:
            raise ValueError(
                "a request with a change needs a reviewer (--reviewer), or the change is not read"
            )
        if not self.needs_review and self.reviewer is not None:
            raise ValueError("a reviewer without a change has nothing to read")
        return self

    @property
    def needs_review(self) -> bool:
        return self.diff is not None or self.since is not None

    def describe(self) -> str:
        """One line a human can read in a report: what this run was asked to do."""
        change = f"diff {self.diff}" if self.diff else (f"since {self.since}" if self.since else "")
        parts = [self.name, self.policy]
        if change:
            parts.append(change)
        if self.reviewer:
            parts.append(f"reviewer {self.reviewer}")
        return " · ".join(parts)


class VerificationRun(StrictModel):
    """The artifact of one execution: what ran, what it said, what it cost in seconds."""

    run_id: str
    policy_name: str
    policy_digest: str
    profile_digest: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    checks: list[CheckRun] = Field(default_factory=list)
    planned: int = Field(default=0, description="checks the plan contained, including skips")
    spent_seconds: float = Field(default=0.0, ge=0)
    error: str | None = Field(default=None, description="the run itself failed, not a check")
    adopted: list[str] = Field(
        default_factory=list,
        description="proposals accepted from the history and merged into this run's policy",
    )
    sandbox: SandboxRecord | None = Field(
        default=None,
        description="the limits the checks ran under, or why none were applied",
    )

    def by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return dict(sorted(counts.items()))

    def blocking(self) -> list[CheckRun]:
        """Checks whose result a later verdict will have to face. Not a verdict itself."""
        return [check for check in self.checks if check.blocking]
