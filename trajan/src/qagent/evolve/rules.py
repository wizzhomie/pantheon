"""Mining the history for a rule, and replaying it over the verdicts that already exist.

The interesting number in an evolution loop is not "this check was flaky". It is "these runs
shipped with a verdict this rule would have stopped". That number is computed here by calling
the real :func:`decide` on the real recorded run with one field changed — the check becomes
required — so a proposal's claim is a counterfactual a reader can reproduce, not a score.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from ..verify.contracts import CommandSpec, VerificationPolicy, VerificationRun
from ..verify.verdict import Verdict, VerdictCode, decide
from .contracts import (
    Audit,
    Change,
    Evidence,
    Proposal,
    Retrospective,
    check_digest,
    proposal_id,
)
from .history import History, RunFacts

MIN_FAILING_RUNS = 2
"""One failure is an incident; the same failure twice is a pattern the policy did not encode."""

FAILING = ("failed", "timeout", "error")
"""A check that could not run is included: an optional check that never runs is a silent gap."""

REVIEW_GAPS: VerdictCode = "review-failed"


def promote(run: VerificationRun, check_id: str) -> VerificationRun:
    """The same run, with one check required. The only difference the proposed rule makes."""
    checks = [
        check.model_copy(update={"optional": False}) if check.check_id == check_id else check
        for check in run.checks
    ]
    return run.model_copy(update={"checks": checks})


def gaps_from(verdict: Verdict) -> list[str]:
    """Reconstruct the run's gaps from its verdict, so the replay keeps the same blind spots.

    The report does not store the gap list separately — the verdict reasons are the record —
    and dropping them would let the replay "improve" a run that was inconclusive because the
    reviewer never ran.
    """
    return [reason.detail for reason in verdict.reasons if reason.code == REVIEW_GAPS]


def replay(facts: RunFacts, check_id: str) -> Verdict:
    """What the verdict would have been under the proposed rule, on the same evidence."""
    return decide(promote(facts.run, check_id), facts.findings, gaps=gaps_from(facts.verdict))


def _failure(facts: RunFacts, check_id: str) -> bool:
    check = facts.check(check_id)
    return check is not None and check.status in FAILING


def retrospective(history: History, check_id: str) -> Retrospective:
    """Count what the rule would have caught, and what it would have left alone."""
    runs = failed = caught = blocked = unchanged = passed = 0
    flips: list[str] = []
    for facts in history.containing(check_id):
        runs += 1
        if not _failure(facts, check_id):
            passed += 1
            continue
        failed += 1
        after = replay(facts, check_id)
        if not facts.shippable:
            blocked += 1
        elif not after.shippable:
            caught += 1
            flips.append(facts.run_id)
        else:
            unchanged += 1
    return Retrospective(
        runs=runs,
        failed=failed,
        caught=caught,
        already_blocked=blocked,
        unchanged=unchanged,
        passed=passed,
        flips=flips,
    )


def evidence_for(history: History, check_id: str) -> list[Evidence]:
    """One entry per failing run, carrying the verdict it had and the one it would have had."""
    items: list[Evidence] = []
    for facts in history.containing(check_id):
        if not _failure(facts, check_id):
            continue
        check = facts.check(check_id)
        assert check is not None
        items.append(
            Evidence(
                run_id=facts.run_id,
                verdict=facts.verdict.status,
                check_status=check.status,
                would_be=replay(facts, check_id).status,
                finding_ids=facts.findings_for(check_id),
                started_at=facts.started_at,
            )
        )
    return items


def mine(
    history: History,
    policy: VerificationPolicy,
    *,
    existing: Iterable[Proposal] = (),
    min_failures: int = MIN_FAILING_RUNS,
    now: datetime | None = None,
) -> list[Proposal]:
    """Proposals the history argues for, in policy order; nothing is written or decided."""
    decided = list(existing)
    created = now or datetime.now(UTC)
    proposals: list[Proposal] = []
    for spec in policy.checks:
        if not spec.optional or _decided(decided, spec):
            continue
        counts = retrospective(history, spec.id)
        if counts.failed < min_failures or not counts.earned:
            continue
        proposals.append(
            _proposal(
                spec,
                policy,
                counts,
                evidence_for(history, spec.id),
                created,
            )
        )
    return proposals


def audit(proposal: Proposal, history: History) -> Audit:
    """What happened after the decision: runs, failures, and verdicts the rule stopped."""
    check_id = proposal.change.check_id
    decided_at = proposal.decided_at
    if decided_at is None:
        return Audit()
    since = decided_at
    runs = failed = held = 0
    for facts in history.containing(check_id):
        if facts.started_at < since:
            continue
        runs += 1
        if not _failure(facts, check_id):
            continue
        failed += 1
        if facts.verdict.status == "hold":
            held += 1
    return Audit(since=since, runs=runs, failed=failed, held=held)


def _proposal(
    spec: CommandSpec,
    policy: VerificationPolicy,
    counts: Retrospective,
    evidence: list[Evidence],
    created_at: datetime,
) -> Proposal:
    digest = check_digest(spec)
    title = f"Make the {spec.id} check required"
    purpose = f" Its purpose: {spec.purpose}." if spec.purpose else ""
    rationale = (
        f"{spec.id} is optional, so its failures are findings instead of gates.{purpose} "
        f"{counts.summary()}; requiring it would have held "
        f"{_runs(counts.flips)} that the verdict let through."
    )
    return Proposal(
        id=proposal_id(spec.id, digest),
        kind="promote-check",
        title=title,
        rationale=rationale,
        change=Change(
            check_id=spec.id,
            check_digest=digest,
            before_optional=spec.optional,
            after_optional=False,
        ),
        retrospective=counts,
        evidence=evidence,
        policy_digest=policy.digest(),
        created_at=created_at,
    )


def _runs(run_ids: list[str]) -> str:
    """Name the caught runs, but keep the rationale inside its length, however long it gets."""
    if len(run_ids) == 1:
        return f"the run {run_ids[0]}"
    shown = ", ".join(run_ids[:3]) + (", …" if len(run_ids) > 3 else "")
    return f"{len(run_ids)} runs ({shown})"


def _decided(existing: list[Proposal], spec: CommandSpec) -> bool:
    """A decision already covers this exact command; a different command is a new question."""
    digest = check_digest(spec)
    return any(
        proposal.change.check_id == spec.id
        and proposal.change.check_digest == digest
        and proposal.status != "proposed"
        for proposal in existing
    )
