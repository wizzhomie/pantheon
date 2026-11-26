"""Augustus: the task goes through the box, and the box's record says what happened.

A task is a file a person (or, later, an agent) writes: a goal, a declared scope, a kind, a
criterion, and the change it expects. Augustus does not invent work and does not judge the
answer — it validates the task, picks contours by the declared fields through a table, calls
them in order (architecture, the write gate, verification), and writes the projection: one
record per dispatch, one summary per finished task in ``acta/``, events in the ledger.

What is missing is named, not hidden: there is no executor yet, so a task either carries the
patch it wants applied (``change = "..."``) or explicitly says the change is not the subject
of this dispatch. The outbox says which of the two happened, because "nothing was run" and
"everything passed" must never look the same in the record.
"""

from __future__ import annotations

import json
import re
import shlex
import time
import tomllib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import (
    archivum,
    aurelius,
    caesar,
    contour,
    contradiction,
    debt,
    executor,
    frame,
    rails,
    records,
    sensitivity,
)
from . import box as boxes
from . import context as context_pack

SCHEMA_VERSION = 1
TASK_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "goal",
        "kind",
        "paths",
        "expected",
        "contract",
        "change",
        "forbidden",
        "budget",
        "mode",
        "role",
        "records",
        "milestone",
        "outside_plan",
        "repro",
        "self_check",
        "shape",
        "max_steps",
        "parent",
        "why",
    }
)
MODES = ("manual", "auto")
"""How a step closes: a person after the verdict, or the campaign itself. Only manual runs."""
ROLES = ("executor", "architect")
"""Who is asked for the change: the builder, or the advisor that writes the frame and no code."""
BUDGET_KEYS = frozenset({"max_seconds", "max_cents", "max_read_chars"})
FAILURE_INCOMPLETE = "incomplete"
FAILURE_NARROWED = "narrowed"
FAILURE_ARCHITECTURE = "architecture"
RETRYABLE_FAILURES = frozenset({FAILURE_INCOMPLETE, FAILURE_NARROWED})
"""The failures a second attempt may address: the others are not about the change."""
REPAIR_ATTEMPTS = 2
"""How many times one task is asked: the second is paid only for a retryable class."""
SENSITIVITY_PIECES = 3
"""Inside a dispatch the reversal is the smaller run: the verdict belongs to the checks."""
SENSITIVITY_SECONDS = 60
"""One check run of the rehearsal: a check slower than this says more about its cost."""
SENSITIVITY_TOTAL_SECONDS = 90
"""The clock for the whole rehearsal, so a dispatch cannot outlive the verdict it informs."""
CONTRACT_KEYS = frozenset({"symptom", "visible", "boundaries", "missing"})
"""The four points of a task contract besides the expected behaviour, which stays `expected`."""
ROUTES_ENV = "PANTHEON_ROUTES"
SNAPSHOT_FILE = "task.json"
"""The task as dispatched, frozen beside its artifacts: a resume reads this, not the file."""

SELF_CHECK_FILE = "self-check.json"
"""The visible check's result, written beside the attempt it judged: two of them are compared."""

STEP_FILE = "step.json"
"""One call of a steps run, written beside its proposal: the gate's answer and the check's words.

The step file is how a resumed loop knows what happened before it: the facts are read from the
records each time instead of carried in memory, so a run that died mid-loop continues from the
same history it would have had.
"""

DEFAULT_ROUTES: dict[str, tuple[str, ...]] = {
    "docs": ("verify",),
    "hotfix": ("run", "verify"),
    "bugfix": ("run", "verify"),
    "feature": ("arch", "run", "verify"),
    "architecture": ("arch", "run", "verify"),
    "security": ("arch", "run", "verify"),
}
"""Scope kind to contours. A table, not a model call: the same task must route the same way."""
KIND_HINT = ", ".join(sorted(DEFAULT_ROUTES))


@dataclass(frozen=True, slots=True)
class Contract:
    """The owner's words around the expected behaviour: symptom, visibility, boundaries, gaps.

    The expected behaviour stays the task's own ``expected``: the contract repeats nothing. These
    four points are the frame the criterion must fit into, and ``missing`` is the material an
    escalation is written from — a contract that names what it does not know is honest, not
    incomplete.
    """

    symptom: str | None = None
    visible: str | None = None
    boundaries: str | None = None
    missing: str | None = None


@dataclass(frozen=True, slots=True)
class Task:
    """One declared piece of work: what it is, how far it may reach, what done means."""

    path: Path
    id: str
    goal: str | None
    kind: str | None
    paths: tuple[str, ...]
    expected: str | None
    change: Path | None
    forbidden: tuple[str, ...]
    budget: dict[str, int]
    mode: str = "manual"
    role: str = "executor"
    records: tuple[str, ...] = ()
    """Digest addresses the task names itself: history its paths would not have found."""

    milestone: str | None = None
    """The milestone the work serves; a change either names one or says why it stands out."""

    outside_plan: str | None = None
    """Why this task is outside the plan, when the owner decided it is."""

    repro: tuple[str, ...] = ()
    """The input the task names, as a command: it must fail before the change and pass after it.

    A defect task without this is a task whose "done" nobody can check: a green suite only
    proves what the suite covers, and the defect lives where no test looks (c-d3).

    The model never sees this command: the verdict is the box's, and an answer fitted to the
    words of the check is not a change. What the model sees is ``self_check``.
    """

    shape: str | None = None
    """How big the change is, declared by the task: patch, file, or multi.

    The shape sizes the answer (a patch gets a patch's room) and says which scheme the box
    asks for; a change that does not fit it is a wrong estimate, recorded, not a lost attempt.
    """

    self_check: tuple[str, ...] = ()
    """The check the model is shown, and the box runs beside the verdict — never as the verdict.

    A blind executor answers the task it imagines; a sighted one still cannot fit the answer to
    a check it does not know. The two run the same behaviour over different data: the visible
    one is what the model reads, the hidden one (``repro``) is what decides, and a change that
    satisfies only the visible one is recorded as exactly that.
    """

    max_steps: int | None = None
    """How many calls this task may make: the task's ceiling, lowered by the box's.

    The budget of a task is a total (``budget.max_cents``) and the calls are what spend it;
    a task that has used its calls is stopped before another model call, with the reason in
    the record, instead of being asked again into the same wall.
    """

    parent: str | None = None
    """The task this one succeeds, when the plan changed instead of being rewritten.

    A task is frozen beside its record: widening its scope or budget means a new task that
    names the old one, so "why did this get bigger" never depends on memory. The parent is an
    id, not a copy: the old task keeps its own record and verdict.
    """

    why: str | None = None
    """Why the successor differs: written by ``pantheon amend`` with the diff it recorded."""

    contract: Contract | None = None
    """The owner's words about the defect, beyond the expected behaviour.

    In the file it is a ``[contract]`` table; it freezes with the task, so a dispatch and its
    resumption read the same words even after the original is edited.
    """


@dataclass(frozen=True, slots=True)
class Dispatch:
    """The projection: what each contour said, and what the task's state is."""

    task: Task
    state: str
    stages: tuple[str, ...]
    attempt: caesar.Attempt | None = None
    verify: contour.VerifyResult | None = None
    arch: aurelius.Result | None = None
    outbox: Path | None = None
    directory: Path = Path()
    notes: list[str] = field(default_factory=list)
    exit_code: int = 0
    proposal: executor.Proposal | None = None
    resumed_from: Path | None = None
    review_cap: float | None = None
    """The ceiling the box declared for one reading, when it declared one."""

    review_skipped: str | None = None
    """Why the change was not read by declaration, when the box reads only some paths."""

    commit: caesar.Commit | None = None
    """What became of the commit after the verdict: made, skipped with a reason, or failed."""

    frame: frame.Standing | None = None
    """The architecture basis this dispatch rested on: approved, changed, never, or empty."""

    rails: rails.Check | None = None
    """The milestone this change serves, or why it stands outside the plan."""

    context: context_pack.Pack | None = None
    """What the model was told about the box before it was asked for a change."""

    repeated: str | None = None
    """The digest of a change already tried, when the retry proposed exactly it again."""

    stop_reason: str | None = None
    """Why the task was not allowed to make another call: its budget, its calls, no progress.

    A stop is not a verdict: it says the box refused to pay for another answer, and names the
    fact it rested on. ``state`` is ``stopped`` in that case, and the exit code is 2.
    """

    shape: dict[str, object] | None = None
    """What the task declared the change to be, and the room the box gave the answer."""

    self_check: dict[str, object] | None = None
    """What the check the task shows the model said, when the task names one.

    It is information, never the verdict: the box's own check (``repro``) decides, and the two
    disagreeing is the finding — a model that satisfies only what it can see has fitted its
    answer to the words it was given.
    """

    sensitivity: sensitivity.Report | None = None
    """Whether the task's own check notices the change, reversed piece by piece.

    Information, not a verdict (ADR-047): a blind piece says the check can be green whatever that
    piece says, and the record shows it. Returning the task on a blind piece is a separate
    decision, and this field does not make it.
    """

    engine: str = "one-shot"
    """Which engine ran the task: the box's table decided, and the record must name it."""

    steps: tuple[dict[str, object], ...] = ()
    """The calls of a steps run: what each answer was and what the box did with it.

    A one-shot run has no steps: its single call is already the proposal and the attempt. A
    steps run keeps the short history here so the acta can say "three steps, two applied, one
    refused" without opening every attempt.
    """

    timings: dict[str, float] = field(default_factory=dict)
    """Wall time in seconds per stage and in total, measured around the work, not guessed."""

    level: int | None = None
    """The execution level the route put the task in, when the box declares a policy."""

    route_reason: str = ""
    """Why that level: the shape, the paths, the visible check, or the missing agent."""


@dataclass(frozen=True, slots=True)
class StepsRun:
    """What a loop of calls produced: the steps, the last answer, and why the loop ended.

    ``stop`` is a refusal to pay for another call (budget, calls, no progress); ``terminal`` is
    an answer the loop will not ask past (an unapproved write, a failed call, a wrong shape).
    They are different facts: a stop is the box's decision about money, a terminal is the
    executor's own dead end.
    """

    proposal: executor.Proposal | None = None
    attempts: tuple[caesar.Attempt, ...] = ()
    self_check: dict[str, object] | None = None
    steps: tuple[dict[str, object], ...] = ()
    stop: str | None = None
    repeated: str | None = None
    terminal: str | None = None


def record_addresses(raw: object, where: str) -> tuple[str, ...]:
    """The record addresses a task or step names, checked by shape; the box resolves them.

    The shape is checked here because a typo should cost nothing — the address is resolved
    against the digest before the model is called, and an address the digest does not have is
    a refusal, not a quiet gap in the pack.
    """
    if not isinstance(raw, list):
        raise boxes.HarnessError(
            f"{where}: records must be a list of addresses written as <task>/<stamp>, "
            "as `pantheon records` prints them"
        )
    addresses: list[str] = []
    for item in raw:
        if not isinstance(item, str) or records.parse_address(item) is None:
            raise boxes.HarnessError(
                f"{where}: {item!r} is not a record address; write it as <task>/<stamp>, "
                "as `pantheon records` prints them"
            )
        addresses.append(item)
    return tuple(addresses)


def _command(raw: object, key: str, path: Path) -> list[str]:
    """A check the task names: a command as a list, at least the executable and its argument."""
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise boxes.HarnessError(f"{path}: {key} must be a list of non-empty strings")
    if raw and len(raw) < 2:
        raise boxes.HarnessError(
            f"{path}: {key} must be a command as a list, at least the executable and its "
            "first argument"
        )
    return [item.strip() for item in raw]


def read_task(path: Path) -> Task:
    """Load a task file; unknown keys are an error, a missing scope is a refusal later."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise boxes.HarnessError(f"no task at {path}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"task {path} does not parse: {error}") from error
    unknown = set(data) - TASK_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    identifier = _identifier(data.get("id"), path)
    paths = data.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise boxes.HarnessError(f"{path}: paths must be a list of glob strings")
    forbidden = data.get("forbidden", [])
    if not isinstance(forbidden, list) or not all(isinstance(item, str) for item in forbidden):
        raise boxes.HarnessError(f"{path}: forbidden must be a list of strings")
    budget = data.get("budget", {})
    if not isinstance(budget, dict) or set(budget) - BUDGET_KEYS:
        raise boxes.HarnessError(f"{path}: budget takes only {', '.join(sorted(BUDGET_KEYS))}")
    for key, value in budget.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise boxes.HarnessError(f"{path}: budget.{key} must be a positive whole number")
    change = data.get("change")
    if change is not None and not isinstance(change, str):
        raise boxes.HarnessError(f"{path}: change must be a path to a patch file")
    role = data.get("role", "executor")
    if role not in ROLES:
        raise boxes.HarnessError(
            f"{path}: role {role!r} is not one of {', '.join(ROLES)}: the role decides who is "
            "asked, and a made-up one would ask nobody"
        )
    mode = data.get("mode", "manual")
    if mode not in MODES:
        raise boxes.HarnessError(
            f"{path}: mode {mode!r} is not one of {', '.join(MODES)}: an unknown mode would be "
            "a rule nothing obeys"
        )
    milestone = data.get("milestone")
    outside_plan = data.get("outside_plan")
    for key, value in (("milestone", milestone), ("outside_plan", outside_plan)):
        if value is not None and not isinstance(value, str):
            raise boxes.HarnessError(f"{path}: {key} must be a string")
    if _text(milestone) is not None and _text(outside_plan) is not None:
        raise boxes.HarnessError(
            f"{path}: a task either names a milestone or says why it stands outside the plan, "
            "not both"
        )
    shape = data.get("shape")
    if shape is not None and shape not in executor.SHAPES:
        raise boxes.HarnessError(
            f"{path}: shape {shape!r} is not one of {', '.join(executor.SHAPES)}: the shape "
            "sizes the answer, and a made-up one would size it by nothing"
        )
    repro_raw = _command(data.get("repro", []), "repro", path)
    self_check_raw = _command(data.get("self_check", []), "self_check", path)
    steps_raw = data.get("max_steps")
    max_steps: int | None = None
    if steps_raw is not None:
        if isinstance(steps_raw, bool) or not isinstance(steps_raw, int) or steps_raw <= 0:
            raise boxes.HarnessError(f"{path}: max_steps must be a positive whole number")
        max_steps = steps_raw
    contract = _contract(data.get("contract"), path)
    parent = _text(data.get("parent"))
    why = _text(data.get("why"))
    for key, value in (("parent", data.get("parent")), ("why", data.get("why"))):
        if value is not None and not isinstance(value, str):
            raise boxes.HarnessError(f"{path}: {key} must be a string")
    if parent is None and why is not None:
        raise boxes.HarnessError(
            f"{path}: why names the reason for a successor, but no parent is declared: "
            "a task that stands alone does not need to say what it replaces"
        )
    if parent is not None and why is None:
        raise boxes.HarnessError(
            f"{path}: parent is declared without why: an amendment that does not say what "
            "changed and why is an unrecorded rewrite"
        )
    if parent == identifier:
        raise boxes.HarnessError(f"{path}: a task cannot be its own parent")
    return Task(
        path=path,
        id=identifier,
        goal=_text(data.get("goal")),
        kind=_text(data.get("kind")),
        paths=tuple(item.strip() for item in paths),
        expected=_text(data.get("expected")),
        contract=contract,
        change=Path(change).expanduser() if isinstance(change, str) else None,
        forbidden=tuple(forbidden),
        budget={key: int(value) for key, value in budget.items()},
        mode=str(mode),
        role=str(role),
        records=record_addresses(data.get("records", []), str(path)),
        milestone=_text(milestone),
        outside_plan=_text(outside_plan),
        repro=tuple(item.strip() for item in repro_raw),
        self_check=tuple(item.strip() for item in self_check_raw),
        shape=shape if isinstance(shape, str) else None,
        max_steps=max_steps,
        parent=parent,
        why=why,
    )


def _contract(raw: object, path: Path) -> Contract | None:
    """Read the ``[contract]`` table: the four points the expected behaviour is framed by."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise boxes.HarnessError(
            f"{path}: contract must be a table with {', '.join(sorted(CONTRACT_KEYS))}"
        )
    unknown = set(raw) - CONTRACT_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: contract does not take {', '.join(sorted(unknown))}")
    for key, value in raw.items():
        if value is not None and not isinstance(value, str):
            raise boxes.HarnessError(f"{path}: contract.{key} must be a string")
    points = {key: _text(raw.get(key)) for key in CONTRACT_KEYS}
    if all(value is None for value in points.values()):
        raise boxes.HarnessError(
            f"{path}: contract is empty: name at least one of {', '.join(sorted(CONTRACT_KEYS))}"
        )
    return Contract(**points)


