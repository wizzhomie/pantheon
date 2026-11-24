"""The runner: the campaign's own rule, executed instead of described.

A campaign in ``auto`` mode says its steps close themselves. This module is what that sentence
means: it asks the plan which step may run now, takes it through the same gates, checks, reader
and commit a person gets with ``pantheon step``, and retries only while the retry rule allows.
It decides nothing on its own — the step order comes from the plan, each outcome from Trajan's
verdict, the retry from the rule the code already had — and it stops the moment one of them
disagrees, with the reason written down.

The number of attempts a run may spend is the plan's own ceiling, summed over its steps: no
constant here overrides what the owner declared.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import augustus, campaign
from . import box as boxes

if TYPE_CHECKING:
    from . import contour

EXIT_STOPPED = 2
"""A stop is not a crash: the run did what the plan allowed and says why it went no further."""

RUN_DIR = Path("artifacts") / "campaign"


@dataclass(frozen=True, slots=True)
class Attempt:
    """One step executed once: what the box recorded, and whether that closed it."""

    step: campaign.Step
    dispatch: augustus.Dispatch
    state: campaign.StepState
    closed: bool


@dataclass(frozen=True, slots=True)
class Stop:
    """The one reason a run cannot go on, in the words the plan or the record gave."""

    reason: str


@dataclass(frozen=True, slots=True)
class Run:
    """A run's whole account: what it tried, what closed, why it stopped, what it cost."""

    plan: campaign.Campaign
    attempts: tuple[Attempt, ...]
    stop: str
    report: campaign.Report
    path: Path | None
    exit_code: int

    @property
    def closed(self) -> tuple[str, ...]:
        return tuple(item.step.id for item in self.attempts if item.closed)


def step(
    box: boxes.Box,
    plan: campaign.Campaign,
    identifier: str,
    *,
    table: dict[str, tuple[str, ...]],
    trajan_root: Path,
    trajan_python: Path,
    review: contour.Reviewer | None,
    reason: str = "",
    retry: bool = False,
) -> Attempt:
    """Run one named step, or refuse with the reason: the one path ``pantheon step`` takes."""
    found = campaign.problems(plan, box.project, table)
    if found:
        raise boxes.HarnessError("the plan cannot start: " + "; ".join(found))
    chosen = plan.step(identifier)
    if chosen is None:
        raise boxes.HarnessError(
            f"{plan.path}: no step {identifier!r}; the plan has "
            + ", ".join(item.id for item in plan.steps)
        )
    outcome = _attempt(
        box,
        plan,
        chosen,
        table=table,
        trajan_root=trajan_root,
        trajan_python=trajan_python,
        review=review,
        reason=reason,
        retry=retry,
    )
    if isinstance(outcome, Stop):
        raise boxes.HarnessError(outcome.reason)
    return outcome


def run(
    box: boxes.Box,
    plan: campaign.Campaign,
    *,
    table: dict[str, tuple[str, ...]],
    trajan_root: Path,
    trajan_python: Path,
    review: contour.Reviewer | None,
    reason: str = "",
) -> Run:
    """Advance the campaign by its own rule until it is done, or until a reason stops it."""
    if plan.mode != "auto":
        return Run(
            plan=plan,
            attempts=(),
            stop=(
                "campaign mode is manual: its steps wait for you, and `pantheon step` runs one; "
                'a run needs mode = "auto"'
            ),
            report=campaign.report(box, plan, routes=table),
            path=None,
            exit_code=EXIT_STOPPED,
        )
    found = campaign.problems(plan, box.project, table)
    if found:
        return Run(
            plan=plan,
            attempts=(),
            stop="the plan cannot start: " + "; ".join(found),
            report=campaign.report(box, plan, routes=table),
            path=None,
            exit_code=EXIT_STOPPED,
        )
    dirty = _dirty(box.project)
    if dirty:
        shown = ", ".join(dirty[:5]) + (f" and {len(dirty) - 5} more" if len(dirty) > 5 else "")
        return Run(
            plan=plan,
            attempts=(),
            stop=(
                f"the project has uncommitted changes ({shown}): a run judges the tree, so it "
                "would judge these too. Commit or revert them first"
            ),
            report=campaign.report(box, plan, routes=table),
            path=None,
            exit_code=EXIT_STOPPED,
        )
    bound = campaign.attempts_allowed(plan)
    attempts: list[Attempt] = []
    stop = ""
    for _index in range(bound):
        report = campaign.report(box, plan, routes=table)
        if report.verdict.status == "complete":
            break
        if report.verdict.status == "over budget":
            stop = "the campaign spent its budget: " + "; ".join(report.verdict.reasons)
            break
        chosen = campaign.next_step(plan, report.state)
        if chosen is None:
            stop = _nothing_ready(plan, report)
            break
        outcome = _attempt(
            box,
            plan,
            chosen,
            table=table,
            trajan_root=trajan_root,
            trajan_python=trajan_python,
            review=review,
            reason=reason,
            retry=True,
        )
        if isinstance(outcome, Stop):
            stop = outcome.reason
            break
        attempts.append(outcome)
        if outcome.closed:
            continue
        if outcome.state.state == "noop":
            stop = (
                f"step {chosen.id} wrote nothing: a run cannot close a step that changes no "
                "files, and the plan declares no executor for it"
            )
            break
        state = campaign.read_state(box, plan)
        assert state is not None, "a recorded attempt implies a state to read it from"
        decision = campaign.retry(box, plan, chosen, state)
        if decision is None:
            stop = f"step {chosen.id} did not close, and the record says why no attempt is next"
            break
        if not decision.allowed:
            stop = decision.reason
            break
    else:
        report = campaign.report(box, plan, routes=table)
        if report.verdict.status != "complete":
            stop = _nothing_ready(plan, report)
    return _finish(box, plan, table, attempts, stop)


