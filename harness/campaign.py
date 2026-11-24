"""The campaign: the plan a project is built by, and the verdict the code computes about it.

A campaign is a file in the repository — the promise — plus a state in the box — what actually
happened. The file names the frame it was planned against, the steps, the acceptance, and the
budget; the state names which steps closed and what they cost. Neither is the verdict: the
verdict is computed from both, so "the campaign is done" is a claim with a program behind it,
not a note somebody left.

What the code can check, it checks: steps exist once, dependencies form no cycle, every step's
paths stay inside what its kind owns, the acceptance names steps that exist, the budget is a
number. What it cannot check it says out loud — a free-text acceptance statement is covered by
the verdict of the step it names, and nothing here pretends to read prose.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import augustus, caesar, frame
from . import box as boxes

SCHEMA_VERSION = 1
CAMPAIGN_DIR = Path(".pantheon") / "campaigns"
STATE_DIR = Path("artifacts") / "campaign"
"""The file lives in the project, the state in the box: a promise and a record, different places."""
CAMPAIGN_KEYS = frozenset(
    {"schema_version", "id", "goal", "frame", "acceptance", "budget", "step", "mode", "attempts"}
)
DEFAULT_ATTEMPTS = 1
"""Without a declared ceiling there is no retry: one attempt is the safe default, not a habit."""
STEP_KEYS = frozenset(
    {"id", "goal", "kind", "role", "paths", "expected", "depends_on", "max_cents"}
)
ACCEPTANCE_KEYS = frozenset({"id", "statement", "step"})
BUDGET_KEYS = frozenset({"max_cents", "max_seconds"})
STATE_KEYS = frozenset(
    {"schema_version", "campaign", "at", "plan", "steps", "spend", "revisions"}
)
STEP_STATES = frozenset({"closed", "noop", "returned", "refused", "running"})
"""``noop`` is a step that closed a verification without writing anything: not work, so not done."""
VERDICTS = frozenset({"pass", "pass_with_conditions", "hold", "inconclusive", "retry"})


@dataclass(frozen=True, slots=True)
class Step:
    """One declared piece of work: what it is, how far it may reach, what must come first."""

    id: str
    goal: str
    kind: str
    paths: tuple[str, ...]
    expected: str
    role: str
    depends_on: tuple[str, ...]
    max_cents: int | None


@dataclass(frozen=True, slots=True)
class Acceptance:
    """A promise about the whole campaign, covered by the verdict of the step that proves it."""

    id: str
    statement: str
    step: str


@dataclass(frozen=True, slots=True)
class Campaign:
    """The plan as declared in the repository: what it promises, against which frame."""

    path: Path
    id: str
    goal: str
    frame: str | None
    acceptance: tuple[Acceptance, ...]
    budget: dict[str, int]
    steps: tuple[Step, ...]
    digest: str
    mode: str = "manual"
    """Who closes a step: a person after the verdict, or a runner that does not exist yet."""
    attempts: int = DEFAULT_ATTEMPTS
    """How many attempts one step may take. The ceiling is a fuse, not a target."""

    def step(self, identifier: str) -> Step | None:
        return next((item for item in self.steps if item.id == identifier), None)


@dataclass(frozen=True, slots=True)
class StepState:
    """What the box says happened to one step."""

    state: str
    verdict: str | None
    cents: float
    at: str
    record: str
    attempts: int = 1
    stop: str = ""
    """Why no further attempt is allowed, when the box already knows it."""


@dataclass(frozen=True, slots=True)
class State:
    """The execution record of a campaign: which steps closed, what they cost, when."""

    path: Path
    campaign: str
    steps: dict[str, StepState]
    cents: float
    at: str
    plan: str = ""
    revisions: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the code concludes about the campaign, and why — never a status without reasons."""

    status: str  # "ready", "in progress", "incomplete", "complete", "blocked", "over budget"
    reasons: tuple[str, ...] = ()
    closed: int = 0
    total: int = 0


@dataclass(frozen=True, slots=True)
class Retry:
    """Whether one more attempt is allowed, and the single reason that decides it."""

    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Report:
    """Everything a person reads about one campaign, gathered once."""

    campaign: Campaign
    standing: frame.Standing
    state: State | None
    problems: tuple[str, ...]
    verdict: Verdict
    missing_steps: tuple[str, ...] = field(default=())
    retries: dict[str, Retry] = field(default_factory=dict)
    """For every step that ran and is not closed: what the code says about another attempt."""