def read_routes(path: Path | None) -> dict[str, tuple[str, ...]]:
    """The routing table: declared kind to contours. Absent means the default table."""
    if path is None:
        return dict(DEFAULT_ROUTES)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise boxes.HarnessError(f"no routing table at {path}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"routing table {path} does not parse: {error}") from error
    routes: dict[str, tuple[str, ...]] = {}
    for kind, stages in data.get("route", {}).items():
        if not isinstance(stages, list) or not all(isinstance(item, str) for item in stages):
            raise boxes.HarnessError(f"{path}: route {kind!r} must be a list of contour names")
        routes[kind] = tuple(stages)
    if not routes:
        raise boxes.HarnessError(f"{path}: no [[route]] entries: an empty table routes nothing")
    return routes


def route(task: Task, routes: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Which contours this task needs; a scope kind without a route is a refusal, not a guess."""
    if task.kind is None:
        return ()
    return routes.get(task.kind, ())


def _is_pattern(path: str) -> bool:
    """Whether a declared path is a pattern; the hotfix lane names exact files instead."""
    return any(char in path for char in "*?[")


def refusal(
    task: Task, routes: dict[str, tuple[str, ...]], *, driven: bool = False
) -> str | None:
    """Why this task may not start, or ``None``. Scope and kind are not optional.

    ``driven`` marks the one honest way an automatic task exists: a campaign runner asked for
    it. A standalone task that asks for the automatic mode is refused, because nothing would
    run it a second time — the declaration would be a promise nobody keeps.
    """
    if not task.kind:
        return "the task declares no kind: a change without a type does not start"
    if not task.paths:
        return "the task declares no paths: nothing is in scope, so nothing may be done"
    if task.kind == "hotfix":
        pattern = next((path for path in task.paths if _is_pattern(path)), None)
        if pattern is not None:
            return (
                f"a hotfix names exact files, not patterns ({pattern!r}): a pattern reads the "
                "whole tree, which is the cost the hotfix lane exists to avoid"
            )
    if not route(task, routes):
        return f"no route for kind {task.kind!r}; known kinds: {', '.join(sorted(routes))}"
    if task.role == "architect" and task.kind != "architecture":
        return (
            f"the architect writes the frame, not a {task.kind!r} change: an architect task is "
            'kind = "architecture"'
        )
    if task.kind == "architecture" and task.role != "architect":
        return (
            "an architecture task belongs to the architect: declare role = \"architect\" so the "
            "box asks for documents and a model, not for code"
        )
    if task.mode == "auto" and not driven:
        return (
            "the task asks for the automatic mode, and automation belongs to a campaign: "
            "`pantheon advance` runs one, while a single dispatch would only look automatic. "
            "Dispatch it as manual, or run its campaign"
        )
    return None


def _step_history(directory: Path) -> tuple[dict[str, object], ...]:
    """The calls already made, as the box recorded them: the history a loop starts from.

    Rebuilt from the proposal directories on every run rather than carried in memory, so a
    loop that died and came back sees the same steps it would have seen. A proposal with no
    step file is a call whose answer was never gated: it is named as unsettled, not as a step.
    """
    if not directory.is_dir():
        return ()
    after = _retry_after(directory)
    found: list[tuple[int, dict[str, object]]] = []
    for child in directory.iterdir():
        if not child.is_dir() or not child.name.startswith("proposal-"):
            continue
        number = child.name.removeprefix("proposal-")
        if not number.isdigit():
            continue
        if after is not None and int(number) <= after:
            continue
        step = child / STEP_FILE
        if step.is_file():
            found.append((int(number), _read_json(step)))
            continue
        record = child / "proposal.json"
        if not record.is_file():
            continue
        data = _read_json(record)
        found.append(
            (
                int(number),
                {
                    "turn": int(number),
                    "status": "unsettled",
                    "reason": (
                        "the call was recorded and its answer never reached the gate "
                        f"({data.get('status') or 'no status'})"
                    ),
                },
            )
        )
    found.sort(key=lambda pair: pair[0])
    return tuple(item for _, item in found)


def _write_step(directory: Path, proposal: executor.Proposal, step: dict[str, object]) -> None:
    """One step beside the call that made it: the record the next step and the acta read."""
    (directory / f"proposal-{proposal.number}" / STEP_FILE).write_text(
        json.dumps(step, indent=2) + "\n", encoding="utf-8"
    )


def _last_applied_digest(directory: Path) -> str | None:
    """The digest of the change already in the tree, when the gate applied one."""
    if not directory.is_dir():
        return None
    after = _retry_after(directory)
    numbers = sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_dir()
            and child.name.isdigit()
            and (after is None or int(child.name) > after)
        ),
        key=lambda child: int(child.name),
        reverse=True,
    )
    for child in numbers:
        record = child / "attempt.json"
        if not record.is_file():
            continue
        data = _read_json(record)
        if data.get("status") != "applied":
            return None
        patch = data.get("patch")
        digest = patch.get("digest") if isinstance(patch, dict) else None
        return digest.removeprefix("sha256:") if isinstance(digest, str) else None
    return None


def _retry_after(directory: Path) -> int | None:
    """The number after which attempts belong to the current run, not to a reverted draft."""
    record = directory / "reverted.json"
    if not record.is_file():
        return None
    data = _read_json(record)
    numbers = data.get("attempts")
    if not isinstance(numbers, list):
        return None
    counted = [item for item in numbers if isinstance(item, int) and not isinstance(item, bool)]
    return max(counted) if counted else None


def _applied_attempts(directory: Path) -> tuple[caesar.Attempt, ...]:
    """The applied changes still in the tree, in the order the gate made them.

    A retry reverts the draft and writes ``reverted.json``; attempts at or below that number
    are no longer in the tree and must not be committed or verified. Everything above it is
    what the current tree holds, whatever dispatch applied it.
    """
    if not directory.is_dir():
        return ()
    after = _retry_after(directory)
    found: list[caesar.Attempt] = []
    for child in sorted(
        (item for item in directory.iterdir() if item.is_dir() and item.name.isdigit()),
        key=lambda item: int(item.name),
    ):
        number = int(child.name)
        if after is not None and number <= after:
            continue
        record = child / "attempt.json"
        if not record.is_file():
            continue
        data = _read_json(record)
        if data.get("status") != "applied":
            continue
        paths = data.get("paths")
        patch = data.get("patch")
        found.append(
            caesar.Attempt(
                task=str(data.get("task") or ""),
                number=number,
                status="applied",
                reason=str(data.get("reason") or ""),
                paths=tuple(str(item) for item in paths) if isinstance(paths, list) else (),
                directory=child,
                patch_digest=(
                    str(patch.get("digest") or "").removeprefix("sha256:")
                    if isinstance(patch, dict)
                    else ""
                ),
            )
        )
    return tuple(found)


def _accumulated_change(directory: Path, attempts: tuple[caesar.Attempt, ...]) -> Path | None:
    """The change the verdict is about: every step's diff in the order they were applied.

    The reader reads one change, and in a steps run the change is the series. Concatenating
    the diffs in order is the same change git will build the commit from; one step needs no
    new file, the attempt's own diff is already the change.
    """
    if not attempts:
        return None
    if len(attempts) == 1:
        return attempts[0].directory / "change.diff"
    target = attempts[-1].directory / "change.accumulated.diff"
    parts = [(item.directory / "change.diff").read_text(encoding="utf-8") for item in attempts]
    target.write_text("".join(parts), encoding="utf-8")
    return target


def _run_steps(
    box: boxes.Box,
    task: Task,
    config: executor.ExecutorConfig,
    *,
    shape: executor.Shape | None,
    context: dict[str, object] | None,
    engine_reason: str,
    contract_path: Path,
    trajan_root: Path,
    trajan_python: Path,
    directory: Path,
    when: datetime,
    previous: dict[str, object] | None,
    notes: list[str],
) -> StepsRun:
    """Ask, gate, check, read the check, ask again — until a stop, a dead end, or a pass.

    Every answer goes through the same gate as a one-shot change: stepping is more rope, not a
    second writer. The visible check runs on the tree after each applied step, and its words go
    into the next request; the hidden check (``repro``) never does, and it is still the verdict.

    The loop ends on the facts in its own records: a call ceiling, the task's remaining money,
    two applied attempts that failed the visible check the same way, or an answer the box will
    not ask past (no change, the wrong shape, a direct write). None of them is a verdict.
    """
    steps = list(_step_history(directory))
    applied: list[caesar.Attempt] = []
    last: executor.Proposal | None = None
    last_check: dict[str, object] | None = None
    stop: str | None = None
    repeated: str | None = None
    terminal: str | None = None
    while True:
        guard = _guard(box, task, config, directory)
        if guard.stop is not None:
            stop = guard.stop
            notes.append(guard.stop)
            break
        turn = executor.next_number(box.root, task.id)
        proposal = executor.propose(
            config,
            task,
            box,
            when=when,
            context=context,
            budget_cents=guard.cents,
            previous=previous,
            turn=turn,
            turns=tuple(steps),
            engine="steps",
            engine_choice=engine_reason,
        )
        last = proposal
        notes.extend(proposal.notes)
        _event(box.root, when, "task.executed", task, proposal.line, proposal.data())
        if proposal.done:
            step = {"turn": turn, "status": "done", "reason": proposal.reason}
            _write_step(directory, proposal, step)
            steps.append(step)
            notes.append(
                f"step {turn}: the executor declared the work done; the loop ended there and "
                "the box's own check still decides"
            )
            break
        if not proposal.usable:
            step = {"turn": turn, "status": proposal.status, "reason": proposal.reason}
            _write_step(directory, proposal, step)
            steps.append(step)
            terminal = f"step {turn} produced no usable change: {proposal.reason}"
            if proposal.truncated:
                notes.append(
                    "the room the task's money bought was spent to the last token: a larger "
                    "room is a budget decision (raise the task's max_cents), not a retry the box "
                    "can pay for out of the same budget"
                )
            notes.append(terminal)
            break
        if proposal.patch is not None:
            problem = None if shape is None else _shape_problem(shape, proposal.patch)
            if problem is not None:
                step = {"turn": turn, "status": "out-of-shape", "reason": problem}
                _write_step(directory, proposal, step)
                steps.append(step)
                terminal = f"step {turn}: {problem}"
                notes.append(terminal)
                break
            digest = _proposal_digest(box.project, proposal.patch)
            prior = _last_applied_digest(directory)
            if prior is not None and digest == prior:
                repeated = digest
                step = {
                    "turn": turn,
                    "status": "repeated",
                    "reason": "the change is already in the tree: it was not applied again",
                }
                _write_step(directory, proposal, step)
                steps.append(step)
                notes.append(
                    f"step {turn}: the executor proposed the change already applied: the loop "
                    "stopped, because the same answer cannot move the tree"
                )
                break
        assert proposal.patch is not None
        attempt = caesar.run_attempt(
            box.root, box.project, caesar.read_contract(contract_path), proposal.patch, when=when
        )
        if not attempt.applied:
            detail = (
                "; ".join(f"{item.code}: {item.detail}" for item in attempt.violations)
                or attempt.reason
            )
            step = {
                "turn": turn,
                "status": "refused",
                "attempt": attempt.number,
                "reason": detail,
            }
            _write_step(directory, proposal, step)
            steps.append(step)
            notes.append(f"step {turn}: the write gate refused the change: {detail}")
            continue
        check: dict[str, object] | None = None
        if task.self_check:
            code, tail = contour.run_repro(
                box.project, task.self_check, root=trajan_root, python=trajan_python
            )
            check = {"status": "passed" if code == 0 else "failed", "exit_code": code, "tail": tail}
            (attempt.directory / SELF_CHECK_FILE).write_text(
                json.dumps(check, indent=2) + "\n", encoding="utf-8"
            )
            notes.append(
                f"step {turn}: applied; the check the task shows the model: {check['status']} "
                f"({tail})"
            )
        else:
            notes.append(f"step {turn}: applied; the task names no visible check")
        step = {
            "turn": turn,
            "status": "applied",
            "attempt": attempt.number,
            "check": check,
            "seconds": None if proposal.usage is None else proposal.usage.seconds,
            "gate_seconds": attempt.seconds,
        }
        _write_step(directory, proposal, step)
        steps.append(step)
        applied.append(attempt)
        last_check = check
        if check is not None and check["status"] == "passed":
            notes.append(
                f"step {turn}: the visible check passed; the loop ends and the box's own check "
                "decides"
            )
            break
    return StepsRun(
        proposal=last,
        attempts=tuple(applied),
        self_check=last_check,
        steps=tuple(steps),
        stop=stop,
        repeated=repeated,
        terminal=terminal,
    )


def dispatch(
    box: boxes.Box,
    task: Task,
    *,
    routes: dict[str, tuple[str, ...]],
    trajan_root: Path,
    trajan_python: Path,
    tool: str | None = None,
    when: datetime | None = None,
    executor_config: executor.ExecutorConfig | None = None,
    resumed_from: Path | None = None,
    review: contour.Reviewer | None = None,
    commit_requested: bool = True,
    no_repeat: str | None = None,
    driven: bool = False,
    previous: dict[str, object] | None = None,
    notes_prefix: tuple[str, ...] = (),
    continuing: bool = False,
) -> Dispatch:
    """Run the task through its contours, write the record and the outbox summary.

    ``resumed_from`` is set when this is a continuation of an earlier run from the same frozen
    snapshot: the route is then run again from the start, because a retry is a new run of the
    same task, not a second half of the old one.

    ``review`` is the reader for the change, when the run asked for one. Without it the
    verification is checks-only and the record says the change was not read — the honest
    answer, not an assumed one.

    ``no_repeat`` is the digest of the change the previous attempt tried: a retry that proposes
    exactly it again is recorded and not applied, because paying twice for the same answer is
    how a retry turns into a loop.

    ``continuing`` is a steps run that stopped before a verdict and is now allowed to go on: the
    tree already holds the steps it paid for, so the reproduction is not read as "no defect"
    when it passes, and the loop picks up from the records instead of starting over.
    """
    began = time.monotonic()
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / "tabularium" / task.id
    directory.mkdir(parents=True, exist_ok=True)
    parked = _parked(box.root, task.id)
    if parked is not None:
        reason = str(parked.get("why") or "no reason recorded")
        detail = (
            f"the task is parked ({reason}): it does not run. `pantheon amend` writes a "
            "successor that names it; parking is not a pause button"
        )
        _event(box.root, stamp, "task.refused", task, detail)
        return Dispatch(
            task=task,
            state="refused",
            stages=(),
            directory=directory,
            notes=[detail],
            exit_code=1,
        )
    readiness = intake(task)
    if not readiness.ready:
        detail = (
            "the task is not handed on: "
            + "; ".join(readiness.refusals)
            + ". The contract is read before the work — `pantheon intake` shows the same five "
            "points — so nothing was asked and nothing was paid for"
        )
        _event(box.root, stamp, "task.refused", task, detail)
        return Dispatch(
            task=task,
            state="refused",
            stages=(),
            directory=directory,
            notes=[detail],
            exit_code=1,
        )
    blocked = refusal(task, routes, driven=driven)
    if blocked is not None:
        _event(box.root, stamp, "task.refused", task, blocked)
        return Dispatch(
            task=task,
            state="refused",
            stages=(),
            directory=directory,
            notes=[blocked],
            exit_code=1,
        )
    rail = rails.check(box, milestone=task.milestone, outside=task.outside_plan)
    if rail.refusal is not None:
        _event(box.root, stamp, "task.refused", task, rail.refusal)
        return Dispatch(
            task=task,
            state="refused",
            stages=(),
            directory=directory,
            notes=[rail.refusal],
            exit_code=1,
            rails=rail,
        )
    stages, extended = _with_write_gate(route(task, routes), task)
    notes: list[str] = list(notes_prefix)
    if resumed_from is not None:
        if continuing:
            notes.append(
                f"continued after {_relative(resumed_from, box.root)}: the steps already applied "
                "are kept, and the loop goes on from the records instead of starting over"
            )
        else:
            notes.append(
                f"continued after {_relative(resumed_from, box.root)}: the change was asked for "
                "again, from the frozen task, because the recorded attempt was not accepted"
            )
    task = _snapshot(box, task, stamp)
    if extended:
        notes.append(
            "the route for this kind does not include the write gate, but the task declares "
            "a change: the gate is not optional, so it was added"
        )
    arch: aurelius.Result | None = None
    attempt: caesar.Attempt | None = None
    verify: contour.VerifyResult | None = None
    reader: contour.Reviewer | None = None
    skipped = ""
    change: Path | None = task.change
    contract_path = _write_contract(box.root, task)
    _event(
        box.root,
        stamp,
        "task.resumed" if resumed_from is not None else "task.dispatched",
        task,
        "retry" if resumed_from is not None else " -> ".join(stages),
    )

    compared = _compare_with_archive(box, task, notes)
    if compared is not None and compared.escalated:
        escalated = contradiction.reason(compared)
        assert escalated is not None
        _event(box.root, stamp, "contract.contradiction", task, escalated)
        return _finish(
            box,
            task,
            Dispatch(
                task=task,
                state="refused",
                stages=stages,
                directory=directory,
                notes=notes,
                exit_code=1,
                resumed_from=resumed_from,
            ),
            stamp,
            began,
            rail=rail,
        )

    if "arch" in stages:
        arch = _check_architecture(box, tool)
        notes.extend(arch.notes)
        if arch.status == "broken":
            notes.append("the architecture is broken: the change does not start")
            return _finish(
                box,
                task,
                Dispatch(
                    task=task,
                    state="returned",
                    stages=stages,
                    arch=arch,
                    directory=directory,
                    notes=notes,
                    exit_code=1,
                    resumed_from=resumed_from,
                ),
                stamp,
                began,
                rail=rail,
            )

    proposal: executor.Proposal | None = None
    pack: context_pack.Pack | None = None
    own_check: dict[str, object] | None = None
    shape: executor.Shape | None = None
    shape_record: dict[str, object] | None = None
    engine_name = "one-shot"
    execution = executor.ExecutionRoute(
        None, "one-shot", "the task carries its own patch: no executor route was taken"
    )
    changed_paths: tuple[str, ...] | None = None
    steps: tuple[dict[str, object], ...] = ()
    applied: tuple[caesar.Attempt, ...] = ()
    loop_stop: str | None = None
    repeated: str | None = None
    if "run" in stages:
        patch = task.change
        if patch is None:
            config = executor_config or executor.read_config(box.root / executor.PROFILE_FILE)
            if config.command is None:
                notes.append(
                    "the task declares no change and the box declares no executor: nothing "
                    "was written, and the write gate had nothing to gate"
                )
            else:
                shape, shape_reason = executor.shape_for(config, task.shape)
                if task.shape is not None and shape is None:
                    notes.append(shape_reason)
                    return _finish(
                        box,
                        task,
                        Dispatch(
                            task=task,
                            state="refused",
                            stages=stages,
                            arch=arch,
                            directory=directory,
                            notes=notes,
                            exit_code=1,
                            resumed_from=resumed_from,
                        ),
                        stamp,
                        began,
                        rail=rail,
                    )
                shape_record = (
                    None
                    if shape is None
                    else {
                        "name": shape.name,
                        "max_changed_lines": shape.max_changed_lines,
                        "max_files": shape.max_files,
                    }
                )
                notes.append(f"shape: {shape_reason}")
                execution = executor.route_for(config, task)
                notes.append(f"level: {execution.reason}")
                if execution.refusal is not None:
                    notes.append(execution.refusal)
                    return _finish(
                        box,
                        task,
                        Dispatch(
                            task=task,
                            state="refused",
                            stages=stages,
                            arch=arch,
                            directory=directory,
                            notes=notes,
                            exit_code=1,
                            resumed_from=resumed_from,
                            engine=execution.engine,
                            level=execution.level,
                            route_reason=execution.reason,
                        ),
                        stamp,
                        began,
                        rail=rail,
                    )
                engine_name, engine_reason = execution.engine, execution.reason
                notes.append(f"engine: {engine_reason}")
                if engine_name == "steps" and executor.step_ceiling(task, config) is None:
                    notes.append(
                        "the box routes this kind through steps, and no call ceiling is declared "
                        "(max_steps in the task or the profile): a loop without a cap is not "
                        "allowed, so nothing was asked"
                    )
                    return _finish(
                        box,
                        task,
                        Dispatch(
                            task=task,
                            state="refused",
                            stages=stages,
                            arch=arch,
                            directory=directory,
                            notes=notes,
                            exit_code=1,
                            resumed_from=resumed_from,
                            engine=engine_name,
                            level=execution.level,
                            route_reason=execution.reason,
                        ),
                        stamp,
                        began,
                        rail=rail,
                    )
                if task.repro:
                    code, tail = contour.run_repro(
                        box.project, task.repro, root=trajan_root, python=trajan_python
                    )
                    if code == 0:
                        if continuing:
                            notes.append(
                                "the reproduction passes before this call: the tree already "
                                "holds the steps the task paid for, so it is not a task without "
                                "a defect"
                            )
                        else:
                            notes.append(
                                "the reproduction already passes before any change: the task does "
                                "not describe a live defect, so nothing was written"
                            )
                            return _finish(
                                box,
                                task,
                                Dispatch(
                                    task=task,
                                    state="returned",
                                    stages=stages,
                                    arch=arch,
                                    directory=directory,
                                    notes=notes,
                                    exit_code=1,
                                    resumed_from=resumed_from,
                                ),
                                stamp,
                                began,
                                rail=rail,
                            )
                    else:
                        notes.append(
                            f"the reproduction failed before the change as it should: {tail}"
                        )
                pack = context_pack.build(
                    box, task.id, task.paths, when=stamp, named=task.records
                )
                context_pack.write(pack)
                notes.append(f"context: {context_pack.line(pack)}")
                if engine_name == "steps":
                    run = _run_steps(
                        box,
                        task,
                        config,
                        shape=shape,
                        context=context_pack.payload(pack),
                        engine_reason=engine_reason,
                        contract_path=contract_path,
                        trajan_root=trajan_root,
                        trajan_python=trajan_python,
                        directory=directory,
                        when=stamp,
                        previous=previous,
                        notes=notes,
                    )
                    proposal = run.proposal
                    steps = run.steps
                    loop_stop = run.stop
                    repeated = run.repeated
                    applied = _applied_attempts(directory)
                    if run.proposal is not None and run.proposal.status == "unapproved-write":
                        return _finish(
                            box,
                            task,
                            Dispatch(
                                task=task,
                                state="returned",
                                stages=stages,
                                arch=arch,
                                directory=directory,
                                notes=notes,
                                exit_code=1,
                                proposal=run.proposal,
                                resumed_from=resumed_from,
                                context=pack,
                                engine=engine_name,
                                level=execution.level,
                                route_reason=execution.reason,
                                steps=steps,
                            ),
                            stamp,
                            began,
                            rail=rail,
                        )
                    if not applied:
                        if loop_stop is not None:
                            return _finish(
                                box,
                                task,
                                Dispatch(
                                    task=task,
                                    state="stopped",
                                    stages=stages,
                                    arch=arch,
                                    directory=directory,
                                    notes=notes,
                                    exit_code=2,
                                    proposal=run.proposal,
                                    resumed_from=resumed_from,
                                    context=pack,
                                    stop_reason=loop_stop,
                                    engine=engine_name,
                                    level=execution.level,
                                    route_reason=execution.reason,
                                    steps=steps,
                                    shape=shape_record,
                                ),
                                stamp,
                                began,
                                rail=rail,
                            )
                        return _finish(
                            box,
                            task,
                            Dispatch(
                                task=task,
                                state="returned",
                                stages=stages,
                                arch=arch,
                                directory=directory,
                                notes=notes,
                                exit_code=1,
                                proposal=run.proposal,
                                resumed_from=resumed_from,
                                context=pack,
                                engine=engine_name,
                                level=execution.level,
                                route_reason=execution.reason,
                                steps=steps,
                                shape=shape_record,
                            ),
                            stamp,
                            began,
                            rail=rail,
                        )
                    attempt = applied[-1]
                    own_check = run.self_check
                    change = _accumulated_change(directory, applied) or (
                        attempt.directory / "change.diff"
                    )
                else:
                    guard = _guard(box, task, config, directory)
                    if guard.stop is not None:
                        notes.append(guard.stop)
                        return _finish(
                            box,
                            task,
                            Dispatch(
                                task=task,
                                state="stopped",
                                stages=stages,
                                arch=arch,
                                directory=directory,
                                notes=notes,
                                exit_code=2,
                                resumed_from=resumed_from,
                                stop_reason=guard.stop,
                                engine=engine_name,
                                level=execution.level,
                                route_reason=execution.reason,
                            ),
                            stamp,
                            began,
                            rail=rail,
                        )
                    proposal = executor.propose(
                        config,
                        task,
                        box,
                        when=stamp,
                        context=context_pack.payload(pack),
                        previous=previous,
                        budget_cents=guard.cents,
                        engine=engine_name,
                        engine_choice=engine_reason,
                    )
                    notes.extend(proposal.notes)
                    _event(box.root, stamp, "task.executed", task, proposal.line, proposal.data())
                    if proposal.truncated and not proposal.usable:
                        notes.append(
                            "the room the task's money bought was spent to the last token: a "
                            "larger room is a budget decision (raise max_cents), not a retry the "
                            "box can pay for out of the same budget"
                        )
                    if not proposal.usable:
                        notes.append(
                            f"the executor did not produce a usable change: {proposal.reason}"
                        )
                        return _finish(
                            box,
                            task,
                            Dispatch(
                                task=task,
                                state="returned",
                                stages=stages,
                                arch=arch,
                                directory=directory,
                                notes=notes,
                                exit_code=1,
                                proposal=proposal,
                                resumed_from=resumed_from,
                                context=pack,
                                shape=shape_record,
                                engine=engine_name,
                                level=execution.level,
                                route_reason=execution.reason,
                            ),
                            stamp,
                            began,
                            rail=rail,
                        )
                    if shape is not None and proposal.patch is not None:
                        problem = _shape_problem(shape, proposal.patch)
                        if problem is not None:
                            notes.append(problem)
                            return _finish(
                                box,
                                task,
                                Dispatch(
                                    task=task,
                                    state="returned",
                                    stages=stages,
                                    arch=arch,
                                    directory=directory,
                                    notes=notes,
                                    exit_code=1,
                                    proposal=proposal,
                                    resumed_from=resumed_from,
                                    context=pack,
                                    shape=shape_record,
                                    engine=engine_name,
                                    level=execution.level,
                                    route_reason=execution.reason,
                                ),
                                stamp,
                                began,
                                rail=rail,
                            )
                    if no_repeat is not None and proposal.patch is not None:
                        digest = _proposal_digest(box.project, proposal.patch)
                        if digest == no_repeat:
                            notes.append(
                                "the executor proposed the change that was already tried: it was "
                                "not applied, because the same answer cannot become a different "
                                "verdict"
                            )
                            return _finish(
                                box,
                                task,
                                Dispatch(
                                    task=task,
                                    state="returned",
                                    stages=stages,
                                    arch=arch,
                                    directory=directory,
                                    notes=notes,
                                    exit_code=1,
                                    proposal=proposal,
                                    resumed_from=resumed_from,
                                    context=pack,
                                    repeated=digest,
                                    shape=shape_record,
                                    engine=engine_name,
                                    level=execution.level,
                                    route_reason=execution.reason,
                                ),
                                stamp,
                                began,
                                rail=rail,
                            )
                    patch = proposal.patch
        if patch is not None and not applied:
            contract = caesar.read_contract(contract_path)
            attempt = caesar.run_attempt(box.root, box.project, contract, patch, when=stamp)
            if not attempt.applied:
                detail = (
                    "; ".join(f"{item.code}: {item.detail}" for item in attempt.violations)
                    or attempt.reason
                )
                notes.append(f"the write gate refused the change: {detail}")
                return _finish(
                    box,
                    task,
                    Dispatch(
                        task=task,
                        state="returned",
                        stages=stages,
                        attempt=attempt,
                        arch=arch,
                        directory=directory,
                        notes=notes,
                        exit_code=1,
                        proposal=proposal,
                        resumed_from=resumed_from,
                        context=pack,
                        engine=engine_name,
                        level=execution.level,
                        route_reason=execution.reason,
                    ),
                    stamp,
                    began,
                    rail=rail,
                )
            # The change the gate applied is the change a reader must read: a task that
            # carried its patch and a task whose executor proposed one are the same fact here.
            change = attempt.directory / "change.diff"
            if task.self_check:
                code, tail = contour.run_repro(
                    box.project, task.self_check, root=trajan_root, python=trajan_python
                )
                own_check = {
                    "status": "passed" if code == 0 else "failed",
                    "exit_code": code,
                    "tail": tail,
                }
                (attempt.directory / SELF_CHECK_FILE).write_text(
                    json.dumps(own_check, indent=2) + "\n", encoding="utf-8"
                )
                notes.append(
                    f"the check the task shows the model: {own_check['status']} ({tail})"
                )
        if applied:
            changed_paths = tuple(sorted({name for item in applied for name in item.paths})) or None
        else:
            changed_paths = attempt.paths if attempt is not None else None

    sensitivity_report: sensitivity.Report | None = None
    if "run" in stages and ((attempt is not None and attempt.applied) or applied):
        sensitivity_report = _rehearse(box, task, change, trajan_root, trajan_python, notes)

    if "verify" in stages:
        reader, skipped = _reader_for(review, change, notes)
        if review is not None and reader is None and not skipped:
            notes.append(
                "--review asked for a reader, but this task produced no change to read: the "
                "checks ran alone"
            )
        verify = _verify(
            box,
            directory,
            stamp,
            trajan_root,
            trajan_python,
            reader,
            change,
            paths=_verification_paths(box, changed_paths, notes),
            repro=task.repro,
        )
        notes.extend(verify.reasons)
    else:
        notes.append("verification was not routed for this task: nothing was checked")

    state = _state(verify, arch)
    if (
        own_check is not None
        and own_check["status"] == "passed"
        and verify is not None
        and verify.status == "hold"
    ):
        notes.append(
            "the change satisfied the check the task shows the model, and failed the box's own: "
            "the answer was fitted to what it could see, not changed where the verdict looks"
        )
    made = _commit_after_verdict(
        box,
        attempt,
        verify,
        state,
        contract_path,
        commit_requested,
        stamp,
        attempts=applied or None,
    )
    if made is not None:
        notes.append(_commit_note(made))
    return _finish(
        box,
        task,
        Dispatch(
            task=task,
            state=state,
            stages=stages,
            attempt=attempt,
            verify=verify,
            arch=arch,
            commit=made,
            directory=directory,
            notes=notes,
            exit_code=0 if state == "closed" else 1,
            proposal=proposal,
            resumed_from=resumed_from,
            review_cap=None if reader is None else reader.max_cents,
            review_skipped=skipped or None,
            context=pack,
            self_check=own_check,
            shape=shape_record,
            engine=engine_name,
            level=execution.level,
            route_reason=execution.reason,
            steps=steps,
            stop_reason=loop_stop,
            repeated=repeated,
            sensitivity=sensitivity_report,
        ),
        stamp,
        began,
        rail=rail,
    )


def _rehearse(
    box: boxes.Box,
    task: Task,
    change: Path | None,
    trajan_root: Path,
    trajan_python: Path,
    notes: list[str],
) -> sensitivity.Report | None:
    """Does the task's own check notice the change? Recorded, never turned into a verdict.

    Deterministic and free, and still the smaller run: three pieces and ninety seconds, because
    the dispatch has already spent its money on the change and the checks. A check that cannot be
    run twice (too slow, already red) is named as not measured — the verdict does not move.
    """
    if not task.repro:
        notes.append(
            "sensitivity: the task declares no check of its own, so nothing was reversed: the "
            "project's checks say nothing about whether this task's criterion could fail"
        )
        return None
    if change is None or not change.is_file():
        notes.append("sensitivity: no applied change on disk, so nothing was reversed")
        return None
    try:
        report = sensitivity.check(
            box.project,
            task=task.id,
            patch=change,
            repro=task.repro,
            root=trajan_root,
            python=trajan_python,
            limit=SENSITIVITY_PIECES,
            seconds=SENSITIVITY_SECONDS,
            total_seconds=SENSITIVITY_TOTAL_SECONDS,
        )
    except boxes.HarnessError as error:
        notes.append(f"sensitivity: not measured — {error}")
        return None
    for item in report.blind:
        notes.append(
            f"sensitivity: the check does not notice {item.piece.where} ({item.piece.kind}): it "
            "can be green whatever that piece says"
        )
    if report.stopped:
        notes.append(f"sensitivity: {report.stopped}")
    return report


def _snapshot(box: boxes.Box, task: Task, when: datetime) -> Task:
    """Freeze the task beside its artifacts, so a dispatch can be continued later.

    The change file is copied too: the record must not depend on a file outside the box, and
    editing the original task afterwards must not rewrite what already ran.
    """
    directory = box.root / "tabularium" / task.id
    directory.mkdir(parents=True, exist_ok=True)
    change: Path | None = None
    if task.change is not None:
        try:
            data = task.change.read_bytes()
        except OSError as error:
            raise boxes.HarnessError(f"cannot read the patch {task.change}: {error}") from error
        change = directory / "change.diff"
        change.write_bytes(data)
    header = [
        "# Frozen by `pantheon dispatch`: the task these artifacts belong to.",
        "# `pantheon resume` reads this file, not the original: editing the task must not",
        "# rewrite what already ran.",
    ]
    (directory / SNAPSHOT_FILE).write_text(
        "\n".join(header) + "\n" + task_text(task, change=change), encoding="utf-8"
    )
    return replace(task, path=directory / SNAPSHOT_FILE, change=change)


def task_text(task: Task, *, change: Path | None = None) -> str:
    """The task as the TOML a dispatch freezes: the same fields, the change named as it is."""
    lines = [
        f"schema_version = {SCHEMA_VERSION}",
        f"id = {_quote(task.id)}",
    ]
    if task.parent is not None:
        lines.append(f"parent = {_quote(task.parent)}")
    if task.why is not None:
        lines.append(f"why = {_quote(task.why)}")
    for key, value in (("goal", task.goal), ("kind", task.kind), ("expected", task.expected)):
        if value is not None:
            lines.append(f"{key} = {_quote(value)}")
    lines.append("paths = [" + ", ".join(_quote(item) for item in task.paths) + "]")
    if task.shape is not None:
        lines.append(f"shape = {_quote(task.shape)}")
    if task.max_steps is not None:
        lines.append(f"max_steps = {task.max_steps}")
    if task.mode != "manual":
        lines.append(f"mode = {_quote(task.mode)}")
    if task.role != "executor":
        lines.append(f"role = {_quote(task.role)}")
    if task.milestone is not None:
        lines.append(f"milestone = {_quote(task.milestone)}")
    if task.outside_plan is not None:
        lines.append(f"outside_plan = {_quote(task.outside_plan)}")
    if change is not None:
        lines.append(f"change = {_quote(str(change))}")
    if task.forbidden:
        lines.append("forbidden = [" + ", ".join(_quote(item) for item in task.forbidden) + "]")
    if task.records:
        lines.append("records = [" + ", ".join(_quote(item) for item in task.records) + "]")
    if task.self_check:
        lines.append("self_check = [" + ", ".join(_quote(item) for item in task.self_check) + "]")
    if task.repro:
        lines.append("repro = [" + ", ".join(_quote(item) for item in task.repro) + "]")
    if task.budget:
        lines += ["", "[budget]"] + [f"{key} = {value}" for key, value in task.budget.items()]
    if task.contract is not None:
        lines += ["", "[contract]"]
        for key in ("symptom", "visible", "boundaries", "missing"):
            value = getattr(task.contract, key)
            if value is not None:
                lines.append(f"{key} = {_quote(value)}")
    return "\n".join(lines) + "\n"


PARKED_FILE = "parked.json"
"""A task set aside with a reason: it stays readable, and no dispatch may run it."""
AMENDMENT_FILE = "amendment.json"
"""The successor's difference from its parent, written once when ``amend`` made it."""
AMENDMENTS_FILE = "amendments.json"
"""On the parent: every successor made from it, so ``status`` can name the replacement."""


@dataclass(frozen=True, slots=True)
class Amendment:
    """A successor task and the difference the box recorded between it and its parent."""

    parent: Task
    child: Task
    changes: tuple[dict[str, object], ...]
    record: Path


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def _parked(box_root: Path, task_id: str) -> dict[str, object] | None:
    """The park record, when the task was set aside: the box refuses to run it again."""
    path = box_root / "tabularium" / task_id / PARKED_FILE
    if not path.is_file():
        return None
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"why": "the park record cannot be read"}
    return data if isinstance(data, dict) else {"why": "the park record is not an object"}


def _successor_id(box_root: Path, parent_id: str) -> str:
    """The next free ``<parent>-b`` name: a chain is readable without a registry."""
    base = f"{parent_id}-b"
    candidate = base
    number = 2
    while (box_root / "tabularium" / candidate).exists() or (
        box_root / "mandata" / f"{candidate}.toml"
    ).exists():
        candidate = f"{base}{number}"
        number += 1
    return candidate


def change_line(change: dict[str, object]) -> str:
    def show(value: object) -> str:
        if value is None:
            return "none"
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "none"
        return str(value)

    return f"{change.get('field')}: {show(change.get('before'))} -> {show(change.get('after'))}"


def _task_changes(before: Task, after: Task) -> list[dict[str, object]]:
    """What an amendment changed, in the three things a task envelope can widen."""
    changes: list[dict[str, object]] = []
    if before.paths != after.paths:
        changes.append(
            {"field": "paths", "before": list(before.paths), "after": list(after.paths)}
        )
    for key in sorted(set(before.budget) | set(after.budget)):
        if before.budget.get(key) != after.budget.get(key):
            changes.append(
                {
                    "field": f"budget.{key}",
                    "before": before.budget.get(key),
                    "after": after.budget.get(key),
                }
            )
    if before.max_steps != after.max_steps:
        changes.append(
            {
                "field": "max_steps",
                "before": before.max_steps,
                "after": after.max_steps,
            }
        )
    if before.shape != after.shape:
        changes.append(
            {"field": "shape", "before": before.shape, "after": after.shape}
        )
    return changes


def _append_acta_line(box_root: Path, task_id: str, line: str) -> None:
    """Say it in the task's acta too, when that acta exists: the summary must not go stale."""
    acta = sorted((box_root / "acta").glob(f"*-{task_id}.md"))
    if not acta:
        return
    with acta[-1].open("a", encoding="utf-8") as handle:
        handle.write(f"\n{line}\n")


def _amendment(directory: Path) -> dict[str, object] | None:
    """The successor's own record: what it changed from its parent, if it amended anything."""
    path = directory / AMENDMENT_FILE
    if not path.is_file():
        return None
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _amendments(box_root: Path, task_id: str) -> dict[str, object] | None:
    path = box_root / "tabularium" / task_id / AMENDMENTS_FILE
    if not path.is_file():
        return None
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _reason(value: str | None, command: str) -> str:
    reason = (value or "").strip()
    if not reason:
        raise boxes.HarnessError(f"{command} needs --why: a change nobody explained is a rewrite")
    return reason


def amend(
    box: boxes.Box,
    task_id: str,
    *,
    why: str | None,
    add_paths: tuple[str, ...] = (),
    budget: dict[str, int] | None = None,
    max_steps: int | None = None,
    when: datetime | None = None,
) -> Amendment:
    """Write a successor task that names its parent, the reason and the difference.

    The parent is never edited: its record already says what ran and what it concluded. The
    successor gets its own id, its own frozen snapshot and one amendment record; the parent
    gets an event and a line in its acta. A call that changes nothing is refused, because an
    empty successor is a second name for the same task.
    """
    reason = _reason(why, "amend")
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / "tabularium" / task_id
    snapshot = directory / SNAPSHOT_FILE
    if not snapshot.is_file():
        raise boxes.HarnessError(
            f"{task_id}: no {SNAPSHOT_FILE} in {directory}: there is no frozen task to amend. "
            "`pantheon dispatch` freezes the task when it runs it; amend reads that record."
        )
    parent = read_task(snapshot)
    added = tuple(item.strip() for item in add_paths if item.strip())
    if len(added) != len(add_paths):
        raise boxes.HarnessError("amend: --add-path needs a path, not an empty string")
    paths = tuple(dict.fromkeys((*parent.paths, *added)))
    new_budget = dict(parent.budget)
    for key, value in (budget or {}).items():
        if key not in BUDGET_KEYS:
            raise boxes.HarnessError(
                f"amend: budget.{key} is not one of {', '.join(sorted(BUDGET_KEYS))}"
            )
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise boxes.HarnessError(f"amend: budget.{key} must be a positive whole number")
        new_budget[key] = value
    if max_steps is not None and max_steps <= 0:
        raise boxes.HarnessError("amend: --max-steps must be a positive whole number")
    steps = parent.max_steps if max_steps is None else max_steps
    candidate = replace(parent, paths=paths, budget=new_budget, max_steps=steps)
    changes = _task_changes(parent, candidate)
    if not changes:
        raise boxes.HarnessError(
            "nothing changes: add a path, a budget or a call ceiling, or amend nothing — an "
            "empty successor would be the same task under a second name"
        )
    child_id = _successor_id(box.root, parent.id)
    child_dir = box.root / "tabularium" / child_id
    child_dir.mkdir(parents=True, exist_ok=False)
    change: Path | None = None
    if parent.change is not None and parent.change.is_file():
        change = child_dir / "change.diff"
        change.write_bytes(parent.change.read_bytes())
    mandata = box.root / "mandata"
    mandata.mkdir(parents=True, exist_ok=True)
    child_path = mandata / f"{child_id}.toml"
    child = replace(
        candidate,
        path=child_path,
        id=child_id,
        parent=parent.id,
        why=reason,
        change=change,
    )
    header = [
        "# Frozen by `pantheon amend`: the successor task these artifacts belong to.",
        "# `pantheon resume` reads this file; the parent keeps its own frozen record.",
    ]
    (child_dir / SNAPSHOT_FILE).write_text(
        "\n".join(header) + "\n" + task_text(child, change=change), encoding="utf-8"
    )
    child_path.write_text(task_text(child, change=change), encoding="utf-8")
    amendment = {
        "parent": parent.id,
        "child": child_id,
        "why": reason,
        "at": _iso(stamp),
        "changes": changes,
    }
    record = child_dir / AMENDMENT_FILE
    record.write_text(json.dumps(amendment, indent=2) + "\n", encoding="utf-8")
    chain_path = directory / AMENDMENTS_FILE
    chain: dict[str, object] = _amendments(box.root, parent.id) or {"children": []}
    children = chain.get("children")
    if not isinstance(children, list):
        children = []
    children.append({"id": child_id, "why": reason, "at": _iso(stamp)})
    chain["children"] = children
    chain_path.write_text(json.dumps(chain, indent=2) + "\n", encoding="utf-8")
    _event(
        box.root,
        stamp,
        "task.amended",
        parent,
        f"replaced by {child_id}: {reason}",
        data={"child": child_id, "why": reason, "changes": changes},
    )
    _append_acta_line(box.root, parent.id, f"- replaced by {child_id}: {reason}")
    return Amendment(parent=parent, child=child, changes=tuple(changes), record=record)


def park(
    box: boxes.Box,
    task_id: str,
    *,
    why: str | None,
    when: datetime | None = None,
) -> dict[str, object]:
    """Set a task aside with a reason; it stays readable, and no dispatch may run it.

    Parking is not deletion: the record keeps the work and the reason it stopped, and the way
    back is a successor (``amend``), not a quiet un-park of the same task.
    """
    reason = _reason(why, "park")
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / "tabularium" / task_id
    snapshot = directory / SNAPSHOT_FILE
    if not snapshot.is_file():
        raise boxes.HarnessError(
            f"{task_id}: no {SNAPSHOT_FILE} in {directory}: there is no task to park. "
            "A task is known by its frozen record; park cannot invent one."
        )
    task = read_task(snapshot)
    record: dict[str, object] = {"task": task.id, "why": reason, "at": _iso(stamp)}
    (directory / PARKED_FILE).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    _event(box.root, stamp, "task.parked", task, reason)
    _append_acta_line(box.root, task.id, f"- parked: {reason}")
    return record


def resume(
    box: boxes.Box,
    task_id: str,
    *,
    routes: dict[str, tuple[str, ...]],
    trajan_root: Path,
    trajan_python: Path,
    tool: str | None = None,
    when: datetime | None = None,
    executor_config: executor.ExecutorConfig | None = None,
    budget: dict[str, int] | None = None,
    retry: bool = False,
    review: contour.Reviewer | None = None,
    commit_requested: bool = True,
    notes_prefix: tuple[str, ...] = (),
) -> Dispatch:
    """Continue a dispatch that stopped before its verdict, from the artifacts it left.

    Three ways to stop are covered, and the record says which one it was:

    * the executor never answered (killed, timed out, crashed): nothing usable was recorded,
      so continuing means asking again with ``retry``, with a new budget if the limits were
      the reason;
    * the gate applied the change and the run died before verification: the change is in the
      tree, so the tree is what gets checked, not produced again;
    * the call cost more than the budget allowed, and the change was recorded but not applied:
      a raised ``--max-cents`` lets the tree take an already-paid change without paying twice.

    What a continuation never does: ask the model again for a change already on disk, verify a
    tree the applied change is no longer in, or close a task whose verification already ran.
    """
    began = time.monotonic()
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / "tabularium" / task_id
    snapshot = directory / SNAPSHOT_FILE
    if not snapshot.is_file():
        raise boxes.HarnessError(
            f"{task_id}: no {SNAPSHOT_FILE} in {directory}: there is nothing to continue from. "
            "`pantheon dispatch` freezes the task when it runs it; a dispatch from before that "
            "file existed cannot be continued — run the task again."
        )
    task = read_task(snapshot)
    parked = _parked(box.root, task.id)
    if parked is not None:
        reason = str(parked.get("why") or "no reason recorded")
        detail = (
            f"the task is parked ({reason}): it does not continue. `pantheon amend` writes a "
            "successor that names it; parking is not a pause button"
        )
        _event(box.root, stamp, "task.refused", task, detail)
        return Dispatch(
            task=task,
            state="refused",
            stages=(),
            directory=directory,
            notes=[detail],
            exit_code=1,
        )
    rail = rails.check(box, milestone=task.milestone, outside=task.outside_plan)
    if rail.refusal is not None:
        _event(box.root, stamp, "task.refused", task, rail.refusal)
        return Dispatch(
            task=task,
            state="refused",
            stages=(),
            directory=directory,
            notes=[rail.refusal],
            exit_code=1,
            rails=rail,
        )
    if budget:
        task = replace(task, budget={**task.budget, **budget})
    config = executor_config or executor.read_config(box.root / executor.PROFILE_FILE)
    notes: list[str] = []
    stages: tuple[str, ...] = ()
    patch: Path | None = None
    attempt: caesar.Attempt | None = None
    proposal: executor.Proposal | None = None
    source: Path | None = None

    record = _last_record(directory)
    if record is not None and not retry:
        record_path, record_data = record
        state = str(record_data.get("state"))
        if state == "closed":
            return Dispatch(
                task=task,
                state="closed",
                stages=_stages(record_data),
                directory=directory,
                notes=[
                    f"nothing to continue: the task is closed ({_relative(record_path, box.root)})"
                ],
                exit_code=0,
            )
        if record_data.get("verify") is not None:
            return Dispatch(
                task=task,
                state=state,
                stages=_stages(record_data),
                directory=directory,
                notes=[
                    f"nothing to continue: verification ran and the verdict is {state} "
                    f"({_relative(record_path, box.root)}); a re-run would repeat it"
                ],
                exit_code=0,
            )

    engine, _ = executor.engine_for(config, task.kind)
    if not retry and engine == "steps" and _steps_unfinished(directory, record):
        if config.command is None:
            raise boxes.HarnessError(
                f"{task_id}: the box declares no executor, so there is nothing to ask again"
            )
        return dispatch(
            box,
            task,
            routes=routes,
            trajan_root=trajan_root,
            trajan_python=trajan_python,
            tool=tool,
            when=stamp,
            executor_config=config,
            resumed_from=record[0] if record is not None else None,
            notes_prefix=(*notes_prefix, *notes),
            review=review,
            commit_requested=commit_requested,
            continuing=True,
        )

    if retry:
        if config.command is None:
            raise boxes.HarnessError(
                f"{task_id}: the box declares no executor, so there is nothing to ask again"
            )
        if record is not None:
            _revert_draft(box, record[1], notes)
        return dispatch(
            box,
            task,
            routes=routes,
            trajan_root=trajan_root,
            trajan_python=trajan_python,
            tool=tool,
            when=stamp,
            executor_config=config,
            resumed_from=record[0] if record is not None else None,
            previous=previous_failure(
                record[1], attempts=len(list(directory.glob("dispatch-*.json")))
            )
            if record is not None
            else None,
            notes_prefix=(*notes_prefix, *notes),
            review=review,
            commit_requested=commit_requested,
        )

    contract_path = _write_contract(box.root, task)
    _event(box.root, stamp, "task.resumed", task, "continue")

    last_attempt = _last_attempt(directory)
    if last_attempt is not None:
        attempt_path, attempt_data = last_attempt
        status = str(attempt_data.get("status"))
        if status == "applied":
            note = _check_tree_still_holds(box, attempt_path)
            if note is not None:
                notes.append(note)
            stages = ("verify",)
            source = attempt_path
            notes.append(
                f"continued from {_relative(attempt_path, box.root)}: the gate applied the "
                "change, and verification is the step that did not run"
            )
        elif status == "refused":
            raise boxes.HarnessError(
                f"{task_id}: the gate refused the recorded change "
                f"({attempt_data.get('reason') or 'no reason recorded'}); continuing means a "
                "new proposal: `pantheon resume ... --retry`"
            )
        else:
            raise boxes.HarnessError(
                f"{task_id}: the last attempt has status {status!r}, which cannot be continued"
            )
    else:
        found = _last_proposal(directory)
        if found is not None:
            proposal_path, proposal_data = found
            status = str(proposal_data.get("status"))
            candidate = proposal_path / "change.diff"
            if status in {"ok", "over-budget"} and candidate.is_file():
                if status == "over-budget":
                    _check_over_budget(task, proposal_data, config)
                patch = candidate
                source = proposal_path
                stages = ("run", "verify")
                notes.append(
                    f"continued from {_relative(proposal_path, box.root)}: the proposal was "
                    "recorded, and the gate is what it has not gone through"
                )
        if patch is None:
            raise boxes.HarnessError(
                f"{task_id}: the last dispatch left no usable change{_because(record)}; ask "
                "again with `pantheon resume ... --retry`"
            )

    if "run" in stages:
        assert patch is not None
        attempt = caesar.run_attempt(
            box.root, box.project, caesar.read_contract(contract_path), patch, when=stamp
        )
        if not attempt.applied:
            detail = (
                "; ".join(f"{item.code}: {item.detail}" for item in attempt.violations)
                or attempt.reason
            )
            notes.append(f"the write gate refused the change: {detail}")
            return _finish(
                box,
                task,
                Dispatch(
                    task=task,
                    state="returned",
                    stages=stages,
                    attempt=attempt,
                    directory=directory,
                    notes=notes,
                    exit_code=1,
                    proposal=proposal,
                    resumed_from=source,
                ),
                stamp,
                began,
                rail=rail,
            )

    reader, skipped = _reader_for(review, patch, notes)
    if review is not None and reader is None and not skipped:
        notes.append(
            "--review asked for a reader, but the recorded change is gone: the checks ran alone"
        )
    verify = _verify(
        box,
        directory,
        stamp,
        trajan_root,
        trajan_python,
        reader,
        patch,
        paths=_verification_paths(
            box, attempt.paths if attempt is not None else _attempt_paths(last_attempt), notes
        ),
    )
    notes.extend(verify.reasons)
    state = _state(verify, None)
    made = _commit_after_verdict(
        box,
        attempt,
        verify,
        state,
        contract_path,
        commit_requested,
        stamp,
        attempts=_applied_attempts(directory) or None,
    )
    if made is not None:
        notes.append(_commit_note(made))
    return _finish(
        box,
        task,
        Dispatch(
            task=task,
            state=state,
            stages=stages,
            attempt=attempt,
            verify=verify,
            commit=made,
            directory=directory,
            notes=notes,
            exit_code=0 if state == "closed" else 1,
            proposal=proposal,
            resumed_from=source,
            review_cap=None if reader is None else reader.max_cents,
            review_skipped=skipped or None,
        ),
        stamp,
        began,
        rail=rail,
    )


def dispatch_with_attempts(
    box: boxes.Box,
    task: Task,
    *,
    routes: dict[str, tuple[str, ...]],
    trajan_root: Path,
    trajan_python: Path,
    tool: str | None = None,
    when: datetime | None = None,
    executor_config: executor.ExecutorConfig | None = None,
    review: contour.Reviewer | None = None,
    commit_requested: bool = True,
    attempts: int = REPAIR_ATTEMPTS,
) -> Dispatch:
    """Ask for the change, and ask once more when the class says a repair can work.

    The first attempt is the ordinary dispatch; a second one is paid only when the checks
    disagreed in a way the change can address (``RETRYABLE_FAILURES``). A broken architecture or
    a refused task is not a bad answer — asking again would buy the same refusal.

    The same class twice in a row stops earlier than the ceiling: a repair that failed the same
    way is not learning, and the next paid call would be a guess (D47).

    The stop is written as a box event, not as a note: an attempt's record is written when it
    finishes, and a note added afterwards would make the record and the run disagree.
    """
    if attempts < 1:
        raise boxes.HarnessError(
            f"a task is asked at least once: attempts is {attempts}, and the box will not run a "
            "task zero times"
        )
    seen: list[str] = []
    config = executor_config or executor.read_config(box.root / executor.PROFILE_FILE)
    result = dispatch(
        box,
        task,
        routes=routes,
        trajan_root=trajan_root,
        trajan_python=trajan_python,
        tool=tool,
        when=when,
        executor_config=executor_config,
        review=review,
        commit_requested=commit_requested,
    )
    while True:
        reached = failure_class(result)
        if result.state != "returned" or reached not in RETRYABLE_FAILURES:
            return result
        assert reached is not None
        if result.engine != "one-shot":
            _retry_stopped(
                box,
                result,
                f"the {result.engine or 'declared'} engine has its own loop and its own fuses: a "
                "second attempt would start it over, so the box does not add one",
            )
            return result
        if not config.declared:
            _retry_stopped(
                box,
                result,
                "the box declares no executor: there is nothing to ask again, and the verdict "
                "stands as it is",
            )
            return result
        if len(seen) + 1 >= attempts:
            _retry_stopped(
                box,
                result,
                f"the attempts are spent ({attempts} of {attempts}) and the class is still "
                f"{reached}",
            )
            return result
        if seen and seen[-1] == reached:
            _retry_stopped(
                box,
                result,
                f"the same class twice ({reached}): a third attempt would repeat the second",
            )
            return result
        seen.append(reached)
        result = resume(
            box,
            result.task.id,
            routes=routes,
            trajan_root=trajan_root,
            trajan_python=trajan_python,
            tool=tool,
            executor_config=executor_config,
            retry=True,
            review=review,
            commit_requested=commit_requested,
            notes_prefix=(
                f"attempt {len(seen) + 1} of {attempts}: the previous one ended {reached}, "
                "and the box asks again",
            ),
        )


def _retry_stopped(box: boxes.Box, result: Dispatch, why: str) -> None:
    """The box decided not to pay for another call: the fact goes where stops are kept."""
    _event(box.root, datetime.now(UTC).replace(microsecond=0), "retry.stopped", result.task, why)


def _steps_unfinished(
    directory: Path, record: tuple[Path, dict[str, object]] | None
) -> bool:
    """Whether a steps run stopped with work left, so continuing means going on, not verifying.

    A recorded stop (state ``stopped``) is the box refusing to pay for another call: the tree
    has no applied step to judge, and continuing is only meaningful after the budget or the
    call ceiling was raised. Without a record the artifacts speak: a last step whose visible
    check failed, or one the gate refused, is a loop that had not finished; a passed check, a
    "done", or an answer the box would not gate means the next thing is the verdict. An
    unsettled proposal is deliberately not continued: its change is on disk, and applying it
    is cheaper than asking for it again.
    """
    if record is not None:
        return str(record[1].get("state")) == "stopped"
    history = _step_history(directory)
    if not history:
        return False
    last = history[-1]
    status = last.get("status")
    if status == "refused":
        return True
    if status != "applied":
        return False
    check = last.get("check")
    return not (isinstance(check, dict) and check.get("status") == "passed")


def _reader_for(
    review: contour.Reviewer | None, change: Path | None, notes: list[str]
) -> tuple[contour.Reviewer | None, str]:
    """The reader, or the reason this change is not read by the box's own declaration.

    The decision is made on the paths the change actually touches, read from the change file
    the gate wrote. A delegated choice ("read only src/**") is recorded with its reason: a
    saving that looked like a reading would be the quietest kind of gap.
    """
    if review is None or change is None or not review.when_paths:
        return review, ""
    try:
        touched = caesar.touched_paths(change.read_text(encoding="utf-8"))
    except OSError as error:
        notes.append(f"the change could not be read to decide what to read: {error}")
        return review, ""
    reason = review.declines(touched)
    if reason is None:
        return review, ""
    notes.append(f"the change was not read: {reason}")
    return None, reason


def _attempt_paths(found: tuple[Path, dict[str, object]] | None) -> tuple[str, ...] | None:
    """The paths a recorded attempt touched: what the plan of a later verify narrows by."""
    if found is None:
        return None
    raw = found[1].get("paths")
    if not isinstance(raw, list):
        return None
    return tuple(str(item) for item in raw)


def _verification_paths(
    box: boxes.Box, narrowed: tuple[str, ...] | None, notes: list[str]
) -> tuple[str, ...] | None:
    """The paths that narrow a plan; a debt at the limit is repaid with one full run.

    The debt is the box's, not the task's: a quick verdict is a decision taken earlier, and the
    run that repays it checks everything the policy declares instead of what the change touches.
    """
    if narrowed is None:
        return None
    owed = debt.tally(box)
    if not owed.due():
        return narrowed
    notes.append(
        f"the check debt reached {owed.count} narrowed verdicts: this run verified the full "
        "policy instead of the set its paths touch, and the debt is repaid"
    )
    return None


def _verify(
    box: boxes.Box,
    directory: Path,
    stamp: datetime,
    trajan_root: Path,
    trajan_python: Path,
    review: contour.Reviewer | None = None,
    change: Path | None = None,
    paths: tuple[str, ...] | None = None,
    repro: tuple[str, ...] = (),
) -> contour.VerifyResult:
    """Run the checks, and the reader too when both a reviewer and a change exist.

    A reviewer without a change would be a request trajan refuses ("a change needs a reader"),
    so the change is passed exactly when there is one and someone was asked to read it.

    ``repro`` is the task's own input, added to the policy as one more check: the defect the
    task names must go from failing to passing, or the green suite proved nothing about it.
    """
    out = contour.run_directory(directory, stamp)
    if review is None or change is None:
        review = None
        change = None
    policy = _policy_with_repro(box.project, repro, out) if repro else None
    return contour.run_verify(
        box.project,
        root=trajan_root,
        python=trajan_python,
        out=out,
        policy=policy,
        diff=change,
        paths=paths,
        reviewer=None if review is None else review.spec,
        reviewer_env=None if review is None else review.env,
        box_root=box.root,
    )


REPRO_CHECK = """\

[[checks]]
id = "repro"
name = "the reproduction the task names"
argv = [{argv}]
purpose = "the input the task names: it must fail before the change and pass after it"
timeout_s = 120
"""


def _policy_with_repro(project: Path, repro: tuple[str, ...], out: Path) -> Path:
    """The project's policy plus one check: the task's own reproduction, run as evidence.

    Written by appending a ``[[checks]]`` table, so the policy stays data and the box does not
    re-serialise a file it only reads. The check has no ``when_paths``: the input is the point,
    not the paths the change touches.
    """
    source = project / ".trajan" / "policy.toml"
    text = source.read_text(encoding="utf-8") if source.is_file() else ""
    out.mkdir(parents=True, exist_ok=True)
    argv = ", ".join(json.dumps(item) for item in repro)
    target = out / "policy-repro.toml"
    target.write_text(
        text.rstrip("\n") + "\n" + REPRO_CHECK.format(argv=argv), encoding="utf-8"
    )
    return target


def _commit_after_verdict(
    box: boxes.Box,
    attempt: caesar.Attempt | None,
    verify: contour.VerifyResult | None,
    state: str,
    contract_path: Path,
    requested: bool,
    when: datetime,
    attempts: tuple[caesar.Attempt, ...] | None = None,
) -> caesar.Commit | None:
    """Only a settled green verdict turns the change into a commit; nothing else does.

    A task that changed nothing has nothing to commit, and a verdict that did not pass leaves
    the change in the tree as a draft: the commit is what says "this is the project now", so
    it is made one verdict later than the write, never instead of it. ``--no-commit`` is
    recorded rather than obeyed silently, because "green but not in git" is a fact a person
    reads later and must be able to find.
    """
    if attempt is None or not attempt.applied:
        return None
    if not requested:
        return caesar.Commit(
            "skipped", reason="not committed: this run asked for the tree alone (--no-commit)"
        )
    if state != "closed" or verify is None:
        return None
    return caesar.commit_change(
        box.project,
        caesar.read_contract(contract_path),
        attempt,
        verdict=verify.status,
        report=_relative(verify.report, box.root),
        box_root=box.root,
        when=when,
        attempts=attempts,
    )


def _commit_note(commit: caesar.Commit) -> str:
    if commit.made:
        return f"the change is committed as {commit.sha[:12]}: {commit.subject}"
    if commit.status == "skipped":
        return f"no commit: {commit.reason}"
    return f"the commit failed: {commit.reason}"


def _proposal_digest(project: Path, patch: Path) -> str:
    """The proposed change in project coordinates, or an empty string if it cannot be read."""
    try:
        return caesar.patch_digest(project, patch.read_text(encoding="utf-8"))
    except OSError:
        return ""


def _context_line(pack: context_pack.Pack | None) -> str:
    if pack is None:
        return "not built — no model was asked"
    return context_pack.line(pack)


def _frame_line(standing: frame.Standing | None) -> str:
    return "not read" if standing is None else standing.line()


def _rails_line(check: rails.Check | None) -> str:
    return "not recorded" if check is None else check.line()


def _commit_line(commit: caesar.Commit | None) -> str:
    if commit is None:
        return "not made"
    if commit.made:
        return f"{commit.sha[:12]}  {commit.subject}"
    return f"not made — {commit.reason}"


def _check_tree_still_holds(box: boxes.Box, attempt_path: Path) -> str | None:
    """The tree the gate changed must still be the tree verification would judge.

    The applied change has to be in it: a green verdict about a tree that does not contain the
    change is the quietest pass there is. A moved HEAD is not a refusal by itself — the change
    is still there — but it is worth saying out loud.
    """
    patch = attempt_path / "change.diff"
    if not patch.is_file():
        raise boxes.HarnessError(
            f"the applied change is missing from {attempt_path}: there is nothing to verify"
        )
    base: object = None
    change_file = attempt_path / "change.json"
    if change_file.is_file():
        try:
            change = json.loads(change_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            change = {}
        if isinstance(change, dict):
            base = change.get("base")
    if not caesar.contains(box.project, patch):
        raise boxes.HarnessError(
            "the change the gate applied is no longer in the tree: a verification would pass a "
            "tree that does not hold it. Dispatch the task again."
        )
    current = caesar.head(box.project)
    if isinstance(base, str) and base and current is not None and base != current:
        return (
            f"the project moved from {base[:12]} to {current[:12]} since the gate; the change is "
            "still in the tree, so verification judges it on top of the new base"
        )
    return None


def _check_over_budget(
    task: Task, data: dict[str, object], config: executor.ExecutorConfig
) -> None:
    """A recorded change that broke the budget is applied only when the limit is raised."""
    usage = data.get("usage")
    spent = usage.get("cost_cents") if isinstance(usage, dict) else None
    _seconds, cents = executor.limits(config, task.budget)
    if cents is None or not isinstance(spent, (int, float)) or isinstance(spent, bool):
        return
    if float(spent) > cents:
        raise boxes.HarnessError(
            f"the recorded call cost {float(spent):.2f} cents, above the {cents:g} allowed, and "
            "nothing was applied. Raise the limit without paying twice with "
            f"`--max-cents {float(spent):g}`, or ask the model again with `--retry --max-cents N`."
        )


@dataclass(frozen=True, slots=True)
class Spend:
    """What a task has already paid, read from its records: there is no counter to drift."""

    cents: float
    calls: int
    unpriced: int


def spent(box_root: Path, task_id: str, *, ceiling: float | None = None) -> Spend:
    """The money a task's records already show, and the billable calls that made it.

    A call that was certainly never sent (no key, a local refusal) is not spend and not a call:
    charging it at the ceiling would let one infrastructure mistake eat the task's budget. A
    call whose fate is unknown is still not free: it is counted at the box's ceiling when one
    is declared, because the only honest number for "unknown" is the most it could have been.
    """
    directory = box_root / "tabularium" / task_id
    cents = 0.0
    calls = 0
    unpriced = 0
    for record in sorted(directory.glob("proposal-*/proposal.json")):
        data = _read_json(record)
        billing = data.get("billing_status")
        if billing == "not_billed":
            continue
        calls += 1
        usage = data.get("usage")
        cost = usage.get("cost_cents") if isinstance(usage, dict) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            cents += float(cost)
        else:
            unpriced += 1
    if unpriced and ceiling is not None:
        cents += unpriced * ceiling
    for record in sorted(directory.glob("dispatch-*.json")):
        data = _read_json(record)
        verify = data.get("verify")
        review = verify.get("review") if isinstance(verify, dict) else None
        if not isinstance(review, dict):
            continue
        cost = review.get("cost_cents")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            cents += float(cost)
            continue
        cap = data.get("review_cap")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool):
            cents += float(cap)
        elif ceiling is not None:
            cents += ceiling
    return Spend(cents=round(cents, 4), calls=calls, unpriced=unpriced)


def _steps(task: Task, config: executor.ExecutorConfig) -> int | None:
    """How many calls this task may make: the lower of the task's ceiling and the box's."""
    declared = [value for value in (task.max_steps, config.max_steps) if value is not None]
    return min(declared) if declared else None


@dataclass(frozen=True, slots=True)
class Guard:
    """What is left for the next call, or why there must not be one."""

    cents: float | None
    stop: str | None = None


def _guard(box: boxes.Box, task: Task, config: executor.ExecutorConfig, directory: Path) -> Guard:
    """The questions asked before paying for another call, and the answer that stops it.

    Three facts, all read from the box's own records: how many calls were made, how much they
    cost, and whether the last two attempts failed the same visible check in the same words.
    The order is deliberate: the budget and the call count are counts, while "no progress" is
    a judgement about two runs — the counts are cheaper to be sure about.
    """
    ceiling = executor.limits(config, {})[1]
    record = spent(box.root, task.id, ceiling=ceiling)
    steps = _steps(task, config)
    if steps is not None and record.calls >= steps:
        return Guard(
            None,
            f"the task allows {steps} call(s) and has already made {record.calls}: nothing was "
            "asked again",
        )
    declared = task.budget.get("max_cents")
    box_cap = config.max_cents
    limit: float | None = None
    if declared is not None:
        limit = float(declared)
    if box_cap is not None:
        limit = float(box_cap) if limit is None else min(limit, float(box_cap))
    cents: float | None = None
    if limit is not None:
        left = limit - record.cents
        if left <= 0:
            if declared is not None and (box_cap is None or float(declared) <= float(box_cap)):
                source = f"the task declared {declared:g} cents"
            elif declared is not None:
                source = f"the box allows {box_cap:g} cents (the task asked {declared:g})"
            else:
                source = f"the box declares {box_cap:g} cents"
            unknown = (
                ""
                if ceiling is not None
                else f"; {record.unpriced} call(s) reported no price, so the sum is a lower bound"
            )
            return Guard(
                None,
                f"{source} and its records already show "
                f"{record.cents:.2f} cents over {record.calls} call(s){unknown}: nothing was "
                "asked again — raise the limit if this is a deliberate decision",
            )
        cents = left
    progress = _no_progress(directory)
    if progress is not None:
        return Guard(None, progress)
    return Guard(cents)


def _no_progress(directory: Path) -> str | None:
    """Whether the last two applied attempts failed the visible check in the same words.

    The signal is deterministic and narrow: the same exit code and the same tail twice, read
    from the attempts themselves rather than from the dispatch records, because an attempt has
    its own number and cannot be overwritten by a second dispatch in the same second. A visible
    check that passes is not this: the model saw it pass, so it is not stuck against the thing
    it can see, and the verdict is still the hidden check's to decide.
    """
    if not directory.is_dir():
        return None
    attempts = [child for child in directory.iterdir() if child.is_dir() and child.name.isdigit()]
    attempts.sort(key=lambda child: int(child.name))
    if len(attempts) < 2:
        return None
    checks: list[dict[str, object]] = []
    for attempt in attempts[-2:]:
        path = attempt / SELF_CHECK_FILE
        if not path.is_file():
            return None
        check = _read_json(path)
        if check.get("status") != "failed":
            return None
        checks.append(check)
    first, second = checks
    codes = (first.get("exit_code"), second.get("exit_code"))
    tails = (first.get("tail"), second.get("tail"))
    if (
        isinstance(codes[0], int)
        and codes[0] == codes[1]
        and isinstance(tails[0], str)
        and tails[0] == tails[1]
    ):
        return (
            "the last two attempts failed the visible check with the same exit code and the "
            "same words: another call would pay for an answer that is not moving"
        )
    return None


def _stages(data: dict[str, object]) -> tuple[str, ...]:
    raw = data.get("stages")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


def _last_record(directory: Path) -> tuple[Path, dict[str, object]] | None:
    records = sorted(directory.glob("dispatch-*.json"))
    if not records:
        return None
    newest = records[-1]
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(
            f"the newest dispatch record {newest} does not parse: {error}"
        ) from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"the newest dispatch record {newest} is not a JSON object")
    return newest, {str(key): value for key, value in data.items()}


