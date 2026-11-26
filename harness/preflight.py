"""The check the task brings is checked itself, before the model is paid for.

A repair task is real when its own reproduction fails on the base tree and passes on a known
good fix. Dispatch already refuses a reproduction that passes before any change; what it
cannot show is that the check is *satisfiable* — that a correct fix makes it pass. The
preflight runs the same command twice, on the project as it is and on a reference commit, and
writes nothing: a wrong criterion should cost no money and leave no record that later reads
as work.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import box as boxes
from . import contour


class CheckedTask(Protocol):
    """What the preflight reads from a task: its name and the check it brings."""

    @property
    def id(self) -> str: ...

    @property
    def repro(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class Run:
    """One execution of the task's reproduction: where it ran, the code, the last line."""

    where: str
    code: int
    tail: str

    @property
    def passed(self) -> bool:
        return self.code == 0


@dataclass(frozen=True, slots=True)
class Preflight:
    """What the check did on the base, what it did on the fix, and why it is refused."""

    task: str
    repro: tuple[str, ...]
    base: Run
    fix: Run | None
    refusal: str | None

    @property
    def ok(self) -> bool:
        return self.refusal is None


def check(
    project: Path,
    task: CheckedTask,
    *,
    fix: str | None,
    root: Path,
    python: Path,
) -> Preflight:
    """Run the task's reproduction on the base tree and, when named, on a fix commit.

    The base is the working tree the dispatch will run on, not a commit: a dirty tree is the
    tree the model would be asked about, and pretending otherwise would check a different
    thing. A reproduction that already passes on the base is refused before any money is
    spent; without a fix there is nothing else to compare it to.
    """
    if not task.repro:
        raise boxes.HarnessError(
            f"task {task.id} declares no reproduction: a preflight checks a check, and this "
            "task brings none"
        )
    code, tail = contour.run_repro(project, task.repro, root=root, python=python)
    base = Run("the base tree", code, tail)
    if base.passed:
        return Preflight(
            task.id,
            task.repro,
            base,
            None,
            "the reproduction passes on the base tree: the task does not describe a live defect",
        )
    if fix is None:
        return Preflight(task.id, task.repro, base, None, None)
    commit = _resolve(project, fix)
    run = _run_at(project, commit, task.repro, fix=fix, root=root, python=python)
    refusal = None
    if not run.passed:
        refusal = (
            f"the reproduction does not pass on {fix} ({commit[:12]}): the criterion and the "
            "reference disagree"
        )
    return Preflight(task.id, task.repro, base, run, refusal)


def render(result: Preflight) -> list[str]:
    """The preflight as a person reads it, naming both runs and the refusal."""
    lines = [
        f"task       {result.task}",
        f"repro      {_words(result.repro)}",
        _run_line("base", result.base),
    ]
    if result.fix is not None:
        lines.append(_run_line("fix", result.fix))
    if result.refusal is not None:
        lines.append(f"refused    {result.refusal}")
    else:
        tail = " and passes on the fix" if result.fix is not None else ""
        lines.append(f"verdict    live check: it fails on the base{tail}")
    return lines


def payload(result: Preflight) -> dict[str, object]:
    """The same answer for a machine."""
    return {
        "task": result.task,
        "repro": list(result.repro),
        "base": _run_json(result.base),
        "fix": None if result.fix is None else _run_json(result.fix),
        "ok": result.ok,
        "refusal": result.refusal,
    }


def _run_json(run: Run) -> dict[str, object]:
    return {"where": run.where, "code": run.code, "tail": run.tail, "passed": run.passed}


def _run_line(label: str, run: Run) -> str:
    state = "passed" if run.passed else "failed"
    return f"{label:<10} {run.where}: {state} ({run.tail})"


def _words(command: tuple[str, ...]) -> str:
    parts = []
    for part in command:
        flat = " ".join(part.split())
        if len(flat) > 72:
            flat = flat[:69] + "..."
        parts.append(shlex.quote(flat))
    return " ".join(parts)


def _resolve(project: Path, ref: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise boxes.HarnessError(
            f"the project has no commit {ref!r}: a preflight compares the base with a fix, "
            "and this one does not resolve"
        )
    return done.stdout.strip()


def _run_at(
    project: Path,
    commit: str,
    repro: tuple[str, ...],
    *,
    fix: str,
    root: Path,
    python: Path,
) -> Run:
    """Run the reproduction in a worktree of the fix, and leave the project as it was.

    The worktree is the commit, not the working tree: files the check needs but git does not
    track are not there. The project's own virtualenv is linked in when it exists, so the
    check resolves the interpreter it would use in the working tree.
    """
    parent = Path(tempfile.mkdtemp(prefix="pantheon-preflight-"))
    tree = parent / "tree"
    try:
        added = subprocess.run(
            ["git", "-C", str(project), "worktree", "add", "--detach", str(tree), commit],
            capture_output=True,
            text=True,
            check=False,
        )
        if added.returncode != 0:
            raise boxes.HarnessError(
                "cannot check the fix in a separate tree: "
                f"{added.stderr.strip()[-300:] or 'git refused the worktree'}"
            )
        venv = project / ".venv"
        if venv.is_dir() and not (tree / ".venv").exists():
            (tree / ".venv").symlink_to(venv)
        code, tail = contour.run_repro(tree, repro, root=root, python=python)
        return Run(f"the fix {fix} ({commit[:12]})", code, tail)
    finally:
        subprocess.run(
            ["git", "-C", str(project), "worktree", "remove", "--force", str(tree)],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(project), "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
        )
        shutil.rmtree(parent, ignore_errors=True)
