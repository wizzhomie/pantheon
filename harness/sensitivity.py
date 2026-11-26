"""Reversing one piece of the change, to see whether the check notices it at all.

A green check says the result looks right; it does not say the check could have turned red. The
rehearsal showed the difference: a test kept passing after the heading it was supposed to protect
was removed. This step asks the smaller, harder question — take one piece of the change, put the
old version back, run the same check: does it notice?

The answer is a list, not a verdict: pieces the check noticed, pieces it did not, and pieces that
were not tested with the reason. It never decides for the task; it makes the weakness visible.

What is tested is the piece, and every kind of change has its own idea of one:

* a new file is removed;
* a deleted file is written back;
* a text block (a paragraph, a table row) is replaced by the old block;
* a config value is put back to the old value, and the file must still parse — a revert that
  breaks the format turns the check red for the wrong reason, which is a lie, not a signal.

Code is not tested yet: a reverted line usually breaks the syntax, so the check fails because the
file no longer imports, not because it saw the behaviour. That needs a real mutation engine, and
until then the report says so instead of producing false alarms. Renames and whitespace-only
changes are skipped for the same honesty.

The run is deterministic and free: no model is asked, the check command comes from the task, and
the tree is a throwaway copy.
"""

from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import box as boxes
from . import contour

LIMIT_PIECES = 5
"""How many pieces are worth reversing: a big change should not cost a hundred check runs."""
LIMIT_SECONDS = 120
"""One check run; a check slower than this is not a signal, it is a cost."""

TEXT_EXT = frozenset({".md", ".rst", ".txt", ".adoc"})
CONFIG_EXT = frozenset({".toml", ".ini", ".cfg", ".json", ".env", ".conf"})
"""Config is reverted by value and must still parse; the parser is the standard library's."""
NO_PARSER = frozenset({".yaml", ".yml"})
"""Skipped, not silently: there is no YAML parser in the standard library, and a fake one lies."""

NOTICED = "noticed"
BLIND = "blind"
SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Piece:
    """One reversible piece of the change: where it is, what it was, what it became."""

    where: str
    path: str
    kind: str
    new: tuple[str, ...]
    old: tuple[str, ...] = ()
    whole: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class Attempt:
    """What happened to one piece: noticed, blind, or skipped for a named reason."""

    piece: Piece
    status: str
    why: str = ""

    @property
    def blind(self) -> bool:
        return self.status == BLIND


@dataclass(frozen=True, slots=True)
class Report:
    """The whole run: what was tested, what was not, and what the check did with each piece."""

    task: str
    patch: Path
    attempts: tuple[Attempt, ...]
    left_out: int = 0
    seconds: float = 0.0
    limit: int = LIMIT_PIECES
    """How many pieces the caller allowed: the report says the number it was given, not a guess."""
    stopped: str = ""
    """Why the run ended before its limit, when the caller put a clock on it."""

    @property
    def blind(self) -> tuple[Attempt, ...]:
        return tuple(item for item in self.attempts if item.status == BLIND)

    @property
    def noticed(self) -> tuple[Attempt, ...]:
        return tuple(item for item in self.attempts if item.status == NOTICED)

    @property
    def skipped(self) -> tuple[Attempt, ...]:
        return tuple(item for item in self.attempts if item.status == SKIPPED)