def render(result: Run) -> list[str]:
    """A run as a person reads it: what closed, what it cost, where it stopped and why."""
    total = len(result.plan.steps)
    closed = len(result.closed)
    lines = [
        f"run        {result.plan.id}: {result.report.verdict.closed} of {total} steps closed in "
        f"the campaign, {closed} by this run"
    ]
    for item in result.attempts:
        mark = "closed" if item.closed else "not closed"
        reason = item.state.verdict or item.state.state
        lines.append(
            f"attempt    {item.step.id}: {mark} ({reason}), attempt {item.state.attempts} of "
            f"{result.plan.attempts}, {item.state.cents:.2f} cents"
        )
    if result.stop:
        lines.append(f"stop       {result.stop}")
        last = result.attempts[-1].dispatch.notes if result.attempts else ()
        if last:
            lines.append(f"last       {last[-1]}")
    else:
        lines.append("stop       none: every step closed with a strict pass")
    verdict = result.report.verdict
    lines.append(f"verdict    {verdict.status} — {verdict.closed} of {verdict.total} steps closed")
    for reason in verdict.reasons:
        lines.append(f"reason     {reason}")
    if result.path is not None:
        lines.append(f"record     {result.path}")
    return lines


def run_json(result: Run) -> dict[str, object]:
    """The same answer for a machine, including what it decided not to do."""
    verdict = result.report.verdict
    return {
        "campaign": result.plan.id,
        "mode": result.plan.mode,
        "attempts": [
            {
                "step": item.step.id,
                "closed": item.closed,
                "state": item.state.state,
                "verdict": item.state.verdict,
                "cents": item.state.cents,
                "record": item.state.record,
            }
            for item in result.attempts
        ],
        "closed": list(result.closed),
        "stop": result.stop,
        "verdict": verdict.status,
        "cents": 0.0 if result.report.state is None else result.report.state.cents,
        "reasons": list(verdict.reasons),
        "record": None if result.path is None else str(result.path),
        "exit": result.exit_code,
    }