def campaign_files(project: Path) -> tuple[Path, ...]:
    """Every campaign the project declares, by name: a plan is a file, not a conversation."""
    directory = project / CAMPAIGN_DIR
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("*.toml")))


def read_campaign(path: Path) -> Campaign:
    """Load a campaign; unknown keys are errors, because they would be rules nobody obeys."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise boxes.HarnessError(f"no campaign at {path}") from error
    except OSError as error:
        raise boxes.HarnessError(f"cannot read the campaign {path}: {error}") from error
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"{path} does not parse: {error}") from error
    unknown = set(data) - CAMPAIGN_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    identifier = _identifier(data.get("id"), path, "campaign")
    steps = tuple(
        _read_step(path, entry, index) for index, entry in enumerate(_tables(data, path))
    )
    acceptance = tuple(
        _read_acceptance(path, entry, index)
        for index, entry in enumerate(_tables(data, path, "acceptance"))
    )
    if not steps:
        raise boxes.HarnessError(f"{path}: no [[step]] entries: a plan without steps plans nothing")
    if not acceptance:
        raise boxes.HarnessError(
            f"{path}: no [[acceptance]] entries: a campaign nobody can accept is not a campaign"
        )
    budget = _read_budget(path, data.get("budget", {}))
    mode = _text(data.get("mode")) or "manual"
    if mode not in augustus.MODES:
        raise boxes.HarnessError(
            f"{path}: mode {mode!r} is not one of {', '.join(augustus.MODES)}"
        )
    attempts = data.get("attempts", DEFAULT_ATTEMPTS)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise boxes.HarnessError(f"{path}: attempts must be a positive whole number")
    return Campaign(
        path=path,
        id=identifier,
        goal=_text(data.get("goal")) or "",
        frame=_text(data.get("frame")),
        acceptance=acceptance,
        budget=budget,
        steps=steps,
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        mode=mode,
        attempts=attempts,
    )


def problems(
    campaign: Campaign, project: Path, routes: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Everything that stops the campaign from starting, all at once: a refusal explains itself."""
    found: list[str] = []
    kinds = caesar.read_kinds(project)
    seen: set[str] = set()
    for step in campaign.steps:
        if step.id in seen:
            found.append(f"step {step.id}: declared twice")
        seen.add(step.id)
        if not step.paths:
            found.append(f"step {step.id}: declares no paths, so nothing is in scope")
        if not routes.get(step.kind, ()):
            known = ", ".join(sorted(routes))
            found.append(
                f"step {step.id}: no route for kind {step.kind!r}; known kinds: {known}"
            )
        if step.role == "architect" and step.kind != "architecture":
            found.append(
                f"step {step.id}: the architect writes the frame, not a {step.kind!r} change"
            )
        if step.kind == "architecture" and step.role != "architect":
            found.append(
                f"step {step.id}: an architecture step is the architect's, not the builder's"
            )
        for path in step.paths:
            found.extend(_outside_kind(step, path, kinds))
    found.extend(_dependency_problems(campaign))
    for item in campaign.acceptance:
        if campaign.step(item.step) is None:
            found.append(f"acceptance {item.id}: names step {item.step!r}, which does not exist")
    return tuple(found)


def state_path(box: boxes.Box, campaign: Campaign) -> Path:
    return box.root / STATE_DIR / campaign.id / "state.json"


def read_state(box: boxes.Box, campaign: Campaign) -> State | None:
    """The execution record, when the box has one. A record that does not parse is an error."""
    path = state_path(box, campaign)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"the campaign state {path} does not parse: {error}") from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"the campaign state {path} is not a JSON object")
    unknown = set(data) - STATE_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    if data.get("campaign") != campaign.id:
        raise boxes.HarnessError(
            f"{path} is the state of {data.get('campaign')!r}, not of {campaign.id!r}: two plans, "
            "one record, and the record would lie about which"
        )
    steps = _read_steps(path, data.get("steps", {}))
    spend = data.get("spend", {})
    if not isinstance(spend, dict):
        raise boxes.HarnessError(f"{path}: spend must be a table")
    cents = _number(spend, "cents")
    plan = data.get("plan", {})
    digest = ""
    if isinstance(plan, dict):
        digest = str(plan.get("digest", ""))
    return State(
        path=path,
        campaign=campaign.id,
        steps=steps,
        cents=cents,
        at=str(data.get("at", "")),
        plan=digest,
        revisions=_read_revisions(path, data.get("revisions", [])),
    )


