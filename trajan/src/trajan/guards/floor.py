"""The test floor: a deterministic check that a change did not make the suite smaller.

The failure it exists for: an agent breaks the code, sees red tests, and deletes them (or marks
them skipped) until the suite is green. The project's own test command then passes, and so does
every verdict that trusts it. Nothing in the exit code says "the net got smaller".

This guard runs as an ordinary check from a policy (argv in, exit code out), so the verdict
code does not change and a failure becomes a confirmed finding like any other:

    [[checks]]
    id = "test-floor"
    argv = ["python", "-m", "trajan.guards.floor", "--base", "origin/main"]

It compares the test functions found by the *syntax tree* in a base revision with the working
tree. It runs no tests and calls no model. It reports, and exits 1 on, four things:

* a test that existed in the base and is gone (a test moved to another file under the same
  name is not gone);
* more skip/xfail markers than the base had;
* a test file that does not parse, on either side: its tests cannot be counted, so the
  comparison would be a quiet pass over a hole;
* a base it cannot read (an unknown revision is a failed check, not a quiet pass).

A project may be a subdirectory of a bigger repository (this one is): git reports paths
from the repository root, and the guard reports them relative to the project, so a check
run from ``trajan/`` sees the tests of ``trajan/`` and not the tests of its neighbours.

What it does not see, and the report line says so: a test whose body was emptied, a
parametrisation that lost cases, an assertion that was weakened. Those need a reader.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SKIP_ATTRIBUTES = frozenset({"skip", "skipif", "skipIf", "skipUnless", "xfail", "expectedFailure"})
TEST_DIRS = frozenset({"tests", "test"})
IGNORED_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__"})

LIMITS = (
    "not seen by this check: an emptied test body, a parametrisation that lost cases, "
    "a weakened assertion"
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What one revision of the project says about its own tests."""

    tests: frozenset[tuple[str, str]] = frozenset()  # (file, qualified test name)
    skips: int = 0
    unreadable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FloorReport:
    removed: tuple[tuple[str, str], ...] = ()
    moved: tuple[tuple[str, str], ...] = ()
    skips_added: int = 0
    unreadable: tuple[str, ...] = ()
    base_tests: int = 0
    current_tests: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.removed and self.skips_added <= 0 and not self.unreadable


def is_test_file(path: str) -> bool:
    """Whether ``path`` (relative, posix) is a Python test module."""
    pure = PurePosixPath(path)
    if pure.suffix != ".py" or IGNORED_PARTS & set(pure.parts):
        return False
    name = pure.name
    return (
        name.startswith("test_") or name.endswith("_test.py") or bool(TEST_DIRS & set(pure.parts))
    )


def scan_source(source: str) -> tuple[frozenset[str], int]:
    """Test function names (qualified by class) and skip/xfail markers in one module."""
    tree = ast.parse(source)
    names: set[str] = set()

    def visit(body: Iterable[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                names.add(f"{prefix}{node.name}")

    visit(tree.body, "")
    skips = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in SKIP_ATTRIBUTES
    )
    return frozenset(names), skips


def snapshot(files: dict[str, str]) -> Snapshot:
    """Build a snapshot from ``{relative path: source}``; unparsable files are listed."""
    tests: set[tuple[str, str]] = set()
    skips = 0
    unreadable: list[str] = []
    for path in sorted(files):
        if not is_test_file(path):
            continue
        try:
            names, found = scan_source(files[path])
        except SyntaxError:
            unreadable.append(path)
            continue
        tests.update((path, name) for name in names)
        skips += found
    return Snapshot(frozenset(tests), skips, tuple(unreadable))


def compare(base: Snapshot, current: Snapshot) -> FloorReport:
    """What the change took away from the suite."""
    base_paths = _paths_by_name(base.tests)
    # A test is moved, not removed, when its name reappears in a file that did not have it
    # before. Matching by name alone would hide a removal behind an unrelated twin: two files
    # can each define ``test_a``, and deleting one of them must still be a failure.
    destinations = {
        name: sorted(paths - base_paths.get(name, set()))
        for name, paths in _paths_by_name(current.tests).items()
    }
    removed: list[tuple[str, str]] = []
    moved: list[tuple[str, str]] = []
    for item in sorted(base.tests - current.tests):
        free = destinations.get(item[1])
        if free:
            moved.append(item)
            free.pop()
        else:
            removed.append(item)
    return FloorReport(
        removed=tuple(removed),
        moved=tuple(moved),
        skips_added=current.skips - base.skips,
        unreadable=tuple(sorted(set(base.unreadable) | set(current.unreadable))),
        base_tests=len(base.tests),
        current_tests=len(current.tests),
    )


def _paths_by_name(tests: frozenset[tuple[str, str]]) -> dict[str, set[str]]:
    paths: dict[str, set[str]] = {}
    for path, name in tests:
        paths.setdefault(name, set()).add(path)
    return paths


def render(report: FloorReport, base: str) -> list[str]:
    lines = [
        f"test floor against {base}: {report.base_tests} tests then, {report.current_tests} now",
    ]
    for path, name in report.removed:
        lines.append(f"  removed  {path}::{name}")
    if report.moved:
        lines.append(f"  moved    {len(report.moved)} test(s) kept under the same name elsewhere")
    if report.skips_added > 0:
        lines.append(f"  skipped  {report.skips_added} more skip/xfail marker(s) than the base")
    for path in report.unreadable:
        lines.append(f"  unreadable  {path} does not parse; its tests cannot be counted")
    lines.append("result: " + ("ok" if report.passed else "the suite got smaller"))
    lines.append(LIMITS)
    return lines


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git {' '.join(args)} failed")
    return done.stdout


def read_base(root: Path, base: str) -> dict[str, str]:
    top, prefix = _scope(root)
    names = _git(top, "ls-tree", "-r", "--name-only", "--full-tree", base).splitlines()
    files: dict[str, str] = {}
    for name in names:
        local = _local(name, prefix)
        if local is not None and is_test_file(local):
            files[local] = _git(top, "show", f"{base}:{name}")
    return files


def read_current(root: Path) -> dict[str, str]:
    top, prefix = _scope(root)
    listed = _git(
        top, "ls-files", "--cached", "--others", "--exclude-standard", "--full-name"
    ).splitlines()
    files: dict[str, str] = {}
    for name in listed:
        local = _local(name, prefix)
        if local is None or not is_test_file(local):
            continue
        target = root / local
        if target.is_file():
            files[local] = target.read_text(encoding="utf-8", errors="replace")
    return files


def _scope(root: Path) -> tuple[Path, str]:
    """The git top level above ``root`` and ``root``'s prefix inside it ("" at the top)."""
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    prefix = root.relative_to(top).as_posix()
    return top, "" if prefix == "." else f"{prefix}/"


def _local(name: str, prefix: str) -> str | None:
    """``name``, if it belongs to the scoped project, as a path relative to its root."""
    if not prefix:
        return name
    return name[len(prefix) :] if name.startswith(prefix) else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trajan.guards.floor",
        description="fail when a change removed tests or added skip markers",
    )
    parser.add_argument("--base", default="HEAD", help="git revision the change started from")
    parser.add_argument("--repo", default=".", help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    try:
        base = snapshot(read_base(root, args.base))
        current = snapshot(read_current(root))
    except RuntimeError as error:
        print(f"test floor could not read {args.base}: {error}")
        return 2
    report = compare(base, current)
    print("\n".join(render(report, args.base)))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
