"""Merging accepted proposals over the project's policy, and saying why one is ignored.

An accepted rule is an overlay, not an edit: ``policy.toml`` stays the human's file, comments
and all, and every run that applies a proposal records which one it applied. The merge is
also where an approval stops being valid — if the check it described was renamed, retimed or
removed, the evidence no longer describes the command, and the proposal is reported as
``stale`` or ``obsolete`` instead of being applied to something else.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from ..contracts.base import StrictModel
from ..verify.contracts import VerificationPolicy
from .contracts import Proposal, check_digest

Applicability = Literal["applicable", "pending", "satisfied", "stale", "obsolete", "rejected"]
"""``pending`` waits for a decision; the other four explain why a decision is not applied."""

IGNORED_REASON: dict[Applicability, str] = {
    "applicable": "applied",
    "pending": "waiting for a decision; run `qagent learn --approve <id>`",
    "satisfied": "the check is already required",
    "stale": "the check command changed since the proposal, so its evidence no longer applies",
    "obsolete": "the check no longer exists in the policy",
    "rejected": "rejected by a human",
}


class Ignored(StrictModel):
    """A proposal that is not being applied, and the reason a reader needs."""

    id: str
    applicability: Applicability
    detail: str


class AppliedPolicy(StrictModel):
    """The policy a run actually uses, plus the paper trail of what was merged into it."""

    policy: VerificationPolicy
    adopted: list[str] = Field(default_factory=list)
    ignored: list[Ignored] = Field(default_factory=list)


def applicability(proposal: Proposal, policy: VerificationPolicy) -> Applicability:
    """Can this decision be applied to *this* policy, unchanged?"""
    if proposal.status == "rejected":
        return "rejected"
    spec = next((check for check in policy.checks if check.id == proposal.change.check_id), None)
    if spec is None:
        return "obsolete"
    if check_digest(spec) != proposal.change.check_digest:
        return "stale"
    if not spec.optional:
        return "satisfied"
    return "applicable" if proposal.status == "approved" else "pending"


def apply_proposals(policy: VerificationPolicy, proposals: Iterable[Proposal]) -> AppliedPolicy:
    """Overlay every accepted, still-valid proposal; report the ones that are not applied."""
    items = list(proposals)
    applicable: list[Proposal] = []
    ignored: list[Ignored] = []
    for proposal in items:
        state = applicability(proposal, policy)
        if state == "applicable":
            applicable.append(proposal)
        else:
            ignored.append(
                Ignored(
                    id=proposal.id,
                    applicability=state,
                    detail=IGNORED_REASON[state],
                )
            )
    if not applicable:
        return AppliedPolicy(policy=policy, ignored=sorted(ignored, key=lambda item: item.id))
    accepted = {proposal.change.check_id for proposal in applicable}
    checks = [
        check.model_copy(update={"optional": False}) if check.id in accepted else check
        for check in policy.checks
    ]
    return AppliedPolicy(
        policy=policy.model_copy(update={"checks": checks}),
        adopted=sorted(proposal.id for proposal in applicable),
        ignored=sorted(ignored, key=lambda item: item.id),
    )
