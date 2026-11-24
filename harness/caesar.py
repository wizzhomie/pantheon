"""Caesar: the gate a change goes through before it touches the project.

The contract is written down (``tasks/<id>.contract.toml``): what this attempt may change,
what it must not, and why the work exists at all. Caesar reads the proposed change, refuses
it when it reaches outside those paths, and only then applies it — so the refusal is the code
that would have written, not a warning printed before an unchecked write.

What this is not, yet: there is no container and no agent, so "the environment refuses" means
"the only writer in this box is this module, and it refuses before writing". Once Augustus
lives in the box (stage 1.5) the same contract is what its write tool enforces, and the
difference between the two is a mount and a user, not a promise.

Every attempt is recorded, refusals included: ``artifacts/<task>/<n>/`` holds the patch, the
paths it touched and the verdict about the attempt itself, and ``ledger/<date>.jsonl`` gets
one event. A refusal that is only printed is a refusal nobody can audit.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes

SCHEMA_VERSION = 1
CONTRACT_KEYS = frozenset(
    {"schema_version", "task", "kind", "goal", "expected", "allowed_paths", "forbidden", "budget"}
)
BUDGET_KEYS = frozenset({"max_seconds", "max_cents"})
FORBIDDEN = frozenset({"delete"})
"""The actions this gate can actually refuse; anything else is a config error, not a no-op."""

ALWAYS_REFUSED = (".git/",)
"""Paths no contract can authorise: rewriting history is not a change, it is a different box."""


@dataclass(frozen=True, slots=True)
class Contract:
    """One attempt's terms: the task, its declared scope, and what must never happen."""

    path: Path
    task: str
    kind: str | None
    goal: str | None
    expected: str | None
    allowed_paths: tuple[str, ...]
    forbidden: tuple[str, ...]
    budget: dict[str, int]


@dataclass(frozen=True, slots=True)
class Violation:
    """One reason the attempt is not allowed, with the path that caused it."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class Attempt:
    """What happened when the change met the contract."""

    task: str
    number: int
    status: str  # "applied" or "refused"
    reason: str
    paths: tuple[str, ...]
    violations: tuple[Violation, ...] = ()
    directory: Path = Path()
    patch_digest: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return self.status == "applied"


def read_contract(path: Path) -> Contract:
    """Load a contract; an unknown key is an error, because it would be a rule nobody obeys."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise boxes.HarnessError(f"no contract at {path}") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"contract {path} does not parse: {error}") from error
    unknown = set(data) - CONTRACT_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    task = data.get("task")
    if not isinstance(task, str) or not task.strip():
        raise boxes.HarnessError(f"{path}: task is required, and it names the attempt directory")
    if "/" in task or task in {".", ".."}:
        raise boxes.HarnessError(f"{path}: task {task!r} is a name, not a path")
    allowed = data.get("allowed_paths", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise boxes.HarnessError(f"{path}: allowed_paths must be a list of glob strings")
    forbidden = data.get("forbidden", [])
    if not isinstance(forbidden, list) or not all(isinstance(item, str) for item in forbidden):
        raise boxes.HarnessError(f"{path}: forbidden must be a list of strings")
    unsupported = set(forbidden) - FORBIDDEN
    if unsupported:
        raise boxes.HarnessError(
            f"{path}: forbidden action(s) {', '.join(sorted(unsupported))} are not enforced by "
            f"this gate; it can refuse: {', '.join(sorted(FORBIDDEN))}"
        )
    budget = data.get("budget", {})
    if not isinstance(budget, dict):
        raise boxes.HarnessError(f"{path}: budget must be a table")
    unknown_budget = set(budget) - BUDGET_KEYS
    if unknown_budget:
        raise boxes.HarnessError(
            f"{path}: unknown budget key(s) {', '.join(sorted(unknown_budget))}"
        )
    return Contract(
        path=path,
        task=task.strip(),
        kind=_optional_text(data.get("kind")),
        goal=_optional_text(data.get("goal")),
        expected=_optional_text(data.get("expected")),
        allowed_paths=tuple(allowed),
        forbidden=tuple(forbidden),
        budget={key: int(value) for key, value in budget.items()},
    )


def matches(pattern: str, path: str) -> bool:
    """Does ``path`` fall under ``pattern``? ``**`` crosses directories, ``*`` does not.

    A pattern is matched against the whole relative path, not against its tail: ``*.py``
    covers the top level and nothing else, ``**/*.py`` covers every directory. An allow-list
    that silently matched deeper than it was written would be a wider contract than the one
    somebody reviewed.
    """
    return _match(pattern.split("/"), path.split("/"))


def _match(patterns: list[str], parts: list[str]) -> bool:
    if not patterns:
        return not parts
    head, rest = patterns[0], patterns[1:]
    if head == "**":
        return any(_match(rest, parts[index:]) for index in range(len(parts) + 1))
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], head) and _match(rest, parts[1:])