def blockers(campaign: Campaign, step: Step, state: State | None) -> tuple[str, ...]:
    """Why a step may not run yet: every dependency has to be closed with a strict pass."""
    found: list[str] = []
    for dependency in step.depends_on:
        recorded = None if state is None else state.steps.get(dependency)
        if recorded is None or recorded.state != "closed" or recorded.verdict != "pass":
            found.append(
                f"step {step.id} waits for {dependency}, which has no strict pass recorded"
            )
    return tuple(found)


def closed(state: State | None, step: Step) -> bool:
    """Whether the record closes the step: only a strict pass closes one, conditions do not."""
    return state is not None and _is_closed(state, step)


def next_step(campaign: Campaign, state: State | None) -> Step | None:
    """The first step the plan says may run now: not closed, and what it waits for holds.

    The order is the plan's own — a step's position in the file is the order the owner chose —
    and a step that waits for a dependency still without a strict pass is skipped, not run
    out of turn.
    """
    for step in campaign.steps:
        if closed(state, step):
            continue
        if not blockers(campaign, step, state):
            return step
    return None


def attempts_allowed(campaign: Campaign) -> int:
    """The most attempts a run may spend: every step may use the ceiling the plan declares."""
    return len(campaign.steps) * campaign.attempts


def task_for(campaign: Campaign, step: Step) -> augustus.Task:
    """The step as the task Augustus dispatches: a plan step is work, not a second plan."""
    budget: dict[str, int] = {}
    if step.max_cents is not None:
        budget["max_cents"] = step.max_cents
    return augustus.Task(
        path=campaign.path,
        id=f"{campaign.id}-{step.id}",
        goal=step.goal,
        kind=step.kind,
        paths=step.paths,
        expected=step.expected or None,
        change=None,
        forbidden=(),
        budget=budget,
        mode=campaign.mode,
        role=step.role,
    )


def record_step(
    box: boxes.Box,
    campaign: Campaign,
    step: Step,
    result: augustus.Dispatch,
    *,
    reason: str = "",
    when: datetime | None = None,
) -> State:
    """Write what the box learned: the step's outcome, its bill, and any plan revision.

    The plan's digest is recorded with every step. When a plan changes between two steps, the
    state says so and keeps the reason a person gave; a plan that changes silently is exactly
    the "we did something else" that a campaign exists to prevent.
    """
    stamp = _stamp(when)
    previous = read_state(box, campaign)
    steps = dict(previous.steps) if previous is not None else {}
    before = None if previous is None else previous.steps.get(step.id)
    attempts = (before.attempts if before is not None else 0) + 1
    stop = ""
    if result.repeated:
        stop = (
            "the executor proposed the same change as the last attempt: another attempt would "
            "pay twice for the same answer"
        )
    steps[step.id] = StepState(
        state=_result_state(result),
        verdict=None if result.verify is None else result.verify.status,
        cents=_spend(result),
        at=stamp,
        record=_record_path(result),
        attempts=attempts,
        stop=stop,
    )
    cents = (previous.cents if previous is not None else 0.0) + _spend(result)
    revisions = list(previous.revisions) if previous is not None else []
    if previous is not None and previous.plan and previous.plan != campaign.digest and reason:
        revisions.append(
            {"at": stamp, "from": previous.plan, "to": campaign.digest, "reason": reason}
        )
    state = State(
        path=state_path(box, campaign),
        campaign=campaign.id,
        steps=steps,
        cents=cents,
        at=stamp,
        plan=campaign.digest,
        revisions=tuple(revisions),
    )
    _write_state(state)
    _append_event(box, campaign, step, state.steps[step.id], stamp)
    return state


