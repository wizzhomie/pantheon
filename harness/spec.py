"""The spec package: documents from an outside process, frozen by a copy and accepted by record.

A package is a directory a person (or an outside process writing in quarantine) produces:
``manifest.toml`` names the package and its tasks, ``spec.md`` carries the intent, and the
rest of the directory is evidence. Nothing here runs that process and nothing parses its
checklists: the harness reads only the manifest, and a task without a ``repro`` is refused
because a criterion that cannot be checked is not one.

Importing copies the directory into the box under ``tabularium/spec/<id>/`` and writes one
``mandata/<task>.toml`` per package task; the copy is frozen afterwards. Acceptance is a
record, not a field: ``tabularium/spec/<id>.json`` carries the digest of the copy, its files,
its tasks, who accepted it and the owner's words. A copy edited after acceptance is a fact
with two digests, which is what ``dispatch`` refuses a feature task on.

The routing table belongs to :mod:`harness.augustus`; the kind list here is a copy kept in
step on purpose, because importing ``augustus`` from this module would close a cycle (Augustus
imports this one to gate a dispatch).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import box as boxes

SCHEMA_VERSION = 1
PACKAGES = Path("tabularium") / "spec"
MANIFEST = "manifest.toml"

MANIFEST_KEYS = frozenset(
    {"schema_version", "id", "milestone", "source", "why", "spec", "task", "campaign"}
)
CAMPAIGN_KEYS = frozenset({"goal", "acceptance", "mode", "attempts", "budget"})
ACCEPTANCE_KEYS = frozenset({"id", "statement", "step"})
BUDGET_KEYS = frozenset({"max_cents", "max_seconds"})
MODES = ("manual", "auto")
TASK_KEYS = frozenset(
    {"id", "kind", "goal", "paths", "expected", "repro", "self_check", "contract"}
)
CONTRACT_KEYS = frozenset({"symptom", "visible", "boundaries", "missing"})
"""The owner's words around the criterion: the same four points ``pantheon intake`` reads."""

KINDS = frozenset({"docs", "hotfix", "bugfix", "feature", "architecture", "security"})
"""The kinds Augustus routes; ``augustus.DEFAULT_ROUTES`` is the table, this is a copy.

A package claiming a kind the routing table does not know would be imported and then refused
as unrouteable, so the import refuses it instead of freezing work that cannot start.
"""

NEVER = "never"
ACCEPTED = "accepted"
CHANGED = "changed"