def check(
    project: Path,
    *,
    task: str,
    patch: Path,
    repro: tuple[str, ...],
    root: Path,
    python: Path,
    limit: int = LIMIT_PIECES,
    seconds: int = LIMIT_SECONDS,
    base: str = "HEAD",
    total_seconds: float | None = None,
) -> Report:
    """Reverse each piece of the change on its own copy and see whether the check notices.

    The tree the pieces are applied to is the base commit plus the patch, built in a worktree:
    the working tree may already hold other things, and a check should be judged against the
    change, not against whatever else is lying around.

    ``total_seconds`` is the caller's clock for the whole run: a dispatch cannot spend ten minutes
    on a rehearsal whose job is to inform a verdict it already computed. What did not fit is
    counted as left out, and the report names the clock instead of pretending it was the limit.
    """
    if not repro:
        raise boxes.HarnessError(
            f"task {task} declares no check: this step reverses a change and asks a check, and "
            "the task brings none"
        )
    if not patch.is_file():
        raise boxes.HarnessError(f"no patch at {patch}: there is no change to reverse")
    pieces = pieces_of(patch.read_text(encoding="utf-8"))
    tested = [item for item in pieces if item.kind != "skipped"][:limit]
    left_out = max(0, len([item for item in pieces if item.kind != "skipped"]) - limit)
    parent = Path(tempfile.mkdtemp(prefix="pantheon-sensitivity-"))
    tree = parent / "fix"
    attempts: list[Attempt] = []
    began = _now()
    try:
        work = _fix_tree(project, patch, tree, base)
        venv = project / ".venv"
        if venv.is_dir():
            (work / ".venv").symlink_to(venv)
        code, tail = contour.run_repro(work, repro, root=root, python=python, timeout=seconds)
        if code == 124:
            raise boxes.HarnessError(
                f"the check did not finish in {seconds}s on the change itself: a check that "
                "cannot be run twice cannot be reversed, and the run would be noise"
            )
        if code != 0:
            raise boxes.HarnessError(
                f"the check is already red on the change itself ({tail}): a check that fails "
                "before anything is reversed cannot notice a reversal, and the run would be noise"
            )
        for item in pieces:
            if item.kind == "skipped":
                attempts.append(Attempt(item, SKIPPED, item.note))
        stopped = ""
        for index, piece in enumerate(tested):
            if total_seconds is not None and _now() - began > total_seconds:
                left_out += len(tested) - index
                stopped = (
                    f"the run stopped after {round(_now() - began, 1)} s: the caller allowed "
                    f"{total_seconds} s, and a rehearsal may not outlast the verdict it informs"
                )
                break
            attempt = _one_piece(work, piece, repro, root, python, seconds, parent)
            attempts.append(attempt)
    finally:
        _remove_worktree(project, tree)
        shutil.rmtree(parent, ignore_errors=True)
    return Report(
        task=task,
        patch=patch,
        attempts=tuple(attempts),
        left_out=left_out,
        seconds=round(_now() - began, 2),
        limit=limit,
        stopped=stopped,
    )


def pieces_of(diff: str) -> tuple[Piece, ...]:
    """The change as reversible pieces, with the untestable ones named instead of dropped."""
    pieces: list[Piece] = []
    for change in _files(diff):
        pieces.extend(_pieces_of(change))
    return tuple(pieces)


def render(report: Report) -> list[str]:
    """The report as a person reads it: what was tested and what the check did not notice."""
    lines = [
        f"task       {report.task}",
        f"patch      {report.patch}",
        f"time       {report.seconds:.2f} s",
        f"pieces     {len(report.noticed) + len(report.blind)} tested, "
        f"{len(report.blind)} blind, {len(report.skipped)} skipped",
    ]
    for item in report.attempts:
        mark = {NOTICED: "noticed", BLIND: "blind", SKIPPED: "skipped"}[item.status]
        detail = f" — {item.why}" if item.why else ""
        lines.append(f"{mark:<10} {item.piece.where} — {item.piece.kind}{detail}")
    if report.stopped:
        lines.append(f"stopped    {report.stopped}")
    if report.left_out:
        lines.append(
            f"left out   {report.left_out} piece(s): the limit is {report.limit} check runs, and "
            "a bigger change is not worth a hundred"
        )
    if report.blind:
        lines.append(
            "verdict    the check does not notice some of the change: it can be green whatever "
            "those pieces say"
        )
    elif report.attempts and not report.skipped:
        lines.append("verdict    the check notices every piece that was reversed")
    return lines


def payload(report: Report) -> dict[str, object]:
    """The same report for a machine."""
    return {
        "task": report.task,
        "patch": str(report.patch),
        "seconds": report.seconds,
        "left_out": report.left_out,
        "limit": report.limit,
        "stopped": report.stopped,
        "pieces": [
            {
                "where": item.piece.where,
                "path": item.piece.path,
                "kind": item.piece.kind,
                "status": item.status,
                "why": item.why,
            }
            for item in report.attempts
        ],
        "blind": len(report.blind),
        "noticed": len(report.noticed),
        "skipped": len(report.skipped),
    }