def verdict(campaign: Campaign, standing: frame.Standing, state: State | None) -> Verdict:
    """The campaign's answer, computed from its plan, the frame and what the box recorded.

    Green steps are necessary and not sufficient: a campaign is complete when every step closed
    with a strict ``pass``, every acceptance item's step did, the frame is the approved one, and
    the money stayed inside the budget. Everything else is named, because "not done" with a
    reason is a status a person can act on.
    """
    total = len(campaign.steps)
    if state is None:
        return Verdict(
            "ready",
            ("nothing has run yet: the plan is declared and the box has no step recorded",),
            0,
            total,
        )
    recorded = [step for step in campaign.steps if step.id in state.steps]
    green = [step for step in recorded if _is_closed(state, step)]
    reasons: list[str] = []
    missing = [step.id for step in campaign.steps if step.id not in state.steps]
    if missing:
        reasons.append("steps not started: " + ", ".join(missing))
    unwritten = [step.id for step in recorded if state.steps[step.id].state == "noop"]
    if unwritten:
        reasons.append("steps that wrote nothing: " + ", ".join(unwritten))
    unconvinced = [
        f"{step.id} ({state.steps[step.id].verdict or state.steps[step.id].state})"
        for step in recorded
        if not _is_closed(state, step) and step.id not in unwritten
    ]
    if unconvinced:
        reasons.append("steps without a strict pass: " + ", ".join(unconvinced))
    every_step_recorded = len(recorded) == total
    if every_step_recorded:
        for item in campaign.acceptance:
            step_state = state.steps.get(item.step)
            if step_state is None or step_state.verdict != "pass":
                reasons.append(
                    f"acceptance {item.id} rests on step {item.step}, which has no strict pass"
                )
        if standing.state != "approved":
            reasons.append(standing.gap() or f"the frame is {standing.state}")
        budget = campaign.budget.get("max_cents")
        if budget is not None and state.cents > budget:
            reasons.append(f"the campaign spent {state.cents:.2f} cents of the {budget} declared")
    budget = campaign.budget.get("max_cents")
    if budget is not None and state.cents > budget:
        return Verdict("over budget", tuple(reasons), len(green), total)
    if every_step_recorded and not reasons:
        return Verdict("complete", (), len(green), total)
    if every_step_recorded:
        return Verdict("incomplete", tuple(reasons), len(green), total)
    return Verdict("in progress", tuple(reasons), len(green), total)


def retry(box: boxes.Box, campaign: Campaign, step: Step, state: State | None) -> Retry | None:
    """The retry rule of D20 as code: one more attempt, or the one reason it is refused.

    ``None`` means there is nothing to retry: the step has not run, or it already closed. A
    decision made earlier stands: a recorded stop is not reconsidered. Then come the fuses —
    the plan's attempt ceiling and the campaign's budget — and only then the verdict: another
    attempt is allowed when the verdict names a place inside the declared paths, because that
    is the only kind of failure a change inside those paths can answer.
    """
    if state is None:
        return None
    recorded = state.steps.get(step.id)
    if recorded is None or recorded.state == "closed":
        return None
    if recorded.stop:
        return Retry(False, recorded.stop)
    if recorded.state == "noop":
        return Retry(True, "the step wrote nothing: the work still has to be done")
    if recorded.attempts >= campaign.attempts:
        return Retry(
            False,
            f"attempts {recorded.attempts} of {campaign.attempts} used: the plan allows no more",
        )
    budget = campaign.budget.get("max_cents")
    if budget is not None and state.cents >= budget:
        return Retry(
            False, f"the campaign spent {state.cents:.2f} cents of the {budget} declared"
        )
    record = _read_json(recorded.record)
    if record is None:
        return Retry(
            False, "the step's dispatch record cannot be read: nothing says what to retry"
        )
    verdict = recorded.verdict
    if verdict is None:
        executor = record.get("executor")
        if isinstance(executor, dict) and executor.get("status") == "failed":
            broken = str(executor.get("reason") or "the executor produced no change")
            return Retry(
                True,
                f"the executor failed ({broken}): no change was proposed, so a technical retry "
                "is allowed",
            )
        return Retry(
            False,
            "the last attempt produced no verdict: the gate refused the change, and another "
            "attempt would repeat it",
        )
    if verdict == "inconclusive":
        return Retry(True, "the verdict is inconclusive: a technical retry is allowed")
    findings = _findings(record)
    outside = [name for name, _title, _kind in findings if name and not _inside(name, step.paths)]
    if outside:
        return Retry(
            False,
            f"the finding points at {outside[0]}, outside the declared paths: the plan or the "
            "frame has to change, not the attempt",
        )
    placed = [item for item in findings if item[0]]
    if placed:
        return Retry(True, f"the verdict names {len(placed)} finding(s) inside the declared paths")
    failed = [item for item in findings if item[2] == "check-failure"]
    if failed:
        return Retry(True, f"{len(failed)} check(s) failed on the declared change")
    return Retry(False, "the verdict names no place inside the declared paths: a retry would guess")