class Declared(Protocol):
    """The fields the gate reads from a task, so this module needs no import cycle."""

    @property
    def id(self) -> str: ...

    @property
    def kind(self) -> str | None: ...

    @property
    def spec(self) -> str | None: ...

    @property
    def spec_task(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class FileDigest:
    """One package file and the bytes that were read from it."""

    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class Contract:
    """The owner's words around the criterion: symptom, what is seen, the boundaries.

    Without these three points ``pantheon intake`` refuses the task before any work, so a
    package that cannot name them is refused at import: a spec that cannot be handed on is not a
    spec, and freezing one would only postpone the refusal.
    """

    symptom: str
    visible: str
    boundaries: str
    missing: str | None = None


@dataclass(frozen=True, slots=True)
class Task:
    """One task the manifest declares: what it is and the check that says it is done."""

    id: str
    kind: str
    goal: str
    paths: tuple[str, ...]
    expected: str
    contract: Contract
    repro: tuple[str, ...]
    self_check: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Acceptance:
    """One statement the owner wants from the feature, and the step whose verdict covers it."""

    id: str
    statement: str
    step: str


@dataclass(frozen=True, slots=True)
class CampaignPlan:
    """The feature as a campaign: the goal, the acceptance, and how each step closes."""

    goal: str
    acceptance: tuple[Acceptance, ...]
    mode: str = "manual"
    attempts: int = 1
    budget: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class Package:
    """A package directory as it is on disk, with every file's digest and one tree digest."""

    id: str
    milestone: str
    source: str
    why: str
    spec: str
    tasks: tuple[Task, ...]
    files: tuple[FileDigest, ...]
    digest: str
    path: Path
    campaign: CampaignPlan | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    """A record a person wrote: this digest was the package they accepted."""

    path: Path
    at: str
    by: str
    note: str
    digest: str
    files: tuple[FileDigest, ...]
    tasks: tuple[str, ...]
    source: str
    milestone: str
    plan: Path | None = None


@dataclass(frozen=True, slots=True)
class Standing:
    """How the copy in the box stands against the acceptance record: accepted, changed, never."""

    state: str
    digest: str
    approval: Approval | None
    files: tuple[FileDigest, ...]
    package: Package

    @property
    def refusal(self) -> str | None:
        """What a feature task resting on this package does not have, or ``None`` when it does."""
        if self.state == ACCEPTED:
            return None
        if self.state == NEVER:
            return _never(self.package.id)
        assert self.approval is not None
        detail = _difference(self.approval.files, self.files)
        return (
            f"the spec package {self.package.id} changed after it was accepted (accepted "
            f"{self.approval.digest}, now {self.digest}): {detail}; re-accept it with "
            f'`pantheon spec {self.package.id} --accept --note "..."` only if the change is '
            "the one you mean"
        )

    def line(self) -> str:
        """The standing as one line for a person."""
        if self.state == ACCEPTED and self.approval is not None:
            return f"accepted {self.approval.at} by {self.approval.by} ({_short(self.digest)})"
        if self.state == CHANGED and self.approval is not None:
            return (
                f"changed since acceptance ({_short(self.approval.digest)} -> "
                f"{_short(self.digest)})"
            )
        return "never accepted"

    def json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "id": self.package.id,
            "digest": self.digest,
            "files": [{"path": item.path, "digest": item.digest} for item in self.files],
            "tasks": [item.id for item in self.package.tasks],
            "accepted": None
            if self.approval is None
            else {
                "at": self.approval.at,
                "by": self.approval.by,
                "digest": self.approval.digest,
                "note": self.approval.note,
                "record": str(self.approval.path),
            },
        }


def read_package(directory: Path) -> Package:
    """Read and validate a package directory; an unknown key or missing task stops the read."""
    path = directory / MANIFEST
    try:
        data: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise boxes.HarnessError(
            f"no spec package at {directory}: {path} does not exist"
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"{path}: the manifest does not parse: {error}") from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path}: the manifest must be a table")
    unknown = sorted(set(data) - MANIFEST_KEYS)
    if unknown:
        raise boxes.HarnessError(
            f"{path}: unknown key(s) {', '.join(unknown)}; the manifest reads "
            + ", ".join(sorted(MANIFEST_KEYS))
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version must be {SCHEMA_VERSION}, not "
            f"{data.get('schema_version')!r}"
        )
    identifier = _name(data.get("id"), f"{path}: id")
    milestone = _required(data, "milestone", path)
    source = _required(data, "source", path)
    why = _required(data, "why", path)
    spec_name = _required(data, "spec", path)
    if Path(spec_name).is_absolute() or ".." in Path(spec_name).parts:
        raise boxes.HarnessError(
            f"{path}: spec must name a file inside the package, not {spec_name!r}"
        )
    document = directory / spec_name
    if not document.is_file():
        raise boxes.HarnessError(
            f"{path}: the manifest names the spec file {spec_name!r}, but {document} is not a file"
        )
    raw_tasks = data.get("task")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise boxes.HarnessError(
            f"{path}: no [[task]] entries: a package with no task declares no work"
        )
    tasks: list[Task] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        task = _read_task(item, f"{path}: task #{index}")
        if task.id in seen:
            raise boxes.HarnessError(f"{path}: task {task.id!r} is declared twice")
        seen.add(task.id)
        tasks.append(task)
    files = _tree(directory)
    if not files:
        raise boxes.HarnessError(f"{path}: the package has no files")
    campaign = _read_campaign_block(data.get("campaign"), tuple(tasks), path)
    return Package(
        id=identifier,
        milestone=milestone,
        source=source,
        why=why,
        spec=spec_name,
        tasks=tuple(tasks),
        files=files,
        digest=_digest(files),
        path=directory,
        campaign=campaign,
    )