DIFF_GIT = re.compile(r"^diff --git a/(?P<left>.+) b/(?P<right>.+)$")
OLD_FILE = re.compile(r"^--- (?:a/)?(?P<path>.+)$")
NEW_FILE = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+)$")
BINARY = re.compile(r"^Binary files a/(?P<left>.+) and b/(?P<right>.+) differ$")
QUOTED = re.compile(r'^"(?P<inner>.+)"$')

PLAIN_PAIR = re.compile(r"^diff --git a/(?P<left>\S+) b/(?P<right>\S+)$")
PLAIN_OLD = re.compile(r"^--- a/(?P<path>\S+)$")
PLAIN_NEW = re.compile(r"^\+\+\+ b/(?P<path>\S+)$")
PLAIN_BINARY = re.compile(r"^Binary files a/(?P<left>\S+) and b/(?P<right>\S+) differ$")
METADATA = re.compile(r"^(?:rename from|rename to|copy from|copy to) (?P<path>\S+)$")


def touched_paths(diff: str) -> tuple[str, ...]:
    """The project-relative paths a unified diff would create, change, rename or delete.

    Parsed from the ``---``/``+++`` pair of each file section, which is what ``git diff``
    writes; a binary section carries the same pair on its ``Binary files`` line. A path that
    git escaped (spaces, quotes) is unquoted when the quoting is simple, because refusing to
    read a patch is not the same as refusing the patch.
    """
    found: list[str] = []
    old = new = None
    for line in diff.splitlines():
        header = DIFF_GIT.match(line)
        if header:
            old = new = None
            continue
        binary = BINARY.match(line)
        if binary:
            found.extend([_clean(binary["left"]), _clean(binary["right"])])
            continue
        match = OLD_FILE.match(line)
        if match:
            old = _clean(match["path"])
            continue
        match = NEW_FILE.match(line)
        if match:
            new = _clean(match["path"])
            if new != "/dev/null":
                found.append(new)
            if old is not None and old not in {new, "/dev/null"}:
                found.append(old)
            old = new = None
    return tuple(dict.fromkeys(path for path in found if path and not path.startswith("/dev/")))


def _clean(raw: str) -> str:
    quoted = QUOTED.match(raw.strip())
    inner = quoted["inner"].replace('\\"', '"') if quoted else raw.strip()
    return inner.removeprefix("a/").removeprefix("b/")


def violations(contract: Contract, paths: tuple[str, ...], diff: str) -> tuple[Violation, ...]:
    """Every reason to refuse, not the first one: a refusal explains all of them at once."""
    found: list[Violation] = []
    if contract.kind is None:
        found.append(Violation("no-kind", "the contract declares no kind: a change without a type"))
    if not contract.allowed_paths:
        found.append(
            Violation("no-scope", "the contract declares no allowed paths: nothing is in scope")
        )
    for path in paths:
        for denied in ALWAYS_REFUSED:
            if path == denied.rstrip("/") or path.startswith(denied):
                found.append(Violation("git-internals", f"{path}: rewriting history is refused"))
        if "delete" in contract.forbidden and _deletes(diff, path):
            found.append(Violation("forbidden-delete", f"{path}: deleting files is forbidden"))
        if contract.allowed_paths and not any(
            matches(pattern, path) for pattern in contract.allowed_paths
        ):
            found.append(
                Violation("outside-scope", f"{path}: not under any allowed path of the contract")
            )
    return tuple(found)