def previous_patch(state: State | None, step: Step) -> Path | None:
    """The failed attempt's recorded change: what a retry reverts before it starts."""
    if state is None:
        return None
    recorded = state.steps.get(step.id)
    if recorded is None:
        return None
    record = _read_json(recorded.record)
    if record is None:
        return None
    attempt = record.get("attempt")
    if not isinstance(attempt, dict):
        return None
    directory = attempt.get("directory")
    if not isinstance(directory, str) or not directory:
        return None
    patch = Path(directory) / "change.diff"
    return patch if patch.is_file() else None


def revert_draft(
    box: boxes.Box, campaign: Campaign, step: Step, state: State, when: datetime | None = None
) -> str | None:
    """Undo the failed attempt's draft before a retry; returns why it could not, or ``None``.

    A retry starts from the accepted tree: the change that did not pass is in the record, not
    under the next attempt's feet. Without this a retry's commit would be built on a tree that
    no longer matches the base the patch was written against.
    """
    patch = previous_patch(state, step)
    if patch is None:
        return None
    done, detail = caesar.revert(box.project, patch)
    if not done:
        return detail
    _event(
        box,
        "step.draft-reverted",
        f"{campaign.id}-{step.id}",
        f"the draft of the failed attempt was reverted: {patch}",
        str(patch),
        when,
    )
    return None


def previous_change(state: State | None, step: Step) -> str | None:
    """The digest of the change the last attempt applied: the retry's no-repeat fuse."""
    if state is None:
        return None
    recorded = state.steps.get(step.id)
    if recorded is None:
        return None
    record = _read_json(recorded.record)
    if record is None:
        return None
    attempt = record.get("attempt")
    if not isinstance(attempt, dict):
        return None
    digest = attempt.get("patch_digest")
    return digest if isinstance(digest, str) and digest else None


def report(
    box: boxes.Box,
    campaign: Campaign,
    *,
    routes: dict[str, tuple[str, ...]],
    standing: frame.Standing | None = None,
) -> Report:
    """Check the plan and read its record once, so every caller sees the same facts."""
    found = problems(campaign, box.project, routes)
    current = standing if standing is not None else frame.standing(box)
    state = read_state(box, campaign)
    if found:
        return Report(
            campaign, current, state, found, Verdict("blocked", (), 0, len(campaign.steps)), ()
        )
    result = verdict(campaign, current, state)
    decisions = {
        step.id: decision
        for step in campaign.steps
        if (decision := retry(box, campaign, step, state)) is not None
    }
    return Report(
        campaign=campaign,
        standing=current,
        state=state,
        problems=(),
        verdict=result,
        retries=decisions,
    )


def render(report: Report) -> list[str]:
    """One campaign as a person reads it: the plan, the basis, the steps, the verdict."""
    campaign = report.campaign
    lines = [
        f"campaign   {campaign.id}  ({campaign.path})",
        f"goal       {campaign.goal or 'not declared'}",
        f"frame      {report.standing.line()}",
    ]
    if campaign.frame:
        matches = campaign.frame == report.standing.digest
        lines.append(
            f"planned    against {campaign.frame}"
            + ("" if matches else " — not the frame on disk now")
        )
    per = "attempt" if campaign.attempts == 1 else "attempts"
    lines.append(f"mode       {campaign.mode}, {campaign.attempts} {per} per step")
    lines.append(f"steps      {len(campaign.steps)}")
    for step in campaign.steps:
        state = report.state.steps.get(step.id) if report.state is not None else None
        mark = "not started"
        if state is not None:
            mark = f"{state.state} ({state.verdict})" if state.verdict else state.state
            mark += f", attempt {state.attempts} of {campaign.attempts}"
        depends = f" after {', '.join(step.depends_on)}" if step.depends_on else ""
        lines.append(f"  {step.id:<10} {step.kind:<14} {mark}{depends}")
        decision = report.retries.get(step.id)
        if decision is not None:
            verdict = "retry" if decision.allowed else "stop"
            lines.append(f"    next {step.id}: {verdict} — {decision.reason}")
    for item in campaign.acceptance:
        state = report.state.steps.get(item.step) if report.state is not None else None
        satisfied = state is not None and state.verdict == "pass"
        lines.append(
            f"accept     {item.id}: {item.statement}"
            f" — {'satisfied' if satisfied else 'not satisfied'} by {item.step}"
        )
    if campaign.budget:
        spent = 0.0 if report.state is None else report.state.cents
        money = campaign.budget.get("max_cents")
        lines.append(
            f"budget     {spent:.2f} cents"
            + (f" of {money}" if money is not None else " (no cent ceiling declared)")
        )
    result = report.verdict
    head = f"{result.status} — {result.closed} of {result.total} steps closed"
    lines.append(f"verdict    {head}")
    for problem in report.problems:
        lines.append(f"problem    {problem}")
    for reason in result.reasons:
        lines.append(f"reason     {reason}")
    return lines


