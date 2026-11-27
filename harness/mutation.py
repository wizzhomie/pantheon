"""A named break, applied on purpose: does this check notice the thing it protects?

A green check says the result looks right; it does not say the check could have turned red.
Sensitivity asks the same question from the other side — it puts the old version of a piece of
the change back. This asks it about code the change *did not touch*: the task names a break (a
file, the line as it is, the line as it becomes), the tree is copied, the break is applied to the
copy, and the check is run. If the check stays green, it is blind to that behaviour.

``ADR-048`` calls this an audit and not a gate: a surviving break never returns a task, never
changes a verdict, and never runs on every change. It is evidence a person reads, with the
address of the break and the reason it was chosen. A break that no longer applies, or one that
does not compile, is reported as exactly that — turning a check red for the wrong reason is a
lie, not a signal.

The project is never touched: the tree is a throwaway worktree of HEAD with the working tree's
own diff and untracked files laid over it, and every break gets its own copy of it.
"""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import box as boxes
from . import caesar, contour, worktree

SCHEMA_VERSION = 1
AUDIT_KEYS = frozenset({"schema_version", "task", "check", "why", "break"})
BREAK_KEYS = frozenset({"path", "old", "new", "why"})

NOTICED = "noticed"
BLIND = "blind"
NOT_APPLIED = "not-applied"
INVALID = "invalid"
TIMEOUT = "timeout"

LIMIT_BREAKS = 10
"""How many breaks are worth applying: an audit is a reading, not a mutation campaign."""
CHECK_SECONDS = 120
"""One run of the check; a check slower than this is a cost, not a signal."""
LIMIT_SECONDS = 300
"""The clock for the whole audit, so a reading cannot outlive the question it answers."""


@dataclass(frozen=True, slots=True)
class Break:
    """One named break: the file, the line as it is, the line as it becomes, and why."""

    path: str
    old: str
    new: str
    why: str = ""


@dataclass(frozen=True, slots=True)
class Audit:
    """What a person declared: the check that should notice, and the breaks it should notice."""

    path: Path
    check: tuple[str, ...]
    breaks: tuple[Break, ...]
    task: str | None = None
    why: str = ""


@dataclass(frozen=True, slots=True)
class Attempt:
    """One break and what the check did: noticed it, stayed blind, or could not be applied."""

    break_: Break
    status: str
    note: str


@dataclass(frozen=True, slots=True)
class Report:
    """The audit as a person reads it: what was applied, what was noticed, what was left out."""

    audit: Audit
    base: str
    attempts: tuple[Attempt, ...]
    seconds: float
    left_out: int = 0
    stopped: str = ""

    @property
    def noticed(self) -> tuple[Attempt, ...]:
        return tuple(item for item in self.attempts if item.status == NOTICED)

    @property
    def blind(self) -> tuple[Attempt, ...]:
        return tuple(item for item in self.attempts if item.status == BLIND)


