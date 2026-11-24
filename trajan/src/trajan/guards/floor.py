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

A deletion a human meant is written down in ``.trajan/test-floor-allow.toml``, reviewed
with the tests it affects (a test and a reason per entry, nothing else). The entry buys that
one deletion: a listed test that is still there fails the check as a stale promise, because
a list that outlives its reasons is a rubber stamp. Skip markers cannot be allow-listed —
the count is global, and an unexplained skip is the thing this guard exists to catch.

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
import tomllib
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

ALLOW_FILE = ".trajan/test-floor-allow.toml"
"""Where a project lists the removals it meant; absence means none were meant."""


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
    allowed: tuple[tuple[str, str], ...] = ()  # (test, the reason a human wrote)
    stale_allow: tuple[str, ...] = ()
    base_tests: int = 0
    current_tests: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            not self.removed
            and self.skips_added <= 0
            and not self.unreadable
            and not self.stale_allow
        )


@dataclass(frozen=True, slots=True)
class AllowEntry:
    """A removal a human meant, written down next to the tests it takes away."""

    test: str  # "path/to/test_x.py::test_name"
    reason: str

    @property
    def path(self) -> str:
        return self.test.split("::", 1)[0]

    @property
    def name(self) -> str:
        return self.test.split("::", 1)[1]


ALLOW_KEYS = frozenset({"test", "reason"})


def read_allow(path: Path) -> tuple[AllowEntry, ...]:
    """Read the deliberate-removal list; a malformed entry is an error, not a quiet skip."""
    if not path.is_file():
        return ()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - {"removed"}
    if unknown:
        raise ValueError(f"{path}: unknown key {', '.join(sorted(unknown))}; use [[removed]]")
    entries = data.get("removed", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: removed must be a list of [[removed]] tables")
    allowed: list[AllowEntry] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) - ALLOW_KEYS:
            raise ValueError(f"{path}: [[removed]] #{index} takes exactly test and reason")
        test = entry.get("test")
        reason = entry.get("reason")
        if not isinstance(test, str) or "::" not in test:
            raise ValueError(
                f'{path}: [[removed]] #{index} needs test = "tests/test_x.py::test_name"'
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"{path}: [[removed]] #{index} needs a reason; an entry nobody explained "
                "is a rubber stamp"
            )
        allowed.append(AllowEntry(test.strip(), reason.strip()))
    return tuple(allowed)


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


def compare(
    base: Snapshot, current: Snapshot, allow: Sequence[AllowEntry] = ()
) -> FloorReport:
    """What the change took away from the suite, and which part of it a human meant.

    An allow entry buys one deletion, not a permanent pass: an entry that no longer matches a
    removal fails the check. Otherwise the list outlives its reasons and becomes a rubber
    stamp that silently permits the next deletion it happens to name.
    """
    base_paths = _paths_by_name(base.tests)
    # A test is moved, not removed, when its name reappears in a file that did not have it
    # before. Matching by name alone would hide a removal behind an unrelated twin: two files
    # can each define ``test_a``, and deleting one of them must still be a failure.
    destinations = {
        name: sorted(paths - base_paths.get(name, set()))
        for name, paths in _paths_by_name(current.tests).items()
    }
    gone: list[tuple[str, str]] = []
    moved: list[tuple[str, str]] = []
    for item in sorted(base.tests - current.tests):
        free = destinations.get(item[1])
        if free:
            moved.append(item)
            free.pop()
        else:
            gone.append(item)
    explained = {entry.test: entry.reason for entry in allow}
    allowed: list[tuple[str, str]] = []
    removed: list[tuple[str, str]] = []
    for path, name in gone:
        reason = explained.get(f"{path}::{name}")
        if reason is None:
            removed.append((path, name))
        else:
            allowed.append((f"{path}::{name}", reason))
    spent = {test for test, _ in allowed}
    return FloorReport(
        removed=tuple(removed),
        moved=tuple(moved),
        skips_added=current.skips - base.skips,
        unreadable=tuple(sorted(set(base.unreadable) | set(current.unreadable))),
        allowed=tuple(allowed),
        stale_allow=tuple(sorted({entry.test for entry in allow} - spent)),
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
    for test, reason in report.allowed:
        lines.append(f"  allowed  {test}  ({reason})")
    for test in report.stale_allow:
        lines.append(f"  stale    {test} is listed for removal, but no such test is gone")
    for path in report.unreadable:
        lines.append(f"  unreadable  {path} does not parse; its tests cannot be counted")
    lines.append("result: " + _verdict(report))
    lines.append(LIMITS)
    return lines


def _verdict(report: FloorReport) -> str:
    if report.passed:
        return "ok"
    if report.removed or report.skips_added > 0 or report.unreadable:
        return "the suite got smaller"
    return "the allow-list does not match the change"


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
    parser.add_argument(
        "--allow", default=None, help=f"deliberate-removal list (default: {ALLOW_FILE})"
    )
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    allow_path = Path(args.allow) if args.allow else root / ALLOW_FILE
    try:
        allow = read_allow(allow_path)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"test floor could not read its allow-list: {error}")
        return 2
    try:
        base = snapshot(read_base(root, args.base))
        current = snapshot(read_current(root))
    except RuntimeError as error:
        print(f"test floor could not read {args.base}: {error}")
        return 2
    report = compare(base, current, allow)
    print("\n".join(render(report, args.base)))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
