"""Augustus: the task goes through the box, and the box's record says what happened.

A task is a file a person (or, later, an agent) writes: a goal, a declared scope, a kind, a
criterion, and the change it expects. Augustus does not invent work and does not judge the
answer — it validates the task, picks contours by the declared fields through a table, calls
them in order (architecture, the write gate, verification), and writes the projection: one
record per dispatch, one summary per finished task in ``outbox/``, events in the ledger.

What is missing is named, not hidden: there is no executor yet, so a task either carries the
patch it wants applied (``change = "..."``) or explicitly says the change is not the subject
of this dispatch. The outbox says which of the two happened, because "nothing was run" and
"everything passed" must never look the same in the record.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from . import aurelius, caesar, contour, executor
from . import box as boxes

SCHEMA_VERSION = 1
TASK_KEYS = frozenset(
    {"schema_version", "id", "goal", "kind", "paths", "expected", "change", "forbidden", "budget"}
)
BUDGET_KEYS = frozenset({"max_seconds", "max_cents"})
ROUTES_ENV = "PANTHEON_ROUTES"
SNAPSHOT_FILE = "task.json"
"""The task as dispatched, frozen beside its artifacts: a resume reads this, not the file."""

DEFAULT_ROUTES: dict[str, tuple[str, ...]] = {
    "docs": ("verify",),
    "bugfix": ("run", "verify"),
    "feature": ("arch", "run", "verify"),
    "architecture": ("arch", "run", "verify"),
    "security": ("arch", "run", "verify"),
}
"""Scope kind to contours. A table, not a model call: the same task must route the same way."""
KIND_HINT = ", ".join(sorted(DEFAULT_ROUTES))


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
    return Task(
        path=path,
        id=identifier,
        goal=_text(data.get("goal")),
        kind=_text(data.get("kind")),
        paths=tuple(item.strip() for item in paths),
        expected=_text(data.get("expected")),
        change=Path(change).expanduser() if isinstance(change, str) else None,
        forbidden=tuple(forbidden),
        budget={key: int(value) for key, value in budget.items()},
    )


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


def refusal(task: Task, routes: dict[str, tuple[str, ...]]) -> str | None:
    """Why this task may not start, or ``None``. Scope and kind are not optional."""
    if not task.kind:
        return "the task declares no kind: a change without a type does not start"
    if not task.paths:
        return "the task declares no paths: nothing is in scope, so nothing may be done"
    if not route(task, routes):
        return f"no route for kind {task.kind!r}; known kinds: {', '.join(sorted(routes))}"
    return None


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
) -> Dispatch:
    """Run the task through its contours, write the record and the outbox summary.

    ``resumed_from`` is set when this is a continuation of an earlier run from the same frozen
    snapshot: the route is then run again from the start, because a retry is a new run of the
    same task, not a second half of the old one.
    """
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / "artifacts" / task.id
    directory.mkdir(parents=True, exist_ok=True)
    blocked = refusal(task, routes)
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
    stages, extended = _with_write_gate(route(task, routes), task)
    notes: list[str] = []
    if resumed_from is not None:
        notes.append(
            f"continued after {_relative(resumed_from, box.root)}: the change was asked for "
            "again, from the frozen task, because the recorded attempt produced nothing usable"
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
    contract_path = _write_contract(box.root, task)
    _event(
        box.root,
        stamp,
        "task.resumed" if resumed_from is not None else "task.dispatched",
        task,
        "retry" if resumed_from is not None else " -> ".join(stages),
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
            )

    proposal: executor.Proposal | None = None
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
                proposal = executor.propose(config, task, box, when=stamp)
                notes.extend(proposal.notes)
                _event(box.root, stamp, "task.executed", task, proposal.line, proposal.data())
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
                        ),
                        stamp,
                    )
                patch = proposal.patch
        if patch is not None:
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
                    ),
                    stamp,
                )

    if "verify" in stages:
        verify = _verify(box, directory, stamp, trajan_root, trajan_python)
        notes.extend(verify.reasons)
    else:
        notes.append("verification was not routed for this task: nothing was checked")

    state = _state(verify, arch)
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
            directory=directory,
            notes=notes,
            exit_code=0 if state == "closed" else 1,
            proposal=proposal,
            resumed_from=resumed_from,
        ),
        stamp,
    )


def _snapshot(box: boxes.Box, task: Task, when: datetime) -> Task:
    """Freeze the task beside its artifacts, so a dispatch can be continued later.

    The change file is copied too: the record must not depend on a file outside the box, and
    editing the original task afterwards must not rewrite what already ran.
    """
    directory = box.root / "artifacts" / task.id
    directory.mkdir(parents=True, exist_ok=True)
    change: Path | None = None
    if task.change is not None:
        try:
            data = task.change.read_bytes()
        except OSError as error:
            raise boxes.HarnessError(f"cannot read the patch {task.change}: {error}") from error
        change = directory / "change.diff"
        change.write_bytes(data)
    lines = [
        "# Frozen by `pantheon dispatch`: the task these artifacts belong to.",
        "# `pantheon resume` reads this file, not the original: editing the task must not",
        "# rewrite what already ran.",
        f"schema_version = {SCHEMA_VERSION}",
        f"id = {_quote(task.id)}",
    ]
    for key, value in (("goal", task.goal), ("kind", task.kind), ("expected", task.expected)):
        if value is not None:
            lines.append(f"{key} = {_quote(value)}")
    lines.append("paths = [" + ", ".join(_quote(item) for item in task.paths) + "]")
    if change is not None:
        lines.append(f"change = {_quote(str(change))}")
    if task.forbidden:
        lines.append("forbidden = [" + ", ".join(_quote(item) for item in task.forbidden) + "]")
    if task.budget:
        lines += ["", "[budget]"] + [f"{key} = {value}" for key, value in task.budget.items()]
    (directory / SNAPSHOT_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return replace(task, path=directory / SNAPSHOT_FILE, change=change)


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
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / "artifacts" / task_id
    snapshot = directory / SNAPSHOT_FILE
    if not snapshot.is_file():
        raise boxes.HarnessError(
            f"{task_id}: no {SNAPSHOT_FILE} in {directory}: there is nothing to continue from. "
            "`pantheon dispatch` freezes the task when it runs it; a dispatch from before that "
            "file existed cannot be continued — run the task again."
        )
    task = read_task(snapshot)
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
                    "nothing to continue: the task is closed "
                    f"({_relative(record_path, box.root)})"
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

    if retry:
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
            )

    verify = _verify(box, directory, stamp, trajan_root, trajan_python)
    notes.extend(verify.reasons)
    state = _state(verify, None)
    return _finish(
        box,
        task,
        Dispatch(
            task=task,
            state=state,
            stages=stages,
            attempt=attempt,
            verify=verify,
            directory=directory,
            notes=notes,
            exit_code=0 if state == "closed" else 1,
            proposal=proposal,
            resumed_from=source,
        ),
        stamp,
    )


def _verify(
    box: boxes.Box, directory: Path, stamp: datetime, trajan_root: Path, trajan_python: Path
) -> contour.VerifyResult:
    out = contour.run_directory(directory, stamp)
    return contour.run_verify(
        box.project, root=trajan_root, python=trajan_python, out=out, box_root=box.root
    )


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
    return '"' + value.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"') + '"'


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """The projection of one task: what its newest dispatch said, and when."""

    id: str
    state: str
    at: str
    verify: str | None
    record: Path
    unknowns: tuple[str, ...] = ()


def recent_tasks(box_root: Path, limit: int = 10) -> tuple[TaskSummary, ...]:
    """The newest dispatch record per task, oldest first: what ``status`` may say."""
    base = box_root / "artifacts"
    if not base.is_dir():
        return ()
    found: list[TaskSummary] = []
    for task_dir in sorted(base.iterdir()):
        if not task_dir.is_dir():
            continue
        records = sorted(task_dir.glob("dispatch-*.json"))
        if not records:
            continue
        try:
            data = json.loads(records[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        verify = data.get("verify")
        unknowns = data.get("unknowns")
        found.append(
            TaskSummary(
                id=str(data.get("task", task_dir.name)),
                state=str(data.get("state", "unknown")),
                at=str(data.get("at", "")) or records[-1].stem.removeprefix("dispatch-"),
                verify=str(verify.get("status")) if isinstance(verify, dict) else None,
                record=records[-1],
                unknowns=tuple(str(item) for item in unknowns)
                if isinstance(unknowns, list)
                else (),
            )
        )
    return tuple(found[-limit:])


def render_dispatch(result: Dispatch) -> list[str]:
    """The dispatch as a person reads it."""
    lines = [
        f"task       {result.task.id}  ({result.task.path})",
        f"kind       {result.task.kind or 'not declared'}",
        f"scope      {', '.join(result.task.paths) or 'nothing declared'}",
        f"stages     {' -> '.join(result.stages) or 'none'}",
        f"arch       {_arch_line(result.arch)}",
        f"executor   {_executor_line(result.proposal)}",
        f"attempt    {_attempt_line(result.attempt)}",
        f"verify     {_verify_line(result.verify)}",
        f"state      {result.state}",
    ]
    if result.resumed_from is not None:
        lines.append(f"resumed    from {result.resumed_from}")
    for note in result.notes:
        lines.append(f"note       {note}")
    if result.outbox is not None:
        lines.append(f"outbox     {result.outbox}")
    return lines


def dispatch_json(result: Dispatch) -> dict[str, object]:
    """The same answer for a machine."""
    return {
        "task": result.task.id,
        "kind": result.task.kind,
        "paths": list(result.task.paths),
        "stages": list(result.stages),
        "arch": None
        if result.arch is None
        else {"status": result.arch.status, "kept": result.arch.kept, "broken": result.arch.broken},
        "attempt": None if result.attempt is None else caesar.attempt_json(result.attempt),
        "budget": dict(result.task.budget),
        "executor": None if result.proposal is None else executor.proposal_json(result.proposal),
        "verify": None
        if result.verify is None
        else {
            "status": result.verify.status,
            "report": str(result.verify.report),
            "exit_code": result.verify.exit_code,
        },
        "state": result.state,
        "resumed_from": None if result.resumed_from is None else str(result.resumed_from),
        "unknowns": _unknowns(result),
        "notes": list(result.notes),
        "outbox": None if result.outbox is None else str(result.outbox),
        "exit_code": result.exit_code,
    }


def _with_write_gate(
    stages: tuple[str, ...], task: Task
) -> tuple[tuple[str, ...], bool]:
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


def _check_architecture(box: boxes.Box, tool: str | None) -> aurelius.Result:
    model = box.project / aurelius.MODEL_FILE
    if not model.is_file():
        return aurelius.Result(
            status="unchecked",
            kept=0,
            broken=0,
            tool="",
            version="",
            directory=box.root / "artifacts" / "arch",
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
    path = box_root / "tasks" / f"{task.id}.contract.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _finish(box: boxes.Box, task: Task, result: Dispatch, when: datetime) -> Dispatch:
    record = result.directory / f"dispatch-{when:%Y%m%dT%H%M%SZ}.json"
    outbox = box.root / "outbox" / f"{when:%Y-%m-%d}-{task.id}.md"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text(_summary(result, when), encoding="utf-8")
    finished = Dispatch(
        task=result.task,
        state=result.state,
        stages=result.stages,
        attempt=result.attempt,
        verify=result.verify,
        arch=result.arch,
        outbox=outbox,
        directory=result.directory,
        notes=result.notes,
        exit_code=result.exit_code,
        proposal=result.proposal,
        resumed_from=result.resumed_from,
    )
    record.write_text(json.dumps(dispatch_json(finished), indent=2) + "\n", encoding="utf-8")
    _event(box.root, when, "task.finished", task, f"{result.state}")
    _event(box.root, when, "outbox.written", task, str(outbox.relative_to(box.root)))
    return finished


def _summary(result: Dispatch, when: datetime) -> str:
    task = result.task
    lines = [
        f"# {task.id} — {task.kind} — {result.state}",
        "",
        f"at: {when.isoformat().replace('+00:00', 'Z')}",
        f"goal: {task.goal or 'not declared'}",
        f"scope: {', '.join(task.paths) or 'nothing declared'}",
        f"expected: {task.expected or 'not declared'}",
        f"resumed: {result.resumed_from or 'no — this dispatch started the task'}",
        "",
        "## what ran",
        f"- architecture: {_arch_line(result.arch)}",
        f"- executor: {_executor_line(result.proposal)}",
        f"- write gate: {_attempt_line(result.attempt)}",
        f"- verification: {_verify_line(result.verify)}",
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
                f"earlier for the same tree ({result.verify.reused_from})"
            )
        if not result.verify.reviewed:
            unknown.append(
                "no reviewer read the change: the verdict is about the checks, not about what "
                "the change does"
            )
        if result.verify.status != "pass":
            unknown.append(f"the verdict is {result.verify.status}, not pass")
    if result.attempt is not None and result.attempt.applied and "verify" not in result.stages:
        unknown.append("the change is in the tree and was never verified")
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


def _executor_line(result: executor.Proposal | None) -> str:
    if result is None:
        return "not run"
    return result.line


def _cost_lines(result: Dispatch) -> list[str]:
    """What the dispatch spent, in the outbox: money, tokens, and the budget it was given."""
    proposal = result.proposal
    if proposal is None:
        return [
            "0 — no model was called in this dispatch: the contours here are deterministic, "
            "and no change was asked of an executor."
        ]
    usage = proposal.usage
    if usage is None:
        first = "unknown — the executor reported no usage: the cost is unknown, not zero."
    else:
        money = (
            "cost not reported"
            if usage.cost_cents is None
            else f"{usage.cost_cents:.2f} cents"
        )
        first = (
            f"{money} ({executor.tokens_line(usage)}, model {usage.model or 'not named'}, "
            f"{usage.seconds:.1f} s)"
        )
    outcome = proposal.status + (f" — {proposal.reason}" if proposal.reason else "")
    lines = [first, f"outcome: {outcome}"]
    if result.task.budget:
        lines.append("budget declared in the task: " + ", ".join(_budget_bits(result.task.budget)))
    return lines


def _budget_bits(budget: dict[str, int]) -> list[str]:
    bits: list[str] = []
    if "max_cents" in budget:
        bits.append(f"{budget['max_cents']} cents")
    if "max_seconds" in budget:
        bits.append(f"{budget['max_seconds']} s")
    return bits


def _arch_line(result: aurelius.Result | None) -> str:
    if result is None:
        return "not run"
    return f"{result.status} ({result.kept} kept, {result.broken} broken)"


def _attempt_line(result: caesar.Attempt | None) -> str:
    if result is None:
        return "not run"
    suffix = f" — {result.reason}" if result.reason else ""
    return f"{result.status} (attempt {result.number}, {len(result.paths)} path(s)){suffix}"


def _verify_line(result: contour.VerifyResult | None) -> str:
    if result is None:
        return "not run"
    line = f"{result.status} ({result.checks} checks, report {result.report})"
    if result.reused_from is not None:
        line += f", reused from {result.reused_from}"
    return line


def _event(
    box_root: Path,
    when: datetime,
    kind: str,
    task: Task,
    detail: str,
    data: dict[str, object] | None = None,
) -> None:
    ledger = box_root / "ledger"
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