def read_audit(path: Path) -> Audit:
    """Load the declaration; an unknown key is an error, because nobody would read it."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"{path}: the audit does not parse: {error}") from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path}: the audit must be a table")
    unknown = sorted(set(data) - AUDIT_KEYS)
    if unknown:
        raise boxes.HarnessError(
            f"{path}: unknown key(s) {', '.join(unknown)}; the audit reads "
            + ", ".join(sorted(AUDIT_KEYS))
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version must be {SCHEMA_VERSION}, not {data.get('schema_version')!r}"
        )
    check = data.get("check")
    if not isinstance(check, list) or not check or not all(
        isinstance(item, str) and item.strip() for item in check
    ):
        raise boxes.HarnessError(
            f"{path}: check must be a command as a list of non-empty strings — the check whose "
            "sight is being audited"
        )
    raw = data.get("break")
    if not isinstance(raw, list) or not raw:
        raise boxes.HarnessError(
            f"{path}: no [[break]] entries: an audit with no break asks nothing"
        )
    breaks: list[Break] = []
    for index, item in enumerate(raw, start=1):
        where = f"{path}: break #{index}"
        if not isinstance(item, dict):
            raise boxes.HarnessError(f"{where} must be a table")
        extra = sorted(set(item) - BREAK_KEYS)
        if extra:
            raise boxes.HarnessError(f"{where}: unknown key(s) {', '.join(extra)}")
        for key in ("path", "old", "new"):
            if not isinstance(item.get(key), str):
                raise boxes.HarnessError(f"{where}: {key} must be a string")
        if not item["path"].strip() or not item["old"]:
            raise boxes.HarnessError(f"{where}: path and old must not be empty")
        breaks.append(
            Break(
                path=item["path"].strip(),
                old=item["old"],
                new=item["new"],
                why=str(item.get("why") or ""),
            )
        )
    task = data.get("task")
    if task is not None and not isinstance(task, str):
        raise boxes.HarnessError(f"{path}: task must be a string when declared")
    return Audit(
        path=path,
        check=tuple(item.strip() for item in check),
        breaks=tuple(breaks),
        task=task.strip() if task else None,
        why=str(data.get("why") or ""),
    )


def run(
    project: Path,
    audit: Audit,
    *,
    root: Path,
    python: Path,
    seconds: int = CHECK_SECONDS,
    limit: int = LIMIT_BREAKS,
    total_seconds: float = LIMIT_SECONDS,
) -> Report:
    """Apply each named break to its own copy of the tree as it is, and ask the check.

    The baseline comes first: a check that is red on the unbroken tree cannot show whether a
    break was noticed, and saying "blind" or "noticed" on top of it would be noise. Everything
    is a copy; the returned report is the whole reading, including what did not fit.
    """
    if not audit.check:
        raise boxes.HarnessError(f"{audit.path}: the audit names no check")
    if not audit.breaks:
        raise boxes.HarnessError(f"{audit.path}: the audit names no break")
    parent = Path(tempfile.mkdtemp(prefix="pantheon-mutation-"))
    tree = parent / "tree"
    top = worktree.toplevel(project)
    began = time.monotonic()
    attempts: list[Attempt] = []
    stopped = ""
    try:
        work = worktree.current(project, tree)
        venv = project / ".venv"
        if venv.is_dir():
            (work / ".venv").symlink_to(venv)
        code, tail = contour.run_repro(work, audit.check, root=root, python=python, timeout=seconds)
        if code == 124:
            raise boxes.HarnessError(
                f"the check did not finish in {seconds}s on the tree as it is: an audit cannot "
                "run a check whose own cost it cannot afford"
            )
        if code != 0:
            raise boxes.HarnessError(
                f"the check is red on the tree as it is ({tail}): a red check cannot show "
                "whether a break was noticed, and the reading would be noise"
            )
        for index, item in enumerate(audit.breaks):
            if index >= limit:
                break
            if time.monotonic() - began > total_seconds:
                stopped = (
                    f"the audit stopped after {round(time.monotonic() - began, 1)} s: it allows "
                    f"{total_seconds} s for the whole reading"
                )
                break
            attempts.append(
                _one_break(work, tree, top, project, item, audit, root, python, seconds, parent)
            )
    finally:
        worktree.remove(project, tree)
        shutil.rmtree(parent, ignore_errors=True)
    left_out = max(0, len(audit.breaks) - len(attempts))
    return Report(
        audit=audit,
        base=caesar.head(project) or "",
        attempts=tuple(attempts),
        seconds=round(time.monotonic() - began, 2),
        left_out=left_out,
        stopped=stopped,
    )


def _one_break(
    work: Path,
    tree: Path,
    top: Path,
    project: Path,
    item: Break,
    audit: Audit,
    root: Path,
    python: Path,
    seconds: int,
    parent: Path,
) -> Attempt:
    """Copy the tree, break the line there, and read what the check did."""
    variant = Path(tempfile.mkdtemp(prefix="variant-", dir=parent))
    target = variant / "tree"
    shutil.copytree(tree, target, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    worktree.drop_caches(target)
    try:
        here = worktree.inside(target, top, project)
        path = here / item.path
        if not path.is_file():
            return Attempt(item, NOT_APPLIED, f"the path is not in the tree: {item.path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            return Attempt(item, NOT_APPLIED, f"the file cannot be read: {error}")
        count = text.count(item.old)
        if count == 0:
            return Attempt(
                item, NOT_APPLIED, "the old text is not in the tree: the break no longer applies"
            )
        broken = text.replace(item.old, item.new)
        problem = _compiles(item.path, broken)
        if problem is not None:
            return Attempt(item, INVALID, problem)
        path.write_text(broken, encoding="utf-8")
        code, tail = contour.run_repro(here, audit.check, root=root, python=python, timeout=seconds)
        if code == 124:
            return Attempt(item, TIMEOUT, f"the check did not finish in {seconds}s")
        said = tail.split(":", 1)[1].strip() if ":" in tail else tail
        words = "" if said in {"", f"exit {code}"} else f" ({tail})"
        where = f"{count} place(s)" if count > 1 else "the line"
        if code == 0:
            return Attempt(item, BLIND, f"the check still passed on {where}{words}")
        return Attempt(item, NOTICED, f"the check failed on {where}{words}")
    finally:
        shutil.rmtree(variant, ignore_errors=True)


def _compiles(path: str, text: str) -> str | None:
    """A break that does not compile turns the check red for the wrong reason: refuse it."""
    if not path.endswith(".py"):
        return None
    try:
        compile(text, path, "exec")
    except SyntaxError as error:
        return f"the break does not compile, so it would be a lie: {error}"
    return None


def _words(command: tuple[str, ...]) -> str:
    """One readable line for the check; the full argv stays in the JSON and the record."""
    parts = []
    for part in command:
        flat = " ".join(part.split())
        if len(flat) > 72:
            flat = flat[:69] + "..."
        parts.append(shlex.quote(flat))
    return " ".join(parts)


def render(report: Report) -> list[str]:
    """The audit as a person reads it: the tree, the check, every break and its answer."""
    audit = report.audit
    lines = [
        f"audit      {audit.path}",
        f"task       {audit.task or 'not declared'}",
        f"check      {_words(audit.check)}",
        f"tree       HEAD {report.base or 'unknown'}",
    ]
    if audit.why:
        lines.append(f"why        {audit.why}")
    lines.append("breaks")
    for item in report.attempts:
        reason = f" — {item.break_.why}" if item.break_.why else ""
        lines.append(f"- {item.break_.path}{reason}: {item.status} ({item.note})")
    if report.left_out:
        lines.append(f"left out   {report.left_out} break(s) did not fit the limit")
    if report.stopped:
        lines.append(f"stopped    {report.stopped}")
    lines.append(
        f"verdict    {len(report.noticed)} noticed, {len(report.blind)} blind, "
        f"{len([item for item in report.attempts if item.status in {NOT_APPLIED, INVALID}])} "
        f"not applied or invalid, {report.seconds} s — an audit, not a verdict on the code"
    )
    return lines


def payload(report: Report) -> dict[str, object]:
    """The same reading for a machine."""
    audit = report.audit
    return {
        "schema_version": SCHEMA_VERSION,
        "audit": str(audit.path),
        "task": audit.task,
        "why": audit.why,
        "check": list(audit.check),
        "base": report.base,
        "seconds": report.seconds,
        "left_out": report.left_out,
        "stopped": report.stopped,
        "break": [
            {
                "path": item.break_.path,
                "why": item.break_.why,
                "status": item.status,
                "note": item.note,
            }
            for item in report.attempts
        ],
        "noticed": len(report.noticed),
        "blind": len(report.blind),
    }


def save(report: Report, directory: Path, when: str) -> Path:
    """Write the reading into the box: one file per audit, never edited afterwards."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{when}.json"
    path.write_text(json.dumps(payload(report), indent=2, ensure_ascii=False) + "\n", "utf-8")
    return path