def _read_campaign_block(raw: object, tasks: tuple[Task, ...], path: Path) -> CampaignPlan | None:
    """The feature as a campaign, when the package offers one: steps are its own tasks."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise boxes.HarnessError(f"{path}: campaign must be a table")
    unknown = sorted(set(raw) - CAMPAIGN_KEYS)
    if unknown:
        raise boxes.HarnessError(
            f"{path}: campaign: unknown key(s) {', '.join(unknown)}; it reads "
            + ", ".join(sorted(CAMPAIGN_KEYS))
        )
    goal = _required(raw, "goal", path)
    raw_acceptance = raw.get("acceptance")
    if not isinstance(raw_acceptance, list) or not raw_acceptance:
        raise boxes.HarnessError(
            f"{path}: campaign has no acceptance: a feature nobody can accept is not planned"
        )
    names = {task.id for task in tasks}
    acceptance: list[Acceptance] = []
    for index, item in enumerate(raw_acceptance, start=1):
        where = f"{path}: campaign acceptance #{index}"
        if not isinstance(item, dict):
            raise boxes.HarnessError(f"{where} must be a table")
        unknown = sorted(set(item) - ACCEPTANCE_KEYS)
        if unknown:
            raise boxes.HarnessError(
                f"{where}: unknown key(s) {', '.join(unknown)}; it reads "
                + ", ".join(sorted(ACCEPTANCE_KEYS))
            )
        identifier = _name(item.get("id"), f"{where}: id")
        statement = _required(item, "statement", path)
        step = _required(item, "step", path)
        if step not in names:
            raise boxes.HarnessError(
                f"{where}: names step {step!r}, which the package does not declare; the steps "
                f"are {', '.join(sorted(names))}"
            )
        acceptance.append(Acceptance(id=identifier, statement=statement, step=step))
    mode = raw.get("mode", "manual")
    if mode not in MODES:
        raise boxes.HarnessError(
            f"{path}: campaign mode {mode!r} is not one of {', '.join(MODES)}"
        )
    attempts = raw.get("attempts", 1)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise boxes.HarnessError(f"{path}: campaign attempts must be a positive whole number")
    raw_budget = raw.get("budget", {})
    if not isinstance(raw_budget, dict):
        raise boxes.HarnessError(f"{path}: campaign budget must be a table")
    unknown = sorted(set(raw_budget) - BUDGET_KEYS)
    if unknown:
        raise boxes.HarnessError(
            f"{path}: campaign budget: unknown key(s) {', '.join(unknown)}; it reads "
            + ", ".join(sorted(BUDGET_KEYS))
        )
    budget: dict[str, int] = {}
    for key in sorted(BUDGET_KEYS):
        value = raw_budget.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise boxes.HarnessError(f"{path}: campaign budget {key} must be a positive number")
        budget[key] = value
    return CampaignPlan(
        goal=goal,
        acceptance=tuple(acceptance),
        mode=str(mode),
        attempts=attempts,
        budget=budget or None,
    )


def import_package(box: boxes.Box, package: Package) -> tuple[Path, ...]:
    """Freeze the package by copying it into the box and write one task file per package task.

    The copy is refused when the directory is already there: a package is frozen by its first
    import, and a changed one is a new id rather than a silent replacement. All target task
    files are checked before anything is copied, so a conflict leaves no half-import behind.
    """
    target = box.root / PACKAGES / package.id
    if target.exists():
        raise boxes.HarnessError(
            f"the package {package.id} is already imported: {target}; a package is frozen by "
            "its first copy, and a changed one is a new package rather than an overwrite"
        )
    mandata = box.root / "mandata"
    targets = {task.id: mandata / f"{task.id}.toml" for task in package.tasks}
    taken = sorted(str(path) for path in targets.values() if path.exists())
    if taken:
        raise boxes.HarnessError(
            f"the package {package.id} cannot be imported: the box already has "
            f"{', '.join(taken)}; a task file is not overwritten by a package"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            package.path,
            target,
            ignore=shutil.ignore_patterns("__pycache__", ".git"),
        )
    except OSError as error:
        raise boxes.HarnessError(
            f"the package {package.id} cannot be copied to {target}: {error}"
        ) from error
    mandata.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for task in package.tasks:
        path = targets[task.id]
        path.write_text(_task_text(task, package.id), encoding="utf-8")
        written.append(path)
    return tuple(written)


def accept(
    box: boxes.Box,
    package_id: str,
    *,
    by: str = "owner",
    note: str = "",
    when: datetime | None = None,
) -> Approval:
    """Write the owner's mark on the imported copy: its digest, files, tasks and words.

    An identical record — the same digest accepted by the same person with the same words — is
    returned untouched. A different mark replaces the record at the same address, because the
    acceptance is about this copy, not a history of every time it was looked at.
    """
    identifier = _name(package_id, "spec")
    directory = box.root / PACKAGES / identifier
    if not directory.is_dir():
        raise boxes.HarnessError(
            f"the spec package {identifier} is not imported: "
            f"`pantheon spec {identifier} --from <dir>` imports one before it can be accepted"
        )
    package = read_package(directory)
    if package.id != identifier:
        raise boxes.HarnessError(
            f"the copy at {directory} names the package {package.id!r}, not {identifier!r}: "
            "the directory and the manifest disagree"
        )
    record_path = _record_path(box, identifier)
    existing = _read_record(box, identifier)
    if (
        existing is not None
        and existing.digest == package.digest
        and existing.by == by
        and existing.note == note
    ):
        return existing
    plan = write_plan(box, package) if package.campaign is not None else None
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    record = {
        "schema_version": SCHEMA_VERSION,
        "id": package.id,
        "at": _stamp(stamp),
        "by": by,
        "note": note,
        "digest": package.digest,
        "files": [{"path": item.path, "digest": item.digest} for item in package.files],
        "tasks": [task.id for task in package.tasks],
        "source": package.source,
        "milestone": package.milestone,
        "plan": None if plan is None else str(plan),
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", "utf-8")
    _append_event(box, by, note, record_path, stamp, package.digest)
    return Approval(
        path=record_path,
        at=_stamp(stamp),
        by=by,
        note=note,
        digest=package.digest,
        files=package.files,
        tasks=tuple(task.id for task in package.tasks),
        source=package.source,
        milestone=package.milestone,
        plan=plan,
    )


def standing(box: boxes.Box, package_id: str) -> Standing:
    """The imported copy against the acceptance record: never accepted, changed, or accepted."""
    identifier = _name(package_id, "spec")
    directory = box.root / PACKAGES / identifier
    if not directory.is_dir():
        raise boxes.HarnessError(
            f"the spec package {identifier} is not imported: "
            f"`pantheon spec {identifier} --from <dir>` imports one"
        )
    package = read_package(directory)
    approval = _read_record(box, identifier)
    if approval is None:
        return Standing(NEVER, package.digest, None, package.files, package)
    state = ACCEPTED if approval.digest == package.digest else CHANGED
    return Standing(state, package.digest, approval, package.files, package)


def check(box: boxes.Box, task: Declared) -> str | None:
    """Why a feature task may not start, or ``None`` when its package is imported and accepted.

    Only a feature needs a package: ``architecture`` writes the frame itself, and the narrow
    kinds (``bugfix``, ``hotfix``, ``docs``, ``security``) go through their own gate. The
    refusals name the command that closes the gap, because a gate that only says "no" leaves
    the caller guessing.
    """
    if task.kind != "feature":
        return None
    if task.spec is None:
        return (
            "the task declares no spec: a feature does not start without an accepted spec "
            "package; `pantheon spec <id> --from <dir>` imports one and `pantheon spec <id> "
            '--accept --note "..."` accepts it'
        )
    package_id = task.spec.strip()
    if not package_id or "/" in package_id or "\\" in package_id or package_id in {".", ".."}:
        return (
            f"the task declares spec {task.spec!r}, which is not a package name: the spec is "
            "the id the manifest names, not a path"
        )
    directory = box.root / PACKAGES / package_id
    if not directory.is_dir():
        return (
            f"the spec package {package_id} is not imported: a feature rests on a package "
            f"inside the box, and `pantheon spec {package_id} --from <dir>` imports one"
        )
    current = standing(box, package_id)
    if current.refusal is not None:
        return current.refusal
    assert current.approval is not None
    wanted = task.spec_task.strip() if task.spec_task else task.id
    if not wanted:
        return (
            f"the task declares spec_task as an empty name: the id of a task inside the "
            f"package {package_id} is a name, and an empty one names nothing"
        )
    if wanted not in current.approval.tasks:
        named = ", ".join(current.approval.tasks) or "no tasks"
        if wanted == task.id:
            return (
                f"the task {task.id} is not in the spec package {package_id}: the package names "
                f"{named}; a task that the package does not name has no accepted criterion"
            )
        return (
            f"the task {task.id} names {wanted} inside the spec package {package_id}, and the "
            f"package names {named}; a task that the package does not name has no accepted "
            "criterion"
        )
    return None


def render_package(package: Package, written: tuple[Path, ...] = ()) -> list[str]:
    """An import as a person reads it: what arrived, what it holds, and how to accept it."""
    lines = [
        f"id         {package.id}",
        f"milestone  {package.milestone}",
        f"source     {package.source}",
        f"spec       {package.spec}",
        f"tasks      {', '.join(task.id for task in package.tasks)}",
        f"files      {len(package.files)}",
    ]
    for item in package.files:
        lines.append(f"  {item.path}  {item.digest}")
    lines.append(f"digest     {package.digest}")
    for path in written:
        lines.append(f"mandata    {path}")
    lines.append(
        f'accept     pantheon spec {package.id} --accept --note "..."'
    )
    return lines


render_import = render_package
"""The same reading under the name the import handler uses."""


def write_plan(box: boxes.Box, package: Package) -> Path:
    """Write the package's feature as a campaign plan in the project, once, after acceptance.

    The plan is the architect's desk, not a product change: its steps are the package tasks and
    its acceptance is the owner's words; the files it may touch are still the steps' own paths,
    which Caesar gates when the steps run.
    """
    plan = package.campaign
    if plan is None:
        raise boxes.HarnessError(
            f"the package {package.id} declares no campaign: there is no plan to write"
        )
    path = box.project / ".pantheon" / "expeditiones" / f"{package.id}.toml"
    if path.exists():
        raise boxes.HarnessError(
            f"the campaign plan {path} already exists: a plan is written once, and a changed "
            "feature is a new package rather than an overwrite"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_plan_text(package), encoding="utf-8")
    return path


def _plan_text(package: Package) -> str:
    """The campaign file as TOML: the steps are the tasks, and each step names the package."""
    plan = package.campaign
    assert plan is not None
    lines = [
        f"schema_version = {SCHEMA_VERSION}",
        f"id = {_quote(package.id)}",
        f"goal = {_quote(plan.goal)}",
        f"mode = {_quote(plan.mode)}",
        f"attempts = {plan.attempts}",
    ]
    if plan.budget:
        pairs = ", ".join(f"{key} = {value}" for key, value in sorted(plan.budget.items()))
        lines.append(f"budget = {{ {pairs} }}")
    for task in package.tasks:
        lines += [
            "",
            "[[step]]",
            f"id = {_quote(task.id)}",
            f"goal = {_quote(task.goal)}",
            f"kind = {_quote(task.kind)}",
            "paths = [" + ", ".join(_quote(item) for item in task.paths) + "]",
            f"expected = {_quote(task.expected)}",
            f"spec = {_quote(package.id)}",
            f"milestone = {_quote(package.milestone)}",
            "repro = [" + ", ".join(_quote(item) for item in task.repro) + "]",
            "[step.contract]",
            f"symptom = {_quote(task.contract.symptom)}",
            f"visible = {_quote(task.contract.visible)}",
            f"boundaries = {_quote(task.contract.boundaries)}",
        ]
        if task.contract.missing is not None:
            lines.append(f"missing = {_quote(task.contract.missing)}")
    for item in plan.acceptance:
        lines += [
            "",
            "[[acceptance]]",
            f"id = {_quote(item.id)}",
            f"statement = {_quote(item.statement)}",
            f"step = {_quote(item.step)}",
        ]
    return "\n".join(lines) + "\n"


def render_approval(approval: Approval, package_id: str) -> list[str]:
    """An acceptance as one line for a person: the digest, the owner's words, the plan."""
    note = f"  {approval.note}" if approval.note else ""
    lines = [f"accepted   {package_id}  {approval.digest}{note}"]
    if approval.plan is not None:
        lines.append(f"plan       {approval.plan}")
    return lines