CHECK_HINT_LINES = 4
"""A retry needs the sentence that failed, not the whole log it sat in."""
CHECK_HINT_CHARS = 400
_CHECK_HINT = re.compile(r"(?i)error|invalid|failed|failure|expected|assert|--> ")
"""Lines that name what went wrong; a check's whole output would bury the reason in noise."""


def check_tail(report: dict[str, object], item: dict[str, object]) -> str:
    """The failing check's own words, from the report the box already wrote.

    ``detail`` says only "exit 1, expected 0", which tells a retry that something is wrong but
    not what: it would answer the counter instead of the failure. The check's output is in the
    same report, so the retry is given the line that broke — never a summary invented here.
    """
    evidence = item.get("evidence")
    name = evidence[0] if isinstance(evidence, list) and evidence else None
    run = report.get("run")
    checks = run.get("checks") if isinstance(run, dict) else None
    if not isinstance(name, str) or not isinstance(checks, list):
        return ""
    for check in checks:
        if not isinstance(check, dict) or check.get("check_id") != name:
            continue
        tail = check.get("output_tail")
        if not isinstance(tail, str):
            return ""
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        picked = [line for line in lines if _CHECK_HINT.search(line)][:CHECK_HINT_LINES]
        text = " / ".join(picked or lines[:CHECK_HINT_LINES])
        return text if len(text) <= CHECK_HINT_CHARS else text[: CHECK_HINT_CHARS - 1] + "…"
    return ""


