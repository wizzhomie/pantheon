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
from dataclasses import dataclass, field
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
) -> Dispatch:
    """Run the task through its contours, write the record and the outbox summary."""
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
    if extended:
        notes.append(
            "the route for this kind does not include the write gate, but the task declares "
            "a change: the gate is not optional, so it was added"
        )
    arch: aurelius.Result | None = None
    attempt: caesar.Attempt | None = None
    verify: contour.VerifyResult | None = None
    contract_path = _write_contract(box.root, task)
    _event(box.root, stamp, "task.dispatched", task, " -> ".join(stages))

    if "arch" in stages:
        arch = _check_architecture(box, tool)
        notes.extend(arch.notes)
        if arch.status == "broken":
            state = "returned"
            notes.append("the architecture is broken: the change does not start")
            return _finish(
                box,
                task,
                Dispatch(task, state, stages, None, None, arch, None, directory, notes, 1),
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
                            task,
                            "returned",
                            stages,
                            None,
                            None,
                            arch,
                            None,
                            directory,
                            notes,
                            1,
                            proposal,
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
                        task,
                        "returned",
                        stages,
                        attempt,
                        None,
                        arch,
                        None,
                        directory,
                        notes,
                        1,
                        proposal,
                    ),
                    stamp,
                )

    if "verify" in stages:
        out = directory / f"verify-{stamp:%Y%m%dT%H%M%SZ}"
        verify = contour.run_verify(
            box.project, root=trajan_root, python=trajan_python, out=out
        )
        notes.extend(verify.reasons)
    else:
        notes.append("verification was not routed for this task: nothing was checked")

    state = _state(verify, arch)
    exit_code = 0 if state == "closed" else 1
    return _finish(
        box,
        task,
        Dispatch(
            task, state, stages, attempt, verify, arch, None, directory, notes, exit_code, proposal
        ),
        stamp,
    )


@dataclass(frozen=True, slots=True)
class TaskSummary:
    """The projection of one task: what its newest dispatch said, and when."""

    id: str
    state: str
    at: str
    verify: str | None
    record: Path


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
        found.append(
            TaskSummary(
                id=str(data.get("task", task_dir.name)),
                state=str(data.get("state", "unknown")),
                at=str(data.get("at", "")) or records[-1].stem.removeprefix("dispatch-"),
                verify=str(verify.get("status")) if isinstance(verify, dict) else None,
                record=records[-1],
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
            escaped = value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))
            lines.append(f'{key} = "{escaped}"')
    lines.append("allowed_paths = [" + ", ".join(f'"{item}"' for item in task.paths) + "]")
    if task.forbidden:
        lines.append("forbidden = [" + ", ".join(f'"{item}"' for item in task.forbidden) + "]")
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
        "",
        "## what ran",
        f"- architecture: {_arch_line(result.arch)}",
        f"- executor: {_executor_line(result.proposal)}",
        f"- write gate: {_attempt_line(result.attempt)}",
        f"- verification: {_verify_line(result.verify)}",
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
    return f"{result.status} ({result.checks} checks, report {result.report})"


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
