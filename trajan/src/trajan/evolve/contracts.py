"""A proposal to change the project's own checks, and the evidence that would justify it.

Two failure modes sit on either side of this file. A contour that never changes its rules
stops noticing what it keeps missing; a contour that changes them by itself is just another
agent writing its own exam. The proposal is the middle ground: the tool mines the recorded
runs and states, as data, *what* it would change and *what that change would have done to the
verdicts it already has*; a human flips the status with an explicit command.

The id is derived, not chosen: ``p-promote-<check>-<digest6>`` names the rule by what it
changes and by the exact command it was mined against. If the command changes, the evidence
stops describing it, and a different id appears instead of a stale approval silently
applying to something else.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..contracts.base import StrictModel
from ..verify.contracts import CheckStatus, CommandSpec
from ..verify.verdict import VerdictStatus

PROPOSAL_VERSION = 1

PROPOSAL_ID_PATTERN = r"^p-[a-z][a-z0-9]*(?:-[a-z0-9]+)+$"
"""``p-promote-types-1a2b3c``: a prefix, the check, and the command it was mined against."""

ProposalKind = Literal["promote-check"]
"""The kinds of rule this contour knows how to justify. One is enough to start; the field is
an enum so that a second kind is a schema decision, not an accident of string building."""

ProposalStatus = Literal["proposed", "approved", "rejected"]
"""A decision is a human's. ``proposed`` means nobody has decided yet; it changes nothing."""


COMMAND_FIELDS = ("id", "argv", "timeout_s", "expect_exit", "requires", "env")
"""What makes a check *that command*: how it runs and what it is allowed to do.

``optional`` is excluded on purpose — it is the field a promotion changes, so including it
would make every proposal stale the moment it was approved. ``name`` and ``purpose`` are
excluded because renaming a check does not invalidate the evidence about what it saw.
"""


def check_digest(spec: CommandSpec) -> str:
    """The identity of a check as a command, so evidence cannot drift onto another one.

    Evidence about ``types`` says nothing about a later ``types`` that runs a different
    program, so a proposal pins this digest and is marked stale when it no longer matches.
    """
    payload = {field: getattr(spec, field) for field in COMMAND_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def proposal_id(check_id: str, digest: str) -> str:
    return f"p-promote-{check_id}-{digest[:6]}"


class Evidence(StrictModel):
    """One recorded run that the proposal rests on, and what the rule would have said."""

    run_id: str
    verdict: VerdictStatus
    check_status: CheckStatus
    would_be: VerdictStatus | None = Field(
        default=None, description="the verdict this run would have had under the new rule"
    )
    finding_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None

    @property
    def changed(self) -> bool:
        return self.would_be is not None and self.would_be != self.verdict


class Retrospective(StrictModel):
    """What the rule would have done to the verdicts already on record.

    This is the whole argument for a proposal, and it costs nothing: the past runs are
    re-decided by the same code that will decide the next one, so the number is a counter-
    factual, not an estimate. ``caught`` counts runs that shipped with a verdict the rule
    would have stopped; a rule that catches nothing has no evidence and is not proposed.
    """

    runs: int = Field(ge=0, description="recorded runs in which the check appeared")
    failed: int = Field(ge=0, description="runs in which it failed, timed out or could not run")
    caught: int = Field(ge=0, description="failed runs that were still shippable")
    already_blocked: int = Field(ge=0, description="failed runs whose verdict already stopped them")
    unchanged: int = Field(ge=0, description="failed runs whose verdict would not move")
    passed: int = Field(ge=0, description="runs in which the check passed")
    flips: list[str] = Field(default_factory=list, description="the caught run ids, for the report")

    @model_validator(mode="after")
    def _adds_up(self) -> Retrospective:
        if self.caught + self.already_blocked + self.unchanged != self.failed:
            raise ValueError("caught, already_blocked and unchanged must add up to failed")
        if self.failed + self.passed != self.runs:
            raise ValueError("failed and passed must add up to runs")
        if len(self.flips) != self.caught:
            raise ValueError("one flip per caught run, so the claim has a run behind it")
        return self

    @property
    def earned(self) -> bool:
        """At least one shipped verdict would have been held: the rule has a fact behind it."""
        return self.caught > 0

    def summary(self) -> str:
        held = f"{self.already_blocked} were already held"
        return (
            f"{self.failed} of {self.runs} recorded runs failed it; "
            f"{self.caught} of those still shipped, {held}"
        )


class Change(StrictModel):
    """The one thing approving this proposal would do to the policy."""

    check_id: str
    check_digest: str = Field(min_length=16, description="the command the evidence describes")
    before_optional: bool = True
    after_optional: bool = False

    @model_validator(mode="after")
    def _is_a_change(self) -> Change:
        if self.before_optional == self.after_optional:
            raise ValueError("a proposal must change something")
        return self


class Proposal(StrictModel):
    """A rule the history argues for, waiting for a decision that is not the tool's to make."""

    schema_version: int = Field(default=PROPOSAL_VERSION)
    id: str = Field(pattern=PROPOSAL_ID_PATTERN, description="derived from the change")
    kind: ProposalKind = "promote-check"
    title: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=1000)
    change: Change
    retrospective: Retrospective
    evidence: list[Evidence] = Field(default_factory=list)
    policy_digest: str = Field(description="the policy the proposal was mined from")
    status: ProposalStatus = "proposed"
    note: str = Field(default="", max_length=500, description="why a human decided it")
    created_at: datetime
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def _id_names_the_change(self) -> Proposal:
        expected = proposal_id(self.change.check_id, self.change.check_digest)
        if self.id != expected:
            raise ValueError(f"proposal id {self.id} does not match its change ({expected})")
        return self

    @model_validator(mode="after")
    def _decision_is_complete(self) -> Proposal:
        if self.status == "proposed" and self.decided_at is not None:
            raise ValueError("a proposal that nobody decided cannot have a decision time")
        if self.status != "proposed" and self.decided_at is None:
            raise ValueError("a decided proposal records when it was decided")
        return self

    @field_validator("note")
    @classmethod
    def _strip_note(cls, note: str) -> str:
        return note.strip()

    def evidence_digest(self) -> str:
        """The evidence a decision was made on, so later arrivals can be told apart."""
        payload = [[item.run_id, item.verdict, item.check_status] for item in self.evidence]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def decided(
        self, status: ProposalStatus, *, note: str = "", at: datetime | None = None
    ) -> Proposal:
        """The same proposal with a human's decision attached. Pure: IO lives in the store."""
        if status == "proposed":
            raise ValueError("a decision is approved or rejected; proposed is the absence of one")
        return self.model_copy(
            update={
                "status": status,
                "note": note.strip(),
                "decided_at": at or datetime.now(UTC),
            }
        )


class Audit(StrictModel):
    """What an approved rule did *after* it was approved: did it keep earning its place?

    Adoption is not the end of the argument. If the rule never fires again, that is fine; if
    it fires on every run, it is noise the project now pays for, and the same history shows it.
    """

    since: datetime | None = None
    runs: int = Field(default=0, ge=0, description="runs recorded after the decision")
    failed: int = Field(default=0, ge=0)
    held: int = Field(default=0, ge=0, description="runs the verdict stopped because of it")