def _deletes(diff: str, path: str) -> bool:
    """Whether the section for ``path`` deletes the file (``+++ /dev/null``)."""
    current: str | None = None
    for line in diff.splitlines():
        header = DIFF_GIT.match(line)
        if header:
            current = None
            continue
        match = OLD_FILE.match(line)
        if match:
            current = _clean(match["path"])
            continue
        if line.startswith("+++ ") and current == path and "/dev/null" in line:
            return True
    return False


def run_attempt(
    box_root: Path,
    project: Path,
    contract: Contract,
    patch: Path,
    when: datetime | None = None,
) -> Attempt:
    """Check the change against the contract, apply it only if allowed, record either way."""
    started = (when or datetime.now(UTC)).replace(microsecond=0)
    try:
        diff = patch.read_text(encoding="utf-8")
    except OSError as error:
        raise boxes.HarnessError(f"cannot read the patch {patch}: {error}") from error
    prefix = _prefix(project)
    change = _normalize(diff, prefix)
    paths = touched_paths(change)
    digest = hashlib.sha256(change.encode("utf-8")).hexdigest()
    problems = violations(contract, paths, change)
    number = _next_number(box_root, contract.task)
    directory = box_root / "artifacts" / contract.task / str(number)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "change.diff").write_text(change, encoding="utf-8")
    if change != diff:
        # Kept, because the gate judged a rewritten change: the record must show both.
        (directory / "change.source.diff").write_text(diff, encoding="utf-8")

    status = "refused" if problems else "applied"
    reason = _reason(problems)
    if not problems:
        applied = _apply(project, directory / "change.diff", paths, prefix)
        if applied is not None:
            status, reason = "refused", applied
    finished = datetime.now(UTC).replace(microsecond=0)
    attempt = Attempt(
        task=contract.task,
        number=number,
        status=status,
        reason=reason,
        paths=paths,
        violations=problems,
        directory=directory,
        patch_digest=digest,
    )
    _write_records(box_root, project, contract, attempt, started, finished)
    return attempt


def render_attempt(attempt: Attempt, contract: Contract) -> list[str]:
    """The attempt as a person reads it: what was allowed, what it touched, what happened."""
    lines = [
        f"task       {attempt.task}  ({contract.path})",
        f"attempt    {attempt.number}",
        f"scope      {', '.join(contract.allowed_paths) or 'nothing declared'}",
        f"paths      {', '.join(attempt.paths) or 'none'}",
    ]
    for violation in attempt.violations:
        lines.append(f"refused    {violation.code}: {violation.detail}")
    result = f"{attempt.status} — {attempt.reason}" if attempt.reason else attempt.status
    lines.append(f"result     {result}")
    lines.append(f"record     {attempt.directory}")
    return lines


def attempt_json(attempt: Attempt) -> dict[str, object]:
    """The attempt for a machine, matching what is written into the box."""
    return {
        "task": attempt.task,
        "attempt": attempt.number,
        "status": attempt.status,
        "reason": attempt.reason,
        "paths": list(attempt.paths),
        "violations": [{"code": item.code, "detail": item.detail} for item in attempt.violations],
        "patch_digest": attempt.patch_digest,
        "directory": str(attempt.directory),
    }


def _prefix(project: Path) -> str:
    """Where the project sits in its repository: ``""`` at the root, ``trajan/`` below it."""
    value = _git(project, "rev-parse", "--show-prefix")
    return (value or "").strip()


def _normalize(diff: str, prefix: str) -> str:
    """Rewrite a repository-root-relative diff into the project's own coordinates.

    ``git diff`` inside a subdirectory writes ``a/trajan/docs/x``; the contract, the evidence
    and the model's own diffs speak ``docs/x``. The gate must judge one coordinate system, so
    the patch is translated before it is read. A quoted path is left alone on purpose: failing
    to translate it produces a visible refusal, while guessing could write somewhere else.
    """
    if not prefix:
        return diff
    out: list[str] = []
    for line in diff.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        head = line[: -1] if newline else line
        pair = PLAIN_PAIR.match(head)
        if pair:
            left, right = _strip(pair[1], prefix), _strip(pair[2], prefix)
            out.append(f"diff --git a/{left} b/{right}{newline}")
            continue
        old = PLAIN_OLD.match(head)
        if old:
            out.append(f"--- a/{_strip(old[1], prefix)}{newline}")
            continue
        new = PLAIN_NEW.match(head)
        if new:
            out.append(f"+++ b/{_strip(new[1], prefix)}{newline}")
            continue
        binary = PLAIN_BINARY.match(head)
        if binary:
            left, right = _strip(binary[1], prefix), _strip(binary[2], prefix)
            out.append(f"Binary files a/{left} and b/{right} differ{newline}")
            continue
        metadata = METADATA.match(head)
        if metadata:
            verb = head.rsplit(" ", 1)[0]
            out.append(f"{verb} {_strip(metadata[1], prefix)}{newline}")
            continue
        out.append(line)
    return "".join(out)