def report_json(report: Report) -> dict[str, object]:
    """The same answer for a machine."""
    campaign = report.campaign
    state = report.state
    return {
        "campaign": campaign.id,
        "goal": campaign.goal,
        "path": str(campaign.path),
        "digest": campaign.digest,
        "mode": campaign.mode,
        "attempts": campaign.attempts,
        "frame": {
            "planned_against": campaign.frame,
            "standing": report.standing.json(),
        },
        "problems": list(report.problems),
        "steps": [
            {
                "id": step.id,
                "kind": step.kind,
                "role": step.role,
                "paths": list(step.paths),
                "depends_on": list(step.depends_on),
                "state": None if state is None or step.id not in state.steps else {
                    "state": state.steps[step.id].state,
                    "verdict": state.steps[step.id].verdict,
                    "cents": state.steps[step.id].cents,
                    "attempts": state.steps[step.id].attempts,
                    "stop": state.steps[step.id].stop,
                },
                "next": None
                if step.id not in report.retries
                else {
                    "allowed": report.retries[step.id].allowed,
                    "reason": report.retries[step.id].reason,
                },
            }
            for step in campaign.steps
        ],
        "acceptance": [
            {
                "id": item.id,
                "statement": item.statement,
                "step": item.step,
                "satisfied": state is not None
                and item.step in state.steps
                and state.steps[item.step].verdict == "pass",
            }
            for item in campaign.acceptance
        ],
        "budget": dict(campaign.budget),
        "spend": None if state is None else state.cents,
        "verdict": {
            "status": report.verdict.status,
            "reasons": list(report.verdict.reasons),
            "closed": report.verdict.closed,
            "total": report.verdict.total,
        },
    }


def _read_step(path: Path, entry: object, index: int) -> Step:
    where = f"{path}: step #{index + 1}"
    if not isinstance(entry, dict):
        raise boxes.HarnessError(f"{where} must be a table")
    unknown = set(entry) - STEP_KEYS
    if unknown:
        raise boxes.HarnessError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    identifier = _identifier(entry.get("id"), path, f"step #{index + 1}")
    paths = entry.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise boxes.HarnessError(f"{where}: paths must be a list of glob strings")
    depends = entry.get("depends_on", [])
    if not isinstance(depends, list) or not all(isinstance(item, str) for item in depends):
        raise boxes.HarnessError(f"{where}: depends_on must be a list of step ids")
    role = _text(entry.get("role")) or "executor"
    if role not in augustus.ROLES:
        raise boxes.HarnessError(
            f"{where}: role {role!r} is not one of {', '.join(augustus.ROLES)}"
        )
    kind = _text(entry.get("kind")) or ""
    if not kind:
        raise boxes.HarnessError(f"{where}: kind is required")
    max_cents = entry.get("max_cents")
    if max_cents is not None and (
        isinstance(max_cents, bool) or not isinstance(max_cents, int) or max_cents <= 0
    ):
        raise boxes.HarnessError(f"{where}: max_cents must be a positive whole number")
    return Step(
        id=identifier,
        goal=_text(entry.get("goal")) or "",
        kind=kind,
        paths=tuple(item.strip() for item in paths),
        expected=_text(entry.get("expected")) or "",
        role=role,
        depends_on=tuple(item.strip() for item in depends),
        max_cents=max_cents,
    )


def _read_acceptance(path: Path, entry: object, index: int) -> Acceptance:
    where = f"{path}: acceptance #{index + 1}"
    if not isinstance(entry, dict):
        raise boxes.HarnessError(f"{where} must be a table")
    unknown = set(entry) - ACCEPTANCE_KEYS
    if unknown:
        raise boxes.HarnessError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    identifier = _identifier(entry.get("id"), path, f"acceptance #{index + 1}")
    statement = _text(entry.get("statement"))
    if not statement:
        raise boxes.HarnessError(f"{where}: statement is required — acceptance is a sentence")
    step = _text(entry.get("step"))
    if not step:
        raise boxes.HarnessError(
            f"{where}: step is required — acceptance is covered by the verdict of a step"
        )
    return Acceptance(id=identifier, statement=statement, step=step)