def _attempt(
    box: boxes.Box,
    plan: campaign.Campaign,
    chosen: campaign.Step,
    *,
    table: dict[str, tuple[str, ...]],
    trajan_root: Path,
    trajan_python: Path,
    review: contour.Reviewer | None,
    reason: str,
    retry: bool,
) -> Attempt | Stop:
    """One step through the gates: the same road a dispatched task takes, on the plan's terms."""
    state = campaign.read_state(box, plan)
    waiting = campaign.blockers(plan, chosen, state)
    if waiting:
        return Stop("; ".join(waiting))
    if state is not None and state.plan and state.plan != plan.digest and not reason:
        return Stop(
            "the plan changed since the last step, and the state still says the old one. Say why "
            'with `--reason "..."`: splitting or narrowing a step is Augustus\'s, but acceptance '
            "and the set of steps belong to the owner, and neither may happen silently"
        )
    recorded = None if state is None else state.steps.get(chosen.id)
    if recorded is not None and recorded.state != "closed":
        decision = campaign.retry(box, plan, chosen, state)
        if not retry:
            why = "" if decision is None else " " + decision.reason
            return Stop(
                f"step {chosen.id} already ran ({recorded.state}, "
                f"{recorded.verdict or 'no verdict'}) and is not closed. Another attempt needs "
                f"`--retry`, and the retry rule has to allow it.{why}"
            )
        if decision is None or not decision.allowed:
            why = "the step did not run" if decision is None else decision.reason
            return Stop(f"the retry is refused: {why}")
        assert state is not None, "a recorded step implies a state to read it from"
        draft = campaign.revert_draft(box, plan, chosen, state)
        if draft is not None:
            return Stop(
                f"the draft of the failed attempt could not be reverted ({draft}): a retry "
                "starts from the accepted tree, so commit or remove the draft first"
            )
    # A step is work: the routing table decides which contours check it, not whether anything
    # happens. Without this a docs step would close a clean verification of an untouched tree.
    routes_for_step = {
        **table,
        chosen.kind: (
            *[stage for stage in table[chosen.kind] if stage != "verify"],
            "run",
            "verify",
        ),
    }
    result = augustus.dispatch(
        box,
        campaign.task_for(plan, chosen),
        routes=routes_for_step,
        trajan_root=trajan_root,
        trajan_python=trajan_python,
        review=review,
        no_repeat=campaign.previous_change(state, chosen),
        driven=True,
        previous=campaign.previous_failure(state, chosen),
    )
    updated = campaign.record_step(box, plan, chosen, result, reason=reason)
    record = updated.steps[chosen.id]
    return Attempt(
        step=chosen,
        dispatch=result,
        state=record,
        closed=campaign.closed(updated, chosen),
    )


def _dirty(project: Path) -> tuple[str, ...]:
    """What is not committed in the project: a run must judge the tree it is about to change.

    A stopped campaign leaves its last draft in the tree on purpose, so the owner can read it.
    An automatic run that started on such a tree would verify that draft together with its own
    change and could report a verdict about work nobody asked for. Not being a git repository
    is not a refusal: the checks are what judge the tree, and they will say their own word.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(project), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ()
    if done.returncode != 0:
        return ()
    paths: list[str] = []
    for line in done.stdout.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ")[-1]
        if path:
            paths.append(path)
    return tuple(paths)


def _nothing_ready(plan: campaign.Campaign, report: campaign.Report) -> str:
    """Why no step may run now: a waiting dependency, or a closed plan without an acceptance."""
    waiting = [item for item in plan.steps if not campaign.closed(report.state, item)]
    if not waiting:
        return (
            "every step is closed, but the campaign is not complete: "
            + "; ".join(report.verdict.reasons)
        )
    return "no step is ready: " + "; ".join(campaign.blockers(plan, waiting[0], report.state))


def _finish(
    box: boxes.Box,
    plan: campaign.Campaign,
    table: dict[str, tuple[str, ...]],
    attempts: list[Attempt],
    stop: str,
) -> Run:
    """Write down what the run did — a record and one ledger line — and only then report it."""
    report = campaign.report(box, plan, routes=table)
    path = _write_run(box, plan, attempts, stop, report)
    kind = "campaign.completed" if not stop else "campaign.stopped"
    reason = stop or f"{len(attempts)} attempt(s) closed the campaign"
    campaign.note(box, plan, kind, reason)
    complete = not stop and report.verdict.status == "complete"
    return Run(
        plan=plan,
        attempts=tuple(attempts),
        stop=stop,
        report=report,
        path=path,
        exit_code=0 if complete else EXIT_STOPPED,
    )


def _write_run(
    box: boxes.Box,
    plan: campaign.Campaign,
    attempts: list[Attempt],
    stop: str,
    report: campaign.Report,
) -> Path:
    """The run as a file: a person can read it after the fact, and a test can point at it."""
    moment = datetime.now(UTC)
    directory = box.root / RUN_DIR / plan.id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run-{moment:%Y%m%dT%H%M%SZ}.json"
    data = {
        "schema_version": 1,
        "campaign": plan.id,
        "at": moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "plan": plan.digest,
        "mode": plan.mode,
        "attempts": [
            {
                "step": item.step.id,
                "state": item.state.state,
                "verdict": item.state.verdict,
                "cents": item.state.cents,
                "attempts": item.state.attempts,
                "closed": item.closed,
                "record": item.state.record,
            }
            for item in attempts
        ],
        "closed": [item.step.id for item in attempts if item.closed],
        "stop": stop,
        "verdict": report.verdict.status,
        "cents": 0.0 if report.state is None else report.state.cents,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