def _strip(path: str, prefix: str) -> str:
    return path[len(prefix):] if path.startswith(prefix) else path


def _apply(project: Path, patch: Path, paths: tuple[str, ...], prefix: str) -> str | None:
    """Apply the patch where the project actually lives, or say why it did not.

    Two traps are handled here, both found on a real box rather than in a test:

    * the project may be a **subdirectory** of its repository. ``git apply`` run from that
      subdirectory resolves patch paths from the repository root, silently skips every path
      that is not under the current one, and still exits zero — so the gate said "applied"
      while the tree was untouched. The diff is normalised to the project and applied from the
      top level with ``--directory=<prefix>``;
    * a patch can succeed and change nothing (an empty diff, a hunk that was already
      applied). Success is therefore the tree changing, not the exit code.
    """
    top = Path(_git(project, "rev-parse", "--show-toplevel") or project)
    before = _tree_signature(top, tuple(prefix + path for path in paths))
    command = ["git", "-C", str(top), "apply", "--whitespace=nowarn"]
    if prefix:
        command.append(f"--directory={prefix}")
    command.append(str(patch))
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return "the project would not take the patch: " + (
            "; ".join(detail[-3:]) or "git apply failed"
        )
    if _tree_signature(top, tuple(prefix + path for path in paths)) == before:
        return (
            "the patch changed nothing: git reported success, but the paths it names are the "
            "same afterwards"
        )
    return None


def _tree_signature(base: Path, paths: tuple[str, ...]) -> str:
    """What git says about the paths before and after: status plus the diff against HEAD."""
    status = _git(base, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    diff = _git(base, "diff", "HEAD", "--", *paths)
    return (status or "") + "\n" + (diff or "")


def _git(base: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(base), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def _reason(problems: tuple[Violation, ...]) -> str:
    return "contract refused the change" if problems else ""


def _next_number(box_root: Path, task: str) -> int:
    base = box_root / "artifacts" / task
    if not base.is_dir():
        return 1
    numbers = [
        int(child.name) for child in base.iterdir() if child.is_dir() and child.name.isdigit()
    ]
    return max(numbers, default=0) + 1


def _write_records(
    box_root: Path,
    project: Path,
    contract: Contract,
    attempt: Attempt,
    started: datetime,
    finished: datetime,
) -> None:
    base = attempt.directory
    change = {
        "digest": f"sha256:{attempt.patch_digest}",
        "base": _head(project),
        "paths": list(attempt.paths),
    }
    (base / "change.json").write_text(
        json.dumps(change, indent=2) + "\n", encoding="utf-8"
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "task": attempt.task,
        "attempt": attempt.number,
        "status": attempt.status,
        "reason": attempt.reason,
        "started_at": _stamp(started),
        "finished_at": _stamp(finished),
        "contract": str(contract.path),
        "kind": contract.kind,
        "goal": contract.goal,
        "expected": contract.expected,
        "allowed_paths": list(contract.allowed_paths),
        "forbidden": list(contract.forbidden),
        "budget": contract.budget,
        "paths": list(attempt.paths),
        "violations": [
            {"code": item.code, "detail": item.detail} for item in attempt.violations
        ],
        "patch": {"digest": f"sha256:{attempt.patch_digest}", "file": "change.diff"},
    }
    (base / "attempt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_event(box_root, contract, attempt, finished)


def _append_event(
    box_root: Path, contract: Contract, attempt: Attempt, when: datetime
) -> None:
    ledger = box_root / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    event = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": f"attempt.{attempt.status}",
        "at": _stamp(when),
        "task": contract.task,
        "attempt": attempt.number,
        "actor": "caesar",
        "reason": attempt.reason,
        "links": [str(attempt.directory.relative_to(box_root))],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _head(project: Path) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