def render_standing(current: Standing) -> list[str]:
    """The package's standing as a person reads it: the facts, then the one-line answer."""
    package = current.package
    lines = [
        f"id         {package.id}",
        f"milestone  {package.milestone}",
        f"source     {package.source}",
        f"tasks      {', '.join(task.id for task in package.tasks)}",
        f"digest     {current.digest}",
        f"standing   {current.line()}",
    ]
    if current.approval is not None:
        lines.append(f"approved   {current.approval.at} by {current.approval.by}")
        if current.approval.note:
            lines.append(f"note       {current.approval.note}")
        lines.append(f"record     {current.approval.path}")
    if current.refusal is not None:
        lines.append(f"refusal    {current.refusal}")
    return lines


def payload_package(package: Package, written: tuple[Path, ...] = ()) -> dict[str, object]:
    """The import for a machine: the manifest's facts, the file digests and the task files."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": package.id,
        "milestone": package.milestone,
        "source": package.source,
        "why": package.why,
        "spec": package.spec,
        "tasks": [task.id for task in package.tasks],
        "files": [{"path": item.path, "digest": item.digest} for item in package.files],
        "digest": package.digest,
        "imported": str(package.path),
        "mandata": [str(path) for path in written],
    }


payload_import = payload_package
"""The same answer under the name the import handler uses."""


def payload_approval(approval: Approval, package_id: str) -> dict[str, object]:
    """The acceptance for a machine."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": package_id,
        "at": approval.at,
        "by": approval.by,
        "note": approval.note,
        "digest": approval.digest,
        "files": [{"path": item.path, "digest": item.digest} for item in approval.files],
        "tasks": list(approval.tasks),
        "source": approval.source,
        "milestone": approval.milestone,
        "plan": None if approval.plan is None else str(approval.plan),
        "record": str(approval.path),
    }