@dataclass(frozen=True, slots=True)
class _File:
    path: str
    is_new: bool
    is_deleted: bool
    is_rename: bool
    hunks: tuple[tuple[tuple[str, str], ...], ...]
    """Each hunk as (kind, text) pairs, kind being " " for context, "+" and "-"."""


def _files(diff: str) -> tuple[_File, ...]:
    """Split a unified diff into files and hunks; anything unrecognised is not guessed at."""
    found: list[_File] = []
    path = ""
    is_new = is_deleted = is_rename = False
    hunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if path:
                found.append(_File(path, is_new, is_deleted, is_rename, _frozen(hunks)))
            path = ""
            is_new = is_deleted = is_rename = False
            hunks = []
            current = None
            continue
        if line.startswith("new file mode"):
            is_new = True
            continue
        if line.startswith("deleted file mode"):
            is_deleted = True
            continue
        if line.startswith("rename from") or line.startswith("rename to"):
            is_rename = True
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            if not path and line.startswith("+++ "):
                path = line[4:].strip().split("\t")[0].removeprefix("b/")
            continue
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if current is not None and line[:1] in {" ", "+", "-"}:
            current.append((line[0], line[1:]))
    if path:
        found.append(_File(path, is_new, is_deleted, is_rename, _frozen(hunks)))
    return tuple(found)


def _pieces_of(change: _File) -> list[Piece]:
    """The pieces of one file: the whole file when it appears or disappears, blocks otherwise."""
    suffix = Path(change.path).suffix.lower()
    if change.is_rename:
        return [
            Piece(
                where=change.path,
                path=change.path,
                kind="skipped",
                new=(),
                note="a rename is not reversed: the name may not be what the check sees",
            )
        ]
    if change.is_new:
        return [
            Piece(
                where=change.path,
                path=change.path,
                kind="a new file",
                new=(),
                whole=True,
            )
        ]
    if change.is_deleted:
        lines = tuple(text for kind, text in _flat(change.hunks) if kind != "+")
        return [
            Piece(
                where=change.path,
                path=change.path,
                kind="a deleted file",
                new=lines,
                whole=True,
            )
        ]
    if suffix in NO_PARSER:
        return [
            Piece(
                where=change.path,
                path=change.path,
                kind="skipped",
                new=(),
                note="no YAML parser in the standard library: a fake one would lie",
            )
        ]
    if suffix and suffix not in TEXT_EXT and suffix not in CONFIG_EXT:
        return [
            Piece(
                where=change.path,
                path=change.path,
                kind="skipped",
                new=(),
                note="code is not tested yet: a reverted line breaks the syntax, not the "
                "behaviour, and the check would fail for the wrong reason",
            )
        ]
    kind = "a config value" if suffix in CONFIG_EXT else "a text block"
    pieces: list[Piece] = []
    flat = _flat(change.hunks)
    for group in _groups(flat):
        old = tuple(text for mark, text in group if mark == "-")
        new = tuple(text for mark, text in group if mark == "+")
        if not new and not old:
            continue
        where = f"{change.path}: {_excerpt(group)}" if group else change.path
        pieces.append(Piece(where=where, path=change.path, kind=kind, new=new, old=old))
    return pieces


def _frozen(hunks: list[list[tuple[str, str]]]) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Hunks as an immutable table: the diff is data, and a reader cannot edit it by accident."""
    return tuple(tuple(hunk) for hunk in hunks)


def _excerpt(group: list[tuple[str, str]]) -> str:
    """The first changed words, short: a report line is an address, not the whole paragraph."""
    for _, text in group:
        flat = " ".join(text.split())
        if flat:
            return flat if len(flat) <= 48 else flat[:45].rstrip() + "..."
    return "?"


def _flat(hunks: tuple[tuple[tuple[str, str], ...], ...]) -> list[tuple[str, str]]:
    return [line for hunk in hunks for line in hunk]


def _groups(flat: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Consecutive changed lines between context: one paragraph, one value, one block."""
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for mark, text in flat:
        if mark == " ":
            if current:
                groups.append(current)
                current = []
            continue
        current.append((mark, text))
    if current:
        groups.append(current)
    return groups


