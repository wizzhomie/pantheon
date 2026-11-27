"""A throwaway git worktree of the tree as it is: the commit plus what the working tree added.

Two contours read a change without touching the project: the sensitivity rehearsal builds the
base commit plus a patch, and the mutation audit builds the tree as it stands right now. Both
need the same three things — the repository the project lives in, the project inside a worktree
(the box may serve a project that sits inside a bigger repository), and a way to throw the
worktree away. They live here once, so the two steps cannot disagree about what "the tree" is.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import box as boxes


def toplevel(project: Path) -> Path:
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


def inside(tree: Path, top: Path, project: Path) -> Path:
    """The project's own directory inside a worktree of the repository."""
    here = project.resolve()
    return tree if here == top else tree / here.relative_to(top)


def current(project: Path, tree: Path) -> Path:
    """Build a worktree of HEAD overlaid with the working tree, and return the project in it.

    The overlay is what a dispatch would judge: the diff against HEAD (staged or not) applied to
    the worktree, plus the untracked files git does not ignore. Ignored files stay out on purpose
    — a virtualenv, a data directory or an OCR bundle is not the change, and copying it would
    make every variant expensive without making it more true.
    """
    top = toplevel(project)
    added = subprocess.run(
        ["git", "-C", str(top), "worktree", "add", "--detach", str(tree), "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        raise boxes.HarnessError(
            "cannot build the tree as it is: "
            f"{added.stderr.strip()[-300:] or 'git refused the worktree'}"
        )
    diff = subprocess.run(
        ["git", "-C", str(top), "diff", "HEAD", "--binary"],
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        raise boxes.HarnessError(
            f"cannot read the working tree's diff: {diff.stderr.strip()[-300:] or 'git failed'}"
        )
    if diff.stdout.strip():
        applied = subprocess.run(
            ["git", "-C", str(tree), "apply", "-"],
            input=diff.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        if applied.returncode != 0:
            raise boxes.HarnessError(
                "the working tree's diff does not apply to HEAD: "
                f"{applied.stderr.strip()[-300:] or 'git refused it'}"
            )
    untracked = subprocess.run(
        ["git", "-C", str(top), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode == 0:
        for line in untracked.stdout.splitlines():
            if not line.strip():
                continue
            source = top / line
            target = tree / line
            if not source.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return inside(tree, top, project)


def remove(project: Path, tree: Path) -> None:
    """Throw the worktree away; a leftover registration would poison the next one."""
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


CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def drop_caches(tree: Path) -> int:
    """Delete the bytecode and tool caches in a copy, and count what was dropped.

    A live audit found the trap: the baseline run compiles the module into ``__pycache__``, the
    copy keeps both the source and the cache, and a break that leaves the file the same size can
    keep the same mtime — so Python trusts the old bytecode and the check passes on broken code.
    The cache is not part of the tree being judged; dropping it makes the answer deterministic.
    """
    dropped = 0
    for name in CACHE_DIRS:
        for path in list(tree.rglob(name)):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                dropped += 1
    return dropped