def payload_standing(current: Standing) -> dict[str, object]:
    """The standing for a machine, with the refusal when there is one."""
    return {**current.json(), "refusal": current.refusal}


def _read_task(raw: object, where: str) -> Task:
    if not isinstance(raw, dict):
        raise boxes.HarnessError(f"{where} must be a table")
    unknown = sorted(set(raw) - TASK_KEYS)
    if unknown:
        raise boxes.HarnessError(
            f"{where}: unknown key(s) {', '.join(unknown)}; a task reads "
            + ", ".join(sorted(TASK_KEYS))
        )
    identifier = _name(raw.get("id"), f"{where}: id")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise boxes.HarnessError(f"{where}: kind is required")
    kind = kind.strip()
    if kind not in KINDS:
        raise boxes.HarnessError(
            f"{where}: kind {kind!r} is not one of {', '.join(sorted(KINDS))}: the routing "
            "table is Augustus's, and a kind it does not route starts nothing"
        )
    goal = _required(raw, "goal", Path(where))
    expected = _required(raw, "expected", Path(where))
    paths = raw.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(item, str) and item.strip() for item in paths)
    ):
        raise boxes.HarnessError(f"{where}: paths must be a non-empty list of strings")
    contract = _read_contract(raw.get("contract"), f"{where}: contract")
    repro = raw.get("repro")
    if not isinstance(repro, list) or not repro:
        raise boxes.HarnessError(
            f"{where}: the task {identifier} declares no repro: a spec that cannot be checked "
            "is not a spec"
        )
    if not all(isinstance(item, str) and item.strip() for item in repro):
        raise boxes.HarnessError(
            f"{where}: the task {identifier} declares no repro: a spec that cannot be checked "
            "is not a spec"
        )
    self_check = raw.get("self_check", [])
    if not isinstance(self_check, list) or not all(
        isinstance(item, str) and item.strip() for item in self_check
    ):
        raise boxes.HarnessError(f"{where}: self_check must be a list of non-empty strings")
    return Task(
        id=identifier,
        kind=kind,
        goal=goal,
        paths=tuple(item.strip() for item in paths),
        expected=expected,
        contract=contract,
        repro=tuple(item.strip() for item in repro),
        self_check=tuple(item.strip() for item in self_check),
    )