def _read_budget(path: Path, data: object) -> dict[str, int]:
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path}: budget must be a table")
    unknown = set(data) - BUDGET_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: budget takes only {', '.join(sorted(BUDGET_KEYS))}")
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise boxes.HarnessError(f"{path}: budget.{key} must be a positive whole number")
    return {str(key): int(value) for key, value in data.items()}


def _read_steps(path: Path, data: object) -> dict[str, StepState]:
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path}: steps must be a table of step records")
    found: dict[str, StepState] = {}
    for identifier, entry in data.items():
        if not isinstance(entry, dict):
            raise boxes.HarnessError(f"{path}: step record {identifier!r} must be a table")
        state = str(entry.get("state", ""))
        if state not in STEP_STATES:
            raise boxes.HarnessError(
                f"{path}: step {identifier!r} has state {state!r}, which is not one of "
                f"{', '.join(sorted(STEP_STATES))}"
            )
        verdict = entry.get("verdict")
        if verdict is not None and verdict not in VERDICTS:
            raise boxes.HarnessError(
                f"{path}: step {identifier!r} has verdict {verdict!r}, which the checks do not use"
            )
        attempts = entry.get("attempts", 1)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise boxes.HarnessError(
                f"{path}: step {identifier!r} attempts must be a positive whole number"
            )
        found[str(identifier)] = StepState(
            state=state,
            verdict=None if verdict is None else str(verdict),
            cents=_number(entry, "cents"),
            at=str(entry.get("at", "")),
            record=str(entry.get("record", "")),
            attempts=attempts,
            stop=str(entry.get("stop", "")),
        )
    return found


def _result_state(result: augustus.Dispatch) -> str:
    """A step closes only if it changed something: a green check about no work is not the step.

    The dispatch may legitimately close a verification-only task; a campaign step promises work,
    so "nothing was written" is recorded as ``noop`` and the campaign does not count it.
    """
    if result.state == "refused":
        return "refused"
    if result.state == "closed":
        applied = result.attempt is not None and result.attempt.applied
        return "closed" if applied else "noop"
    return "returned"


def _spend(result: augustus.Dispatch) -> float:
    """What this step cost: the reading and the executor are two bills, both counted."""
    total = 0.0
    review = result.verify.review if result.verify is not None else None
    if review is not None and review.cost_cents is not None:
        total += review.cost_cents
    usage = result.proposal.usage if result.proposal is not None else None
    if usage is not None and usage.cost_cents is not None:
        total += usage.cost_cents
    return round(total, 4)


def _record_path(result: augustus.Dispatch) -> str:
    records = sorted(result.directory.glob("dispatch-*.json"))
    return str(records[-1]) if records else str(result.directory)