def _report_at(value: object) -> dict[str, object] | None:
    """A verify report by the path the record holds, or ``None``: a lost report is a gap."""
    if not isinstance(value, str) or not value:
        return None
    try:
        data = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def previous_failure(
    record: dict[str, object], *, attempts: int | None = None
) -> dict[str, object] | None:
    """Why the last dispatch was not accepted, as facts for the next attempt.

    A retry that repeats the same words without the verdict that refused them is blind: it can
    only change something at random. The lines come from the box's own record — the verdict,
    the failed checks with the line that broke, the findings — so the executor is told what
    needs answering, not what someone thinks of it. A campaign step and a single task retry
    answer to the same question, so they read the same record through this function.
    """
    checks: list[str] = []
    findings: list[str] = []
    verify = record.get("verify")
    report = _report_at(verify.get("report")) if isinstance(verify, dict) else None
    if report is not None:
        items = report.get("findings")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict) or item.get("status") == "false-positive":
                    continue
                title = str(item.get("title", ""))
                if item.get("kind") == "check-failure":
                    detail = check_tail(report, item) or str(item.get("detail", ""))
                    checks.append(f"{title} ({detail})" if detail else title)
                else:
                    location = item.get("location")
                    name = location.get("file") if isinstance(location, dict) else ""
                    findings.append(f"{name}: {title}" if name else title)
    own = record.get("self_check")
    if isinstance(own, dict):
        # The model sees one check and is judged by another: handing it the verdict's own words
        # would turn the second check into the first, and the answer could be fitted to it.
        checks = []
        status = str(own.get("status"))
        tail = str(own.get("tail") or "")
        if status == "failed":
            checks.append(f"the check the task shows you failed ({tail})")
        else:
            checks.append(
                "the check the task shows you passed, and the box's own check still failed: "
                "the behaviour is wrong somewhere the visible check does not look"
            )
    reason = ""
    executor_record = record.get("executor")
    if isinstance(executor_record, dict) and executor_record.get("status") == "failed":
        reason = str(executor_record.get("reason") or "")
    if not reason:
        recorded_notes = record.get("notes")
        if isinstance(recorded_notes, list) and recorded_notes:
            reason = str(recorded_notes[-1])
    verdict = "no verdict"
    if isinstance(verify, dict) and isinstance(verify.get("status"), str):
        verdict = str(verify["status"])
    elif record.get("state") == "closed":
        verdict = "closed"
    count = attempts if attempts is not None else record.get("attempts", "?")
    return {
        "verdict": verdict,
        "attempts": count,
        "reason": reason,
        "checks": checks,
        "findings": findings,
    }