def _read_contract(raw: object, where: str) -> Contract:
    """The owner's words from the manifest, refused when they cannot carry a task."""
    if raw is None:
        raise boxes.HarnessError(
            f"{where}: the task declares no contract: `pantheon intake` refuses a task without "
            "the owner's words (symptom, visible, boundaries), so a package that names none is "
            "not imported"
        )
    if not isinstance(raw, dict):
        raise boxes.HarnessError(f"{where}: contract must be a table")
    unknown = sorted(set(raw) - CONTRACT_KEYS)
    if unknown:
        raise boxes.HarnessError(
            f"{where}: unknown key(s) {', '.join(unknown)}; a contract reads "
            + ", ".join(sorted(CONTRACT_KEYS))
        )
    points: dict[str, str] = {}
    for key in ("symptom", "visible", "boundaries"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise boxes.HarnessError(f"{where}: {key} is required")
        points[key] = value.strip()
    missing = raw.get("missing")
    if missing is not None and not isinstance(missing, str):
        raise boxes.HarnessError(f"{where}: missing must be a string")
    return Contract(
        symptom=points["symptom"],
        visible=points["visible"],
        boundaries=points["boundaries"],
        missing=missing.strip() if isinstance(missing, str) and missing.strip() else None,
    )


def _required(data: dict[str, object], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise boxes.HarnessError(f"{path}: {key} must be a non-empty string")
    return value.strip()


def _name(value: object, where: str) -> str:
    if isinstance(value, str) and value.strip():
        name = value.strip()
        if "/" not in name and "\\" not in name and name not in {".", ".."}:
            return name
    raise boxes.HarnessError(f"{where} is a name, not a path")


def _tree(directory: Path) -> tuple[FileDigest, ...]:
    """Every regular file under the package, sorted, skipping the caches a copy must not carry."""
    files: list[FileDigest] = []
    try:
        paths = sorted(directory.rglob("*"))
    except OSError as error:
        raise boxes.HarnessError(f"cannot read the spec package {directory}: {error}") from error
    for path in paths:
        relative = path.relative_to(directory)
        if any(part in {"__pycache__", ".git"} for part in relative.parts):
            continue
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise boxes.HarnessError(f"cannot read the package file {path}: {error}") from error
        files.append(FileDigest(path=relative.as_posix(), digest=_sha(raw)))
    return tuple(files)


def _digest(files: tuple[FileDigest, ...]) -> str:
    """One digest over the whole tree: the same bytes give the same answer, in any order."""
    body = "\n".join(f"{item.path} {item.digest}" for item in sorted(files, key=lambda i: i.path))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _difference(before: tuple[FileDigest, ...], now: tuple[FileDigest, ...]) -> str:
    """Which files differ, are missing, or were added, so "changed" names its evidence."""
    old = {item.path: item.digest for item in before}
    new = {item.path: item.digest for item in now}
    changed = sorted(path for path in old.keys() & new.keys() if old[path] != new[path])
    missing = sorted(old.keys() - new.keys())
    added = sorted(new.keys() - old.keys())
    parts: list[str] = []
    if changed:
        parts.append("changed: " + ", ".join(changed))
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if added:
        parts.append("added: " + ", ".join(added))
    return "; ".join(parts) if parts else "the recorded files no longer match the copy"


def _task_text(task: Task, package_id: str) -> str:
    """A ``mandata`` task carrying the package it came from: a plain file Augustus can read."""
    lines = [
        f"schema_version = {SCHEMA_VERSION}",
        f"id = {_quote(task.id)}",
        f"kind = {_quote(task.kind)}",
        f"goal = {_quote(task.goal)}",
        "paths = [" + ", ".join(_quote(item) for item in task.paths) + "]",
        f"expected = {_quote(task.expected)}",
        f"spec = {_quote(package_id)}",
        "repro = [" + ", ".join(_quote(item) for item in task.repro) + "]",
    ]
    if task.self_check:
        lines.append(
            "self_check = [" + ", ".join(_quote(item) for item in task.self_check) + "]"
        )
    lines += [
        "",
        "[contract]",
        f"symptom = {_quote(task.contract.symptom)}",
        f"visible = {_quote(task.contract.visible)}",
        f"boundaries = {_quote(task.contract.boundaries)}",
    ]
    if task.contract.missing is not None:
        lines.append(f"missing = {_quote(task.contract.missing)}")
    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    """A TOML basic string; the same escaping ``augustus._quote`` uses, kept local on purpose."""
    escaped = (
        value.replace(chr(92), chr(92) * 2)
        .replace('"', chr(92) + '"')
        .replace(chr(10), chr(92) + "n")
        .replace(chr(13), chr(92) + "r")
        .replace(chr(9), chr(92) + "t")
    )
    return '"' + escaped + '"'


def _record_path(box: boxes.Box, package_id: str) -> Path:
    return box.root / PACKAGES / f"{package_id}.json"


def _append_event(
    box: boxes.Box, by: str, note: str, record: Path, when: datetime, digest: str
) -> None:
    """One ledger event per acceptance: a mark the timeline cannot find is half a record."""
    ledger = box.root / "fasti"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    try:
        link = str(record.relative_to(box.root))
    except ValueError:
        link = str(record)
    event = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": "spec.accepted",
        "at": _stamp(when),
        "task": None,
        "actor": by,
        "reason": note,
        "links": [link],
        "data": {"digest": digest},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _read_record(box: boxes.Box, package_id: str) -> Approval | None:
    """The acceptance record, or ``None`` when the package was never accepted."""
    path = _record_path(box, package_id)
    if not path.is_file():
        return None
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"the spec acceptance {path} does not parse: {error}") from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path} is not a spec acceptance record")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version must be {SCHEMA_VERSION}, not "
            f"{data.get('schema_version')!r}"
        )
    digest = data.get("digest")
    if not isinstance(digest, str) or not digest:
        raise boxes.HarnessError(f"{path} names no digest: an acceptance of nothing is not one")
    raw_files = data.get("files", [])
    raw_tasks = data.get("tasks", [])
    if not isinstance(raw_files, list):
        raise boxes.HarnessError(f"{path}: files must be a list")
    if not isinstance(raw_tasks, list):
        raise boxes.HarnessError(f"{path}: tasks must be a list")
    return Approval(
        path=path,
        at=str(data.get("at", "")),
        by=str(data.get("by", "")),
        note=str(data.get("note", "")),
        digest=digest,
        files=tuple(
            FileDigest(path=str(item.get("path", "")), digest=str(item.get("digest", "")))
            for item in raw_files
            if isinstance(item, dict)
        ),
        tasks=tuple(str(item) for item in raw_tasks if isinstance(item, str)),
        source=str(data.get("source", "")),
        milestone=str(data.get("milestone", "")),
    )


def _never(package_id: str) -> str:
    return (
        f"the spec package {package_id} was never accepted: the owner's mark is a record, and "
        f'`pantheon spec {package_id} --accept --note "..."` writes it; without it the task '
        "does not start"
    )


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")