def _fix_tree(project: Path, patch: Path, tree: Path, base: str) -> Path:
    """The base commit plus the patch: the tree the change is about, not the working tree.

    A box may serve a project that sits inside a bigger repository. The worktree is built from
    the repository root, but the change and the check speak the project's own paths: applying a
    patch at the repository root would look for ``docs/guide.md`` where the repository keeps
    ``app/docs/guide.md``. The returned path is the project inside the worktree.
    """
    top = _toplevel(project)
    added = subprocess.run(
        ["git", "-C", str(top), "worktree", "add", "--detach", str(tree), base],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        raise boxes.HarnessError(
            "cannot build the tree the change is about: "
            f"{added.stderr.strip()[-300:] or 'git refused the worktree'}"
        )
    here = project.resolve()
    work = tree if here == top else tree / here.relative_to(top)
    applied = subprocess.run(
        ["git", "-C", str(work), "apply", str(patch)],
        capture_output=True,
        text=True,
        check=False,
    )
    if applied.returncode != 0:
        raise boxes.HarnessError(
            f"the patch does not apply to {base}: "
            f"{applied.stderr.strip()[-300:] or 'git refused it'}"
        )
    return work


def _toplevel(project: Path) -> Path:
    """The repository the project lives in; the project itself when it is the whole checkout."""
    done = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 or not done.stdout.strip():
        raise boxes.HarnessError(
            f"{project} is not inside a git repository: there is no base to build the change on"
        )
    return Path(done.stdout.strip()).resolve()


def _one_piece(
    tree: Path,
    piece: Piece,
    repro: tuple[str, ...],
    root: Path,
    python: Path,
    seconds: int,
    parent: Path,
) -> Attempt:
    """Copy the tree, reverse the piece there, run the check, and read what it did."""
    variant = Path(tempfile.mkdtemp(prefix="variant-", dir=parent))
    target = variant / "tree"
    shutil.copytree(tree, target, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    try:
        problem = _reverse(target, piece)
        if problem is not None:
            return Attempt(piece, SKIPPED, problem)
        code, tail = contour.run_repro(target, repro, root=root, python=python, timeout=seconds)
        said = tail.split(":", 1)[1].strip() if ":" in tail else tail
        words = "" if said in {"", f"exit {code}"} else f" ({tail})"
        if code == 0:
            return Attempt(piece, BLIND, f"the check still passed{words}")
        return Attempt(piece, NOTICED, f"the check failed{words}")
    finally:
        shutil.rmtree(variant, ignore_errors=True)


def _reverse(tree: Path, piece: Piece) -> str | None:
    """Put the old version back; ``None`` when it worked, or the reason it was not a valid piece."""
    path = tree / piece.path
    if piece.whole and not piece.new and not piece.old:
        # A new file: the piece is its existence, and removing it is always valid.
        if not path.exists():
            return "the file the change adds is not in the tree"
        path.unlink()
        return None
    if piece.whole:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(piece.new) + "\n", encoding="utf-8")
        return _valid(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return f"the file cannot be read: {error}"
    lines = text.splitlines()
    if piece.new:
        found = _find(lines, list(piece.new))
        if found is None:
            return "the piece is not in the tree as the diff describes it"
        lines[found : found + len(piece.new)] = list(piece.old)
    else:
        return "a piece that only removes lines is not reversed yet"
    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
    return _valid(path)


def _valid(path: Path) -> str | None:
    """A config that no longer parses turns the check red for the wrong reason: do not test it."""
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
        if suffix == ".toml":
            tomllib.loads(text)
        elif suffix == ".json":
            json.loads(text)
        elif suffix in {".ini", ".cfg", ".conf"}:
            configparser.ConfigParser().read_string(text)
        elif suffix == ".env":
            for line in text.splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" not in line:
                    return f"the old value would break the file: {line.strip()[:40]!r}"
    except (OSError, ValueError, tomllib.TOMLDecodeError, configparser.Error) as error:
        return f"the revert would break the file's format, not its meaning: {error}"
    return None


def _find(lines: list[str], block: list[str]) -> int | None:
    for start in range(0, len(lines) - len(block) + 1):
        if lines[start : start + len(block)] == block:
            return start
    return None


def _remove_worktree(project: Path, tree: Path) -> None:
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


def _now() -> float:
    return time.monotonic()