def _revert_draft(box: boxes.Box, record: dict[str, object], notes: list[str]) -> None:
    """Undo the draft a failed run left in the tree before the retry starts.

    A retry must begin on the tree the box accepted, not on top of the change it refused: the
    executor reads the files from disk, so the draft would be read as if it were the project.
    A steps run has several drafts, and they are reverted last-first — the order they were
    applied in, so each patch meets the tree it was made against. The numbers are written to
    ``reverted.json``: a later commit or verification must not resurrect a draft that is gone.
    """
    attempt = record.get("attempt")
    if not isinstance(attempt, dict) or attempt.get("status") != "applied":
        return
    directory = attempt.get("directory")
    base = Path(directory).parent if isinstance(directory, str) and directory else None
    applied = _applied_attempts(base) if base is not None else ()
    if base is None or not applied:
        return
    reverted: list[int] = []
    for item in reversed(applied):
        patch = item.directory / "change.diff"
        if not patch.is_file():
            raise boxes.HarnessError(
                f"the draft of attempt {item.number} is gone ({patch}): a retry would judge a "
                "tree that still holds part of the old run"
            )
        if not caesar.contains(box.project, patch):
            raise boxes.HarnessError(
                f"the draft of attempt {item.number} ({patch}) is no longer in the tree: a "
                "retry would judge a tree that holds a mix of two runs"
            )
        done, detail = caesar.revert(box.project, patch)
        if not done:
            raise boxes.HarnessError(
                f"the draft of attempt {item.number} ({patch}) cannot be reverted: {detail}; a "
                "retry would judge a tree that holds two changes"
            )
        reverted.append(item.number)
    if reverted:
        turns = [
            int(child.name.removeprefix("proposal-"))
            for child in base.iterdir()
            if child.is_dir()
            and child.name.startswith("proposal-")
            and child.name.removeprefix("proposal-").isdigit()
        ]
        (base / "reverted.json").write_text(
            json.dumps({"attempts": reverted, "after_turn": max(turns, default=0)}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if len(reverted) == 1:
        patch = applied[-1].directory / "change.diff"
        notes.append(
            f"the draft of the last attempt was reverted before the retry: "
            f"{_relative(patch, box.root)}"
        )
    else:
        notes.append(
            f"the draft of the last run was reverted before the retry: {len(reverted)} applied "
            f"step(s), last first ({', '.join(str(item) for item in reverted)})"
        )


def _last_attempt(directory: Path) -> tuple[Path, dict[str, object]] | None:
    numbers = sorted(
        (child for child in directory.iterdir() if child.is_dir() and child.name.isdigit()),
        key=lambda child: int(child.name),
    )
    if not numbers:
        return None
    newest = numbers[-1]
    record = newest / "attempt.json"
    if not record.is_file():
        return None
    return newest, _read_json(record)


def _last_proposal(directory: Path) -> tuple[Path, dict[str, object]] | None:
    candidates = [
        child
        for child in directory.iterdir()
        if child.is_dir() and child.name.startswith("proposal-") and child.name[9:].isdigit()
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda child: int(child.name[9:]))
    record = newest / "proposal.json"
    if not record.is_file():
        return None
    return newest, _read_json(record)


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(
            f"{path} does not parse, so what ran cannot be read: {error}"
        ) from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path} is not a JSON object, so what ran cannot be read")
    return {str(key): value for key, value in data.items()}


def _because(record: tuple[Path, dict[str, object]] | None) -> str:
    if record is None:
        return ""
    notes = record[1].get("notes")
    if not isinstance(notes, list) or not notes:
        return ""
    return "; the last record says: " + "; ".join(str(note) for note in notes[:3])


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _quote(value: str) -> str:
    """A TOML basic string: a frozen task is read back by the parser that wrote it.

    A command may carry a newline (the pilot's reproduction is a small script), and a raw one
    inside a basic string makes the file unparsable — the snapshot then cannot be continued.
    """
    escaped = (
        value.replace(chr(92), chr(92) * 2)
        .replace('"', chr(92) + '"')
        .replace(chr(10), chr(92) + "n")
        .replace(chr(13), chr(92) + "r")
        .replace(chr(9), chr(92) + "t")
    )
    return '"' + escaped + '"'


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """The projection of one task: what its newest dispatch said, and when."""

    id: str
    state: str
    at: str
    verify: str | None
    record: Path
    unknowns: tuple[str, ...] = ()
    seconds: float | None = None
    """The dispatch's total wall time, when its record measured one."""

    parent: str | None = None
    """The task this one amends, when it is a successor."""
    why: str | None = None
    """Why it differs, as the amendment recorded."""
    replaced_by: str | None = None
    """The newest successor made from this task, when one was made."""
    parked: str | None = None
    """Why this task was set aside, when it was."""


def _when_value(value: str) -> float:
    """A record's time as a number, whichever of the two stamps the box wrote."""
    text = value.strip()
    if not text:
        return -1.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return -1.0


def recent_tasks(box_root: Path, limit: int = 10) -> tuple[TaskSummary, ...]:
    """The newest dispatch record per task, oldest first: what ``status`` may say.

    A task that was amended but not run yet is still a task the owner made: it is shown as
    "not run" with its parent, because a successor that silently waited would make the status
    look like the amendment never happened.
    """
    base = box_root / "tabularium"
    if not base.is_dir():
        return ()
    found: list[TaskSummary] = []
    for task_dir in sorted(base.iterdir()):
        if not task_dir.is_dir():
            continue
        records = sorted(task_dir.glob("dispatch-*.json"))
        amendment = _amendment(task_dir)
        parked = _parked(box_root, task_dir.name)
        snapshot = task_dir / SNAPSHOT_FILE
        parent: str | None = None
        why: str | None = None
        if amendment is not None:
            parent = str(amendment.get("parent")) if amendment.get("parent") else None
            why = str(amendment.get("why")) if amendment.get("why") else None
        elif snapshot.is_file():
            try:
                frozen = read_task(snapshot)
            except boxes.HarnessError:
                frozen = None
            if frozen is not None:
                parent, why = frozen.parent, frozen.why
        if records:
            try:
                data = json.loads(records[-1].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            verify = data.get("verify")
            unknowns = data.get("unknowns")
            timings = data.get("timings")
            seconds = timings.get("total") if isinstance(timings, dict) else None
            state = str(data.get("state", "unknown"))
            at = str(data.get("at", "")) or records[-1].stem.removeprefix("dispatch-")
            record = records[-1]
            known = tuple(str(item) for item in unknowns) if isinstance(unknowns, list) else ()
            measured = float(seconds) if isinstance(seconds, (int, float)) else None
        else:
            if amendment is None and parked is None and not snapshot.is_file():
                continue
            verify = None
            known = ()
            measured = None
            state = "not run"
            at = str((amendment or {}).get("at") or (parked or {}).get("at") or "")
            record = snapshot
        if parked is not None:
            state = "parked"
        chain = _amendments(box_root, task_dir.name)
        children = chain.get("children") if isinstance(chain, dict) else None
        replaced_by = None
        if isinstance(children, list) and children:
            last = children[-1]
            if isinstance(last, dict) and last.get("id"):
                replaced_by = str(last["id"])
        found.append(
            TaskSummary(
                id=str(task_dir.name),
                state=state,
                at=at,
                verify=str(verify.get("status")) if isinstance(verify, dict) else None,
                record=record,
                unknowns=known,
                seconds=measured,
                parent=parent,
                why=why,
                replaced_by=replaced_by,
                parked=str(parked.get("why") or "") if parked is not None else None,
            )
        )
    found.sort(key=lambda summary: (_when_value(summary.at), summary.id))
    return tuple(found[-limit:])


def render_dispatch(result: Dispatch) -> list[str]:
    """The dispatch as a person reads it."""
    lines = [
        f"task       {result.task.id}  ({result.task.path})",
        f"kind       {result.task.kind or 'not declared'}",
        f"role       {result.task.role}",
        f"scope      {', '.join(result.task.paths) or 'nothing declared'}",
    ]
    if result.task.parent is not None:
        lines.append(f"parent     {result.task.parent} — {result.task.why or 'no reason recorded'}")
    lines += [
        f"stages     {' -> '.join(result.stages) or 'none'}",
        f"engine     {_engine_line(result)}",
        f"level      {result.level if result.level is not None else 'legacy'} — "
        f"{result.route_reason or 'no execution policy'}",
        f"frame      {_frame_line(result.frame)}",
        f"rails      {_rails_line(result.rails)}",
        f"arch       {_arch_line(result.arch)}",
        f"executor   {_executor_line(result.proposal)}",
        f"reading    {_reading_line(result.proposal)}",
        f"context    {_context_line(result.context)}",
        f"attempt    {_attempt_line(result.attempt)}",
        f"verify     {_verify_line(result.verify, result.review_skipped)}",
        f"own check  {_own_check_line(result.self_check, result.task.self_check)}",
        f"sensitivity {_sensitivity_line(result.sensitivity)}",
        f"shape      {_shape_line(result.shape)}",
        f"commit     {_commit_line(result.commit)}",
        f"time       {_time_line(result)}",
        f"state      {result.state}",
    ]
    failed = failure_class(result)
    if failed is not None:
        lines.append(f"class      {failed}")
    if result.resumed_from is not None:
        lines.append(f"resumed    from {result.resumed_from}")
    if result.stop_reason is not None:
        lines.append(f"stopped    {result.stop_reason}")
    for note in result.notes:
        lines.append(f"note       {note}")
    if result.outbox is not None:
        lines.append(f"outbox     {result.outbox}")
    return lines


def _sensitivity_line(report: sensitivity.Report | None) -> str:
    """One line beside the verdict: what the reversal saw, and no promise beyond it."""
    if report is None:
        return "not run — no check of the task's own, or it could not be measured"
    tested = len(report.noticed) + len(report.blind)
    if not tested:
        return f"nothing to reverse ({len(report.skipped)} piece(s) named as not tested)"
    return (
        f"{tested} piece(s) tested, {len(report.noticed)} noticed, "
        f"{len(report.blind)} blind, {len(report.skipped)} skipped, {report.seconds:.2f} s"
    )


def dispatch_json(result: Dispatch) -> dict[str, object]:
    """The same answer for a machine."""
    return {
        "task": result.task.id,
        "kind": result.task.kind,
        "role": result.task.role,
        "paths": list(result.task.paths),
        "parent": result.task.parent,
        "contract": _contract_json(result.task.contract),
        "why": result.task.why,
        "stages": list(result.stages),
        "engine": result.engine,
        "level": result.level,
        "route": result.route_reason,
        "steps": list(result.steps),
        "arch": None
        if result.arch is None
        else {"status": result.arch.status, "kept": result.arch.kept, "broken": result.arch.broken},
        "attempt": None if result.attempt is None else caesar.attempt_json(result.attempt),
        "budget": dict(result.task.budget),
        "executor": None if result.proposal is None else executor.proposal_json(result.proposal),
        "context": None if result.context is None else result.context.json(),
        "reading": None if result.proposal is None else result.proposal.reading,
        "repeated": result.repeated,
        "stop_reason": result.stop_reason,
        "failure_class": failure_class(result),
        "self_check": result.self_check,
        "sensitivity": None
        if result.sensitivity is None
        else sensitivity.payload(result.sensitivity),
        "shape": result.shape,
        "verify": None
        if result.verify is None
        else {
            "status": result.verify.status,
            "report": str(result.verify.report),
            "reviewed": result.verify.reviewed,
            "review": None
            if result.verify.review is None
            else {
                "model": result.verify.review.model,
                "status": result.verify.review.status,
                "proposed": result.verify.review.proposed,
                "confirmed": result.verify.review.confirmed,
                "prompt_tokens": result.verify.review.prompt_tokens,
                "completion_tokens": result.verify.review.completion_tokens,
                "calls": result.verify.review.calls,
                "cost_cents": result.verify.review.cost_cents,
                "cached": result.verify.review.cached,
                "error": result.verify.review.error,
            },
            "exit_code": result.verify.exit_code,
        },
        "commit": None if result.commit is None else caesar.commit_json(result.commit),
        "frame": None if result.frame is None else result.frame.json(),
        "rails": None if result.rails is None else result.rails.json(),
        "state": result.state,
        "resumed_from": None if result.resumed_from is None else str(result.resumed_from),
        "review_cap": result.review_cap,
        "review_skipped": result.review_skipped,
        "unknowns": _unknowns(result),
        "timings": dict(result.timings),
        "notes": list(result.notes),
        "acta": None if result.outbox is None else str(result.outbox),
        "exit_code": result.exit_code,
    }


@dataclass(frozen=True, slots=True)
class Explanation:
    """What a dispatch would decide, before it pays for a call.

    Every field is read through the same function the dispatch uses: the route and its write
    gate, the engine and model tables, the limits, the guard and the room. The test that keeps
    this honest is "the explanation equals the record" on one task, because a resolver that
    grows its own copy of a rule is how the two answers start to differ.
    """

    task: Task
    stages: tuple[str, ...]
    write_gate_added: bool
    frame: frame.Standing
    rails: rails.Check
    engine: str
    engine_reason: str
    model: str | None
    model_reason: str | None
    seconds: int
    cents: float | None
    calls: int | None
    room: int | None
    room_note: str
    policy: str | None
    checks: tuple[str, ...]
    repro: tuple[str, ...]
    self_check: tuple[str, ...]
    reader: bool
    refusal: str | None
    parked: str | None
    gaps: tuple[str, ...]
    level: int | None = None
    route_reason: str = ""


def explain(
    box: boxes.Box,
    task: Task,
    *,
    routes: dict[str, tuple[str, ...]],
    config: executor.ExecutorConfig,
    review: contour.Reviewer | None = None,
) -> Explanation:
    """Resolve the task's envelope, route, limits, checks and gaps without writing anything.

    Reading is the whole point: the box is not frozen, the contract is not written, no event is
    made. A task that would be refused says so here, with the same words the dispatch uses.
    """
    directory = box.root / "tabularium" / task.id
    rail = rails.check(box, milestone=task.milestone, outside=task.outside_plan)
    parked_record = _parked(box.root, task.id)
    parked = (
        str(parked_record.get("why") or "no reason recorded")
        if parked_record is not None
        else None
    )
    execution = executor.route_for(config, task)
    blocked = refusal(task, routes)
    if blocked is None and execution.refusal is not None:
        blocked = execution.refusal
    if blocked is None and rail.refusal is not None:
        blocked = rail.refusal
    if parked is not None:
        blocked = f"the task is parked ({parked}): it does not run; amend writes a successor"
    base, extended = _with_write_gate(route(task, routes), task)
    engine, engine_reason = execution.engine, execution.reason
    model, model_reason = executor.model_for(config, task.kind)
    seconds, cents = executor.limits(config, task.budget)
    calls = _steps(task, config)
    guard = _guard(box, task, config, directory)
    if guard.stop is not None:
        room, room_note = None, guard.stop
    else:
        room, room_note = executor.room_for(
            config,
            guard.cents,
            model,
            steps=engine == "steps",
            directory=directory,
        )
    project = boxes.read_project(box.project)
    gaps: list[str] = []
    for gap in (
        frame.standing(box).gap(),
        rail.gap(),
    ):
        if gap is not None:
            gaps.append(gap)
    if "verify" not in base:
        gaps.append("no verification stage: nothing will be checked")
    if review is None:
        gaps.append(
            "no reader is declared: the verdict will be about the checks, not about what the "
            "change does"
        )
    if not task.repro:
        gaps.append(
            "no reproduction: 'done' will rest on the suite, which proves only what it covers"
        )
    if not task.self_check:
        gaps.append("no visible check: the model will not see what its change did")
    if execution.level is None:
        gaps.append(
            "no execution policy: one-shot may run a task above level 1; declare [execution] "
            "to enforce the one-shot/steps/agent boundary"
        )
    declared_cents = task.budget.get("max_cents")
    if declared_cents is None:
        gaps.append("no money budget: the call's cost is not bounded before it is made")
    elif config.max_cents is not None and float(declared_cents) > float(config.max_cents):
        gaps.append(
            f"the task asks {declared_cents:g} cents and the box allows {config.max_cents:g}: "
            "the box's ceiling is what binds"
        )
    if calls is None:
        gaps.append("no call ceiling: only money, no progress or an answer can stop the loop")
    if config.command is None and task.change is None:
        gaps.append("no executor and no change: the run would have nothing to apply")
    return Explanation(
        task=task,
        stages=base,
        write_gate_added=extended,
        frame=frame.standing(box),
        rails=rail,
        engine=engine,
        engine_reason=engine_reason,
        model=model.model if model is not None else None,
        model_reason=model_reason,
        seconds=seconds,
        cents=cents,
        calls=calls,
        room=room,
        room_note=room_note,
        policy=project.policy_name,
        checks=project.checks,
        repro=task.repro,
        self_check=task.self_check,
        reader=review is not None,
        refusal=blocked,
        parked=parked,
        gaps=tuple(gaps),
        level=execution.level,
        route_reason=execution.reason,
    )


def _command_words(command: tuple[str, ...]) -> str:
    """One readable line for a command: the full argv stays in the JSON and the record."""
    if not command:
        return "not declared"
    parts: list[str] = []
    for part in command:
        flat = " ".join(part.split())
        if len(flat) > 72:
            flat = flat[:69] + "..."
        parts.append(shlex.quote(flat))
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class Intake:
    """Whether a task may be handed on: the contract filled, nothing left to ask."""

    task: Task
    refusals: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.refusals


def intake(task: Task) -> Intake:
    """Check the task's contract before any work: five points, no open questions.

    ``dispatch`` asks the same question before it runs anything and refuses a task that is not
    ready: the contract is the first gate, not a formality, and a task whose criterion is missing
    must not spend a call. This reading answers whether the task is ready to be *handed on* — the
    owner's words are complete, and nothing is left for the owner to answer — and writes nothing.
    """
    refusals: list[str] = []
    gaps: list[str] = []
    contract = task.contract
    if contract is None:
        refusals.append(
            "no contract: the task is not handed on before the criterion is in the owner's words"
        )
    else:
        omitted = [
            name
            for name in ("symptom", "visible", "boundaries")
            if getattr(contract, name) is None
        ]
        if omitted:
            refusals.append(
                "the contract does not name " + ", ".join(omitted) + ": the criterion is incomplete"
            )
        if contract.missing is not None:
            refusals.append(
                "the report leaves facts unknown (" + contract.missing + "): escalate to the owner"
            )
    if task.expected is None:
        refusals.append("no expected behaviour: what will be seen after the fix is not stated")
    if not task.paths:
        gaps.append("no scope: dispatch will refuse a task with no declared paths")
    if not task.repro:
        gaps.append(
            "no reproduction: 'done' will rest on the suite, which proves only what it covers"
        )
    return Intake(task=task, refusals=tuple(refusals), gaps=tuple(gaps))


def render_intake(result: Intake) -> list[str]:
    """The intake answer as a person reads it."""
    lines = [
        f"task       {result.task.id}  ({result.task.path})",
        f"contract   {_contract_words(result.task.contract)}",
        f"expected   {result.task.expected or 'not declared'}",
        f"verdict    {'ready' if result.ready else 'refused'}",
    ]
    if result.refusals:
        lines.append("refusals")
        lines.extend(f"- {item}" for item in result.refusals)
    lines.append("gaps")
    if result.gaps:
        lines.extend(f"- {item}" for item in result.gaps)
    else:
        lines.append("- none")
    return lines


def intake_payload(result: Intake) -> dict[str, object]:
    """The same answer for a machine."""
    return {
        "task": result.task.id,
        "ready": result.ready,
        "refusals": list(result.refusals),
        "gaps": list(result.gaps),
        "contract": _contract_json(result.task.contract),
        "expected": result.task.expected,
    }


def _contract_words(contract: Contract | None) -> str:
    """The contract as one line: each point named, the absent ones left out, absence named."""
    if contract is None:
        return "not declared — the criterion is not in the owner's words"
    parts = [
        f"{key}: {value}"
        for key, value in (
            ("symptom", contract.symptom),
            ("visible", contract.visible),
            ("boundaries", contract.boundaries),
            ("missing", contract.missing),
        )
        if value is not None
    ]
    return "; ".join(parts)


def _contract_json(contract: Contract | None) -> dict[str, str] | None:
    if contract is None:
        return None
    return {
        key: value
        for key, value in (
            ("symptom", contract.symptom),
            ("visible", contract.visible),
            ("boundaries", contract.boundaries),
            ("missing", contract.missing),
        )
        if value is not None
    }


def render_explanation(result: Explanation) -> list[str]:
    """The explanation as a person reads it, with the record's own field names."""
    lines = [
        f"task       {result.task.id}  ({result.task.path})",
        f"kind       {result.task.kind or 'not declared'}",
        f"goal       {result.task.goal or 'not declared'}",
        f"contract   {_contract_words(result.task.contract)}",
    ]
    if result.task.parent is not None:
        lines.append(
            f"parent     {result.task.parent} — {result.task.why or 'no reason recorded'}"
        )
    limits = f"{result.seconds} s, "
    limits += "no cent ceiling" if result.cents is None else f"{result.cents:g} cents"
    limits += f", {result.calls} call(s)" if result.calls is not None else ", no call ceiling"
    room = "none" if result.room is None else f"{result.room} tokens"
    lines += [
        f"frame      {result.frame.line()}",
        f"rails      {result.rails.line()}",
        f"route      {' -> '.join(result.stages) or 'none'}"
        + (" (write gate added)" if result.write_gate_added else ""),
        f"engine     {result.engine} — {result.engine_reason}",
        f"level      {result.level if result.level is not None else 'legacy'} — "
        f"{result.route_reason or 'no execution policy'}",
        f"model      {result.model or 'not declared'}"
        + (f" — {result.model_reason}" if result.model_reason else ""),
        f"limits     {limits}",
        f"room       {room} — {result.room_note}",
        f"checks     {result.policy or 'no policy'}: "
        f"{', '.join(result.checks) or 'none'} (narrowed by the changed paths)",
        f"repro      {_command_words(result.repro)} (decides the verdict)",
        f"self check {_command_words(result.self_check)} "
        "(shown to the model, does not decide)",
        f"reader     {'declared' if result.reader else 'none declared'}",
    ]
    if result.refusal is not None:
        lines.append(f"refused    {result.refusal}")
    lines.append("gaps")
    lines.extend(f"- {gap}" for gap in result.gaps) if result.gaps else lines.append("- none")
    return lines


def explanation_json(result: Explanation) -> dict[str, object]:
    """The same facts for a machine."""
    return {
        "task": result.task.id,
        "kind": result.task.kind,
        "goal": result.task.goal,
        "parent": result.task.parent,
        "why": result.task.why,
        "stages": list(result.stages),
        "write_gate_added": result.write_gate_added,
        "frame": result.frame.json(),
        "rails": result.rails.json(),
        "engine": result.engine,
        "engine_reason": result.engine_reason,
        "level": result.level,
        "route": result.route_reason,
        "model": result.model,
        "model_reason": result.model_reason,
        "limits": {
            "seconds": result.seconds,
            "cents": result.cents,
            "calls": result.calls,
            "room": result.room,
            "room_note": result.room_note,
        },
        "checks": {
            "policy": result.policy,
            "ids": list(result.checks),
            "repro": list(result.repro),
            "self_check": list(result.self_check),
        },
        "reader": result.reader,
        "refusal": result.refusal,
        "parked": result.parked,
        "gaps": list(result.gaps),
    }


def _with_write_gate(stages: tuple[str, ...], task: Task) -> tuple[tuple[str, ...], bool]:
    """A declared change always passes the write gate, whatever the routing table says.

    The table decides which contours a kind needs; it cannot decide that a write needs no
    gate. Without this, a docs task carrying a patch would skip the gate and verify a tree the
    change never touched — a green answer about the wrong question.
    """
    if task.change is None or "run" in stages:
        return stages, False
    ordered = tuple(stage for stage in ("arch", "run", "verify") if stage in {*stages, "run"})
    return ordered, True


def _state(verify: contour.VerifyResult | None, arch: aurelius.Result | None) -> str:
    """Augustus's projection of the contours' answers — never a verdict of its own."""
    if verify is None:
        return "unverified"
    if not verify.passed:
        return "returned"
    if arch is not None and arch.status == "broken":
        return "returned"
    return "closed"


def failure_class(result: Dispatch) -> str | None:
    """Which checks disagreed, as one deterministic fact — the retry policy rests on it.

    The class is read from what the record already says: the verdict, the visible check, the
    architecture. No model is asked, so the same run always gets the same class. ``None`` is a
    run that has no failed verdict — closed, refused, stopped before its checks.
    """
    if result.state != "returned":
        return None
    if result.arch is not None and result.arch.status == "broken":
        return FAILURE_ARCHITECTURE
    if result.verify is None or result.verify.passed:
        return None
    own = result.self_check or {}
    if str(own.get("status")) == "pass":
        return FAILURE_NARROWED
    return FAILURE_INCOMPLETE


def _compare_with_archive(
    box: boxes.Box, task: Task, notes: list[str]
) -> contradiction.Comparison | None:
    """The new contract against the decisions kept on its paths, before anything is paid.

    Nothing to compare costs nothing: a task whose paths no closed change touched is not sent to
    a reader at all. When there is history and no validator is declared, the record says the
    contracts were not compared — a gap named, not a gap that looks like a pass.
    """
    if not task.paths:
        return None
    config = contradiction.read_config(box.root / contradiction.PROFILE_FILE)
    ask = None
    if config.declared and config.enabled:
        ask = contradiction.command_ask(
            config.command or contradiction.default_command(),
            box_root=box.root,
            model=config.model,
            seconds=config.seconds,
        )
    contract = {key: value for key, value in _contract_points(task)}
    compared = contradiction.compare(
        box.root,
        task.paths,
        lambda history: contradiction.prompt(
            task.goal or "", task.expected or "", contract, history
        ),
        ask=ask,
    )
    if compared.history:
        notes.append(contradiction.note(compared))
    return compared


def _contract_points(task: Task) -> tuple[tuple[str, str], ...]:
    """The four contract points as words; a point left out is absent, not empty."""
    contract = task.contract
    if contract is None:
        return ()
    points: list[tuple[str, str]] = []
    for key in ("symptom", "visible", "boundaries", "missing"):
        value = getattr(contract, key)
        if value:
            points.append((key, value))
    return tuple(points)


def _check_architecture(box: boxes.Box, tool: str | None) -> aurelius.Result:
    model = box.project / aurelius.MODEL_FILE
    if not model.is_file():
        return aurelius.Result(
            status="unchecked",
            kept=0,
            broken=0,
            tool="",
            version="",
            directory=box.root / "tabularium" / "aurelius",
            exit_code=2,
            digest="",
            notes=[f"no {aurelius.MODEL_FILE} in the project: the architecture was not checked"],
        )
    return aurelius.check(box.root, box.project, aurelius.read_model(model), tool=tool)


def _write_contract(box_root: Path, task: Task) -> Path:
    """Augustus states the requirements; Caesar owns the file that gates the write."""
    lines = [
        "# Written by `pantheon dispatch` from the task: Caesar reads this, not the task.",
        f"schema_version = {SCHEMA_VERSION}",
        f'task = "{task.id}"',
    ]
    for key, value in (("kind", task.kind), ("goal", task.goal), ("expected", task.expected)):
        if value:
            lines.append(f"{key} = {_quote(value)}")
    lines.append("allowed_paths = [" + ", ".join(_quote(item) for item in task.paths) + "]")
    if task.forbidden:
        lines.append("forbidden = [" + ", ".join(_quote(item) for item in task.forbidden) + "]")
    if task.budget:
        lines += ["", "[budget]"] + [f"{key} = {value}" for key, value in task.budget.items()]
    path = box_root / "mandata" / f"{task.id}.contract.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _finish(
    box: boxes.Box,
    task: Task,
    result: Dispatch,
    when: datetime,
    began: float,
    rail: rails.Check | None = None,
) -> Dispatch:
    record = result.directory / f"dispatch-{when:%Y%m%dT%H%M%SZ}.json"
    outbox = box.root / "acta" / f"{when:%Y-%m-%d}-{task.id}.md"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    notes = list(result.notes)
    if record.exists():
        # Two attempts can finish inside one second; the record is named by the second, so the
        # second attempt would overwrite the first and the box would keep one of the two. The
        # stamp moves to the next free second and the record says it moved: a name that hides
        # the collision is the quiet loss this box refuses.
        began_stamp = when
        while record.exists():
            when += timedelta(seconds=1)
            record = result.directory / f"dispatch-{when:%Y%m%dT%H%M%SZ}.json"
        moved = int((when - began_stamp).total_seconds())
        notes.append(
            f"the record stamp moved by {moved} s: another attempt finished in the second this "
            "run ended in, and both records must stay"
        )
    if outbox.exists():
        # The day's summary is one file per task, and a second attempt in the same day would
        # overwrite the first attempt's summary — the acta a person reads. The later one takes
        # the time in its name instead, so both remain and the name says which is which.
        outbox = box.root / "acta" / f"{when:%Y-%m-%d}-{task.id}-{when:%H%M%SZ}.md"
    finished = Dispatch(
        task=result.task,
        state=result.state,
        stages=result.stages,
        attempt=result.attempt,
        verify=result.verify,
        arch=result.arch,
        commit=result.commit,
        frame=frame.standing(box),
        rails=rail
        if rail is not None
        else rails.check(box, milestone=task.milestone, outside=task.outside_plan),
        outbox=outbox,
        directory=result.directory,
        notes=notes,
        exit_code=result.exit_code,
        proposal=result.proposal,
        resumed_from=result.resumed_from,
        review_cap=result.review_cap,
        review_skipped=result.review_skipped,
        context=result.context,
        repeated=result.repeated,
        stop_reason=result.stop_reason,
        self_check=result.self_check,
        sensitivity=result.sensitivity,
        shape=result.shape,
        engine=result.engine,
        level=result.level,
        route_reason=result.route_reason,
        steps=result.steps,
        timings=_timings(result, began),
    )
    # The summary is written from the finished record, not from the one handed in: the frame is
    # read here, and a summary that omitted it would be the quietest gap of all.
    outbox.write_text(_summary(finished, when), encoding="utf-8")
    payload = dispatch_json(finished)
    record.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _event(box.root, when, "task.finished", task, f"{result.state}")
    _event(box.root, when, "acta.written", task, str(outbox.relative_to(box.root)))
    _archive(box, payload, when)
    # The digest is written after the record, so the task that just ran is in it: a memory that
    # is one run behind would make every dispatch leave a stale index behind it.
    records.write(box)
    return finished


def _archive(box: boxes.Box, payload: dict[str, object], when: datetime) -> None:
    """Project a closed change into the box's archive, and say so either way.

    The verdict is already written; a failure here cannot change it, and swallowing the failure
    would hide a change whose reason was never kept. So the loss is an event with the reason.
    """
    try:
        folder = archivum.write(box.root, payload, when)
    except (OSError, ValueError) as error:
        _event(
            box.root,
            when,
            "archive.failed",
            _payload_task(box, payload),
            f"the change was closed but not archived: {error}",
        )
        return
    if folder is None:
        return
    _event(
        box.root,
        when,
        "archive.written",
        _payload_task(box, payload),
        str(folder.relative_to(box.root)),
    )


def _payload_task(box: boxes.Box, payload: dict[str, object]) -> Task:
    """A task stand-in for an event about the record: the id is all the event needs."""
    name = str(payload.get("task") or "?")
    return Task(
        path=box.root / "tabularium" / name / SNAPSHOT_FILE,
        id=name,
        goal=None,
        kind=None,
        paths=(),
        expected=None,
        change=None,
        forbidden=(),
        budget={},
    )


def _timings(result: Dispatch, began: float) -> dict[str, float]:
    """Where the wall time went, measured around each stage and leaving the rest named.

    The executor, the gate, the checks and the commit report their own seconds; ``other`` is
    the box's work around them (contract, context, records), computed from the difference. A
    stage that did not run is absent, not zero: "not asked" and "asked and took no time" are
    different facts, and only one of them belongs in a budget conversation.
    """
    total = round(time.monotonic() - began, 2)
    timings: dict[str, float] = {}
    proposal = result.proposal
    if result.steps:
        # A steps run's timings are sums: the last call's seconds would name one step as if it
        # were the work, and the acta would understate what the loop cost in wall time.
        calls = sum(_seconds(step.get("seconds")) for step in result.steps)
        gates = sum(_seconds(step.get("gate_seconds")) for step in result.steps)
        if calls:
            timings["executor"] = round(calls, 2)
        if gates:
            timings["gate"] = round(gates, 2)
    elif proposal is not None and proposal.usage is not None and proposal.usage.seconds:
        timings["executor"] = round(proposal.usage.seconds, 2)
    if not result.steps and result.attempt is not None and result.attempt.seconds:
        timings["gate"] = round(result.attempt.seconds, 2)
    if result.verify is not None:
        timings["verify"] = round(result.verify.seconds, 2)
    if result.commit is not None and result.commit.seconds:
        timings["commit"] = round(result.commit.seconds, 2)
    timings["total"] = total
    known = sum(value for key, value in timings.items() if key != "total")
    timings["other"] = round(max(0.0, total - known), 2)
    return timings


def _seconds(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _time_line(result: Dispatch) -> str:
    """The timing as one line for a person: the total first, the parts after it."""
    if not result.timings:
        return "not measured"
    total = result.timings.get("total", 0.0)
    parts = [
        f"{key} {value:.2f} s"
        for key, value in result.timings.items()
        if key != "total" and value
    ]
    return f"{total:.2f} s" + (f" ({', '.join(parts)})" if parts else "")


def _summary(result: Dispatch, when: datetime) -> str:
    task = result.task
    lines = [
        f"# {task.id} — {task.kind} — {task.role} — {result.state}",
        "",
        f"at: {when.isoformat().replace('+00:00', 'Z')}",
        f"goal: {task.goal or 'not declared'}",
        f"scope: {', '.join(task.paths) or 'nothing declared'}",
        f"expected: {task.expected or 'not declared'}",
        f"resumed: {result.resumed_from or 'no — this dispatch started the task'}",
    ]
    if task.contract is not None:
        for key in ("symptom", "visible", "boundaries", "missing"):
            value = getattr(task.contract, key)
            if value is not None:
                lines.append(f"contract {key}: {value}")
    if task.parent is not None:
        lines.append(f"parent: {task.parent} — {task.why or 'no reason recorded'}")
    if result.stop_reason is not None:
        lines.append(f"stopped: {result.stop_reason}")
    failed = failure_class(result)
    if failed is not None:
        lines.append(f"class: {failed}")
    amendment = _amendment(result.directory)
    if amendment is not None:
        lines += ["", "## what changed from the parent"]
        changes = amendment.get("changes")
        if isinstance(changes, list) and changes:
            for change in changes:
                if isinstance(change, dict):
                    lines.append(f"- {change_line(change)}")
        else:
            lines.append("- the amendment record names no difference")
    lines += [
        "",
        "## what ran",
        f"- frame: {_frame_line(result.frame)}",
        f"- rails: {_rails_line(result.rails)}",
        f"- architecture: {_arch_line(result.arch)}",
        f"- level: {_level_line(result)}",
        f"- engine: {_engine_line(result)}",
        f"- executor: {_executor_line(result.proposal)}",
        f"- context: {_context_line(result.context)}",
        f"- write gate: {_attempt_line(result.attempt)}",
        f"- verification: {_verify_line(result.verify, result.review_skipped)}",
        f"- commit: {_commit_line(result.commit)}",
        "",
        "## what we did not check",
    ]
    unknowns = _unknowns(result)
    lines.extend(f"- {unknown}" for unknown in unknowns) if unknowns else lines.append("- none")
    lines += [
        "",
        "## open questions",
    ]
    questions = _questions(result)
    lines.extend(f"- {question}" for question in questions) if questions else lines.append("- none")
    lines += [
        "",
        "## cost",
    ]
    lines.extend(_cost_lines(result))
    lines += ["", "## time", f"- total: {result.timings.get('total', 0.0):.2f} s"]
    for key, value in result.timings.items():
        if key != "total" and value:
            lines.append(f"- {key}: {value:.2f} s")
    lines += ["", f"record: {result.directory}"]
    return "\n".join(lines) + "\n"


def _unknowns(result: Dispatch) -> list[str]:
    """What this dispatch did not establish, said before anyone reads the word "closed".

    A verdict is only as wide as what it looked at: a checks-only run says nothing about the
    change, a reused run says nothing new at all, and an unchecked architecture is not a
    passing one. These lines exist so "closed" cannot be read as "everything was seen".
    """
    unknown: list[str] = []
    task = result.task
    if result.frame is not None:
        gap = result.frame.gap()
        if gap is not None:
            unknown.append(gap)
    if result.rails is not None:
        gap = result.rails.gap()
        if gap is not None:
            unknown.append(gap)
    if result.arch is not None and result.arch.status != "ok":
        unknown.append(f"the architecture was not confirmed ({result.arch.status})")
    if result.verify is None:
        if "verify" in result.stages:
            unknown.append("verification was routed but left no result")
        else:
            unknown.append("verification was not routed: nothing was checked")
    else:
        if result.verify.reused_from is not None:
            unknown.append(
                "the checks did not run in this dispatch: the verdict is the one computed "
                "earlier for the same tree, policy, checker and change "
                f"({result.verify.reused_from})"
            )
        review = result.verify.review
        if result.review_skipped is not None:
            unknown.append(
                f"the change was not read by declaration: {result.review_skipped}"
            )
        elif review is None:
            unknown.append(
                "no reviewer read the change: the verdict is about the checks, not about what "
                "the change does"
            )
        elif not review.read:
            unknown.append(
                "the reviewer was asked and failed: the change was not read "
                f"({review.error or 'no reason recorded'})"
            )
        else:
            # What the reading did not cover, and what it cost: two different gaps. One must
            # not hide the other — a cut reading whose bill crossed the ceiling is both.
            if review.truncated:
                unknown.append(
                    "the change was cut before the reviewer saw it: the reading covers a part "
                    "of the change, not all of it, and the count of files says nothing about "
                    "the rest"
                )
            elif (
                review.files_reviewed is not None
                and result.attempt is not None
                and result.attempt.paths
                and review.files_reviewed < len(result.attempt.paths)
            ):
                unknown.append(
                    f"the reviewer was shown {review.files_reviewed} of the "
                    f"{len(result.attempt.paths)} changed files: the rest did not fit the "
                    "reading budget, so a finding about them could not have been confirmed"
                )
            if review.cost_cents is None and not review.cached:
                unknown.append(
                    "the reviewer did not report what it cost: the reading is not accounted "
                    "for in any budget"
                )
            elif (
                review.cost_cents is not None
                and result.review_cap is not None
                and review.cost_cents > result.review_cap
            ):
                unknown.append(
                    f"the reading cost {review.cost_cents:.2f} cents, more than the "
                    f"{result.review_cap:g} the box declared: the call had already happened, "
                    "so it is recorded as a gap rather than refused"
                )
        if result.verify.status != "pass":
            unknown.append(f"the verdict is {result.verify.status}, not pass")
    if result.attempt is not None and result.attempt.applied and "verify" not in result.stages:
        unknown.append("the change is in the tree and was never verified")
    if result.commit is not None and not result.commit.made:
        unknown.append(
            f"the change is in the tree and not in git: {result.commit.reason}"
        )
    usage = result.proposal.usage if result.proposal is not None else None
    if usage is not None and usage.cost_cents is None:
        unknown.append("the executor reported no cost: the budget could not be checked")
    if not task.budget:
        unknown.append("the task declared no budget: nothing limited this run")
    return unknown


def _questions(result: Dispatch) -> list[str]:
    questions: list[str] = []
    if result.attempt is None and "run" in result.stages:
        questions.append(
            "the task declares no change and the box declares no executor: who produces the patch?"
        )
    if result.proposal is not None and not result.proposal.usable:
        questions.append(f"the executor did not deliver: {result.proposal.reason}")
    if result.arch is not None and result.arch.status == "unchecked":
        questions.extend(result.arch.notes)
    if result.verify is None:
        questions.append("nothing was verified for this task: is that intended?")
    elif not result.verify.passed:
        questions.extend(result.verify.reasons or ["the verification did not pass"])
    return questions


def _shape_problem(shape: executor.Shape, patch: Path) -> str | None:
    """Is the answer the size the task said it would be? A fact, not a rewrite of the answer.

    The shape is the box's control on the answer's scheme (D37): a patch that restates the file
    is not a patch, however good its lines are, and applying it would teach the box to accept
    the wrong shape. The refusal names both numbers, so a retry knows what to shrink.
    """
    try:
        text = patch.read_text(encoding="utf-8")
    except OSError as error:
        return f"the proposed change cannot be read, so its shape cannot be checked: {error}"
    files, hunks, changed = caesar.diff_size(text)
    over: list[str] = []
    if shape.max_files is not None and files > shape.max_files:
        over.append(f"{files} file(s) for at most {shape.max_files}")
    if shape.max_changed_lines is not None and changed > shape.max_changed_lines:
        over.append(f"{changed} changed line(s) for at most {shape.max_changed_lines}")
    if not over:
        return None
    return (
        f"the answer is out of shape: {shape.name}, and it carries "
        + " and ".join(over)
        + f" ({hunks} hunk(s)): the change was not applied"
    )


def _shape_line(shape: dict[str, object] | None) -> str:
    if shape is None:
        return "not declared — the change's size is its own declaration"
    bits: list[str] = []
    lines = shape.get("max_changed_lines")
    if isinstance(lines, int):
        bits.append(f"at most {lines} changed line(s)")
    files = shape.get("max_files")
    if isinstance(files, int):
        bits.append(f"at most {files} file(s)")
    return f"{shape.get('name')} — " + (", ".join(bits) if bits else "no size limit declared")


def _own_check_line(check: dict[str, object] | None, declared: tuple[str, ...]) -> str:
    if check is None:
        return "declared, not run: no change was applied" if declared else "the task names none"
    return f"{check.get('status')} — {check.get('tail')}"


def _executor_line(result: executor.Proposal | None) -> str:
    if result is None:
        return "not run"
    return result.line


def _reading_line(result: executor.Proposal | None) -> str:
    """What the call read, or the fact that nobody counted: never a silent blank."""
    if result is None:
        return "not run"
    reading = result.reading
    if reading is None:
        return "not reported — the executor did not say what it read"
    files = reading.get("files")
    chars = reading.get("chars")
    tokens = reading.get("tokens_estimate")
    text = f"{files} file(s), {chars} chars (~{tokens} tokens)"
    declared = reading.get("declared_chars")
    if declared is not None:
        text += f", budget {declared} chars"
    left = reading.get("left_out")
    if isinstance(left, list) and left:
        text += "; left out: " + "; ".join(str(item) for item in left)
    return text


def _cost_lines(result: Dispatch) -> list[str]:
    """What the dispatch spent, in the outbox: money, tokens, and the budget it was given.

    The executor and the reader are two separate bills, and each is reported even when the
    other did not run: a review that cost a cent is not "0 — no model was called".
    """
    proposal = result.proposal
    review = result.verify.review if result.verify is not None else None
    lines: list[str] = []
    if proposal is None:
        lines.append(
            "0 — no executor was asked in this dispatch: the change came from the task itself."
        )
    else:
        usage = proposal.usage
        if usage is None:
            lines.append("unknown — the executor reported no usage: the cost is not zero.")
        else:
            money = (
                "cost not reported" if usage.cost_cents is None else f"{usage.cost_cents:.2f} cents"
            )
            lines.append(
                f"{money} ({executor.tokens_line(usage)}, model {usage.model or 'not named'}, "
                f"{usage.seconds:.1f} s)"
            )
    if proposal is not None and proposal.reading is not None:
        lines.append("reading: " + _reading_line(proposal))
    if review is not None:
        lines.append(f"review: {review.line()}")
    elif result.verify is not None and not result.verify.reviewed:
        lines.append("review: none — the change was not read.")
    if proposal is not None:
        outcome = proposal.status + (f" — {proposal.reason}" if proposal.reason else "")
        lines.append(f"outcome: {outcome}")
    if result.task.budget:
        lines.append("budget declared in the task: " + ", ".join(_budget_bits(result.task.budget)))
    return lines


def _budget_bits(budget: dict[str, int]) -> list[str]:
    bits: list[str] = []
    if "max_cents" in budget:
        bits.append(f"{budget['max_cents']} cents")
    if "max_seconds" in budget:
        bits.append(f"{budget['max_seconds']} s")
    if "max_read_chars" in budget:
        bits.append(f"{budget['max_read_chars']} chars read")
    return bits


def _arch_line(result: aurelius.Result | None) -> str:
    if result is None:
        return "not run"
    return f"{result.status} ({result.kept} kept, {result.broken} broken)"


def _level_line(result: Dispatch) -> str:
    """The one-shot/steps/agent level, when the box declares a policy that fixes it."""
    if result.level is None:
        return "not declared: the box has no execution policy, so the level is not enforced"
    return f"level {result.level} — {result.route_reason}"


def _engine_line(result: Dispatch) -> str:
    """The engine, and for a steps run what the steps came to: counts, not a story."""
    if not result.steps:
        return result.engine
    applied = sum(1 for step in result.steps if step.get("status") == "applied")
    refused = sum(1 for step in result.steps if step.get("status") == "refused")
    bits = [f"{len(result.steps)} step(s)", f"{applied} applied"]
    if refused:
        bits.append(f"{refused} refused")
    last = result.steps[-1]
    check = last.get("check")
    if isinstance(check, dict):
        bits.append(f"last visible check {check.get('status')}")
    return f"{result.engine} ({', '.join(bits)})"


def _attempt_line(result: caesar.Attempt | None) -> str:
    if result is None:
        return "not run"
    suffix = f" — {result.reason}" if result.reason else ""
    return f"{result.status} (attempt {result.number}, {len(result.paths)} path(s)){suffix}"


def _verify_line(
    result: contour.VerifyResult | None, skipped: str | None = None
) -> str:
    if result is None:
        return "not run"
    ran = result.checks - len(result.skipped)
    line = f"{result.status} ({ran} of {result.checks} checks ran, report {result.report})"
    if result.skipped:
        line += f"; not selected: {', '.join(result.skipped)}"
    if result.reused_from is not None:
        line += f", reused from {result.reused_from}"
    if result.review is not None:
        line += f"; read by {result.review.line()}"
    elif skipped:
        line += f"; not read by declaration ({skipped})"
    elif not result.reviewed:
        line += "; the change was not read"
    return line


def _event(
    box_root: Path,
    when: datetime,
    kind: str,
    task: Task,
    detail: str,
    data: dict[str, object] | None = None,
) -> None:
    ledger = box_root / "fasti"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    event: dict[str, object] = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": kind,
        "at": when.isoformat().replace("+00:00", "Z"),
        "task": task.id,
        "actor": "augustus",
        "reason": detail,
        "links": [f"tasks/{task.id}.contract.toml"],
    }
    if data is not None:
        event["data"] = data
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _identifier(value: object, path: Path) -> str:
    if isinstance(value, str) and value.strip():
        identifier = value.strip()
        if "/" not in identifier and identifier not in {".", ".."}:
            return identifier
        raise boxes.HarnessError(f"{path}: id {identifier!r} is a name, not a path")
    return path.stem


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