def _write_state(state: State) -> None:
    """Written whole and swapped in: a half-written state would be a record that lies."""
    path = state.path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": SCHEMA_VERSION,
        "campaign": state.campaign,
        "at": state.at,
        "plan": {"digest": state.plan},
        "steps": {
            identifier: {
                "state": item.state,
                "verdict": item.verdict,
                "cents": item.cents,
                "at": item.at,
                "record": item.record,
                "attempts": item.attempts,
                "stop": item.stop,
            }
            for identifier, item in state.steps.items()
        },
        "spend": {"cents": state.cents, "seconds": 0},
        "revisions": list(state.revisions),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append_event(
    box: boxes.Box,
    campaign: Campaign,
    step: Step,
    state: StepState,
    when: str,
) -> None:
    ledger = box.root / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when[:10]}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    event = {
        "id": f"e-{when[:10]}-{len(existing) + 1}",
        "type": f"step.{state.state}",
        "at": when,
        "task": f"{campaign.id}-{step.id}",
        "actor": "augustus",
        "reason": state.verdict or "",
        "links": [state.record],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _read_json(path: object) -> dict[str, object] | None:
    """A box record by path, or ``None``: a missing record is a gap, not a crash."""
    if not isinstance(path, str) or not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _findings(record: dict[str, object]) -> list[tuple[str, str, str]]:
    """The report's findings as ``(file, title, kind)``: the place is what the retry rule reads."""
    verify = record.get("verify")
    path = verify.get("report") if isinstance(verify, dict) else None
    data = _read_json(path)
    if data is None:
        return []
    items = data.get("findings")
    if not isinstance(items, list):
        return []
    found: list[tuple[str, str, str]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") == "false-positive":
            continue
        location = item.get("location")
        name = ""
        if isinstance(location, dict):
            value = location.get("file")
            name = value if isinstance(value, str) else ""
        found.append((name, str(item.get("title", "")), str(item.get("kind", ""))))
    return found


def _inside(path: str, patterns: tuple[str, ...]) -> bool:
    return any(caesar.matches(pattern, path) for pattern in patterns)


def _event(
    box: boxes.Box,
    kind: str,
    task: str,
    reason: str,
    link: str,
    when: datetime | None = None,
) -> None:
    """One ledger line for an action on the project that is not a step outcome."""
    stamp = _stamp(when)
    ledger = box.root / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{stamp[:10]}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    event = {
        "id": f"e-{stamp[:10]}-{len(existing) + 1}",
        "type": kind,
        "at": stamp,
        "task": task,
        "actor": "augustus",
        "reason": reason,
        "links": [link],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def note(box: boxes.Box, campaign: Campaign, kind: str, reason: str) -> None:
    """One ledger line for an event above the step: a run that finished, or one that stopped."""
    _event(box, kind, campaign.id, reason, str(campaign.path))


def _read_revisions(path: Path, data: object) -> tuple[dict[str, str], ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise boxes.HarnessError(f"{path}: revisions must be a list")
    found: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            raise boxes.HarnessError(f"{path}: every revision must be a table")
        found.append({str(key): str(value) for key, value in item.items()})
    return tuple(found)


def _is_closed(state: State, step: Step) -> bool:
    recorded = state.steps.get(step.id)
    return recorded is not None and recorded.state == "closed" and recorded.verdict == "pass"


def _dependency_problems(campaign: Campaign) -> list[str]:
    found: list[str] = []
    known = {step.id for step in campaign.steps}
    for step in campaign.steps:
        for dependency in step.depends_on:
            if dependency == step.id:
                found.append(f"step {step.id}: depends on itself")
            elif dependency not in known:
                found.append(f"step {step.id}: depends on {dependency!r}, which does not exist")
    found.extend(_cycles(campaign))
    return found


def _cycles(campaign: Campaign) -> list[str]:
    """A plan that cannot start is a plan, not a bug report: name the loop, not the symptom."""
    known = {item.id for item in campaign.steps}
    edges = {
        step.id: tuple(dep for dep in step.depends_on if dep in known) for step in campaign.steps
    }
    done: set[str] = set()
    path: list[str] = []

    def walk(node: str) -> None:
        if node in path:
            loop = [*path[path.index(node) :], node]
            found.add(" → ".join(loop))
            return
        if node in done:
            return
        path.append(node)
        for target in edges.get(node, ()):
            walk(target)
        path.pop()
        done.add(node)

    found: set[str] = set()
    for step in campaign.steps:
        walk(step.id)
    return [f"steps depend on each other in a loop: {loop}" for loop in sorted(found)]


def _outside_kind(step: Step, path: str, kinds: dict[str, caesar.Kind]) -> list[str]:
    kind = kinds.get(step.kind, caesar.ANY_KIND)
    found: list[str] = []
    if kind.allow and not any(caesar.matches(pattern, path) for pattern in kind.allow):
        found.append(f"step {step.id}: path {path} is not what kind {step.kind!r} owns")
    if any(caesar.matches(pattern, path) for pattern in kind.forbid):
        found.append(f"step {step.id}: path {path} is never written by kind {step.kind!r}")
    return found


def _tables(data: dict[str, object], path: Path, key: str = "step") -> list[object]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise boxes.HarnessError(f"{path}: {key} must be a list of tables ([[{key}]])")
    return list(value)


def _identifier(value: object, path: Path, what: str) -> str:
    text = _text(value)
    if not text:
        raise boxes.HarnessError(f"{path}: {what} needs an id")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")
    if not set(text) <= allowed:
        raise boxes.HarnessError(
            f"{path}: {what} id {text!r} must be lowercase letters, digits, '-', '_' or '.'"
        )
    return text


def _number(data: dict[str, object], key: str) -> float:
    value = data.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise boxes.HarnessError(f"state field {key!r} must be a non-negative number")
    return float(value)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _stamp(when: datetime | None = None) -> str:
    moment = when or datetime.now(UTC)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
