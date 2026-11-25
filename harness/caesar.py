"""Caesar: the gate a change goes through before it touches the project.

The contract is written down (``mandata/<id>.contract.toml``): what this attempt may change,
what it must not, and why the work exists at all. Caesar reads the proposed change, refuses
it when it reaches outside those paths, and only then applies it — so the refusal is the code
that would have written, not a warning printed before an unchecked write.

"The environment refuses" means the only writer in this box is this module, and it refuses
before writing; the agent in the container has no write tool of its own, so the contract is
enforced by a mount and a user, not by a promise.

Every attempt is recorded, refusals included: ``tabularium/<task>/<n>/`` holds the patch, the
paths it touched and the verdict about the attempt itself, and ``fasti/<date>.jsonl`` gets
one event. A refusal that is only printed is a refusal nobody can audit.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes

SCHEMA_VERSION = 1
CONTRACT_KEYS = frozenset(
    {"schema_version", "task", "kind", "goal", "expected", "allowed_paths", "forbidden", "budget"}
)
BUDGET_KEYS = frozenset({"max_seconds", "max_cents", "max_read_chars"})
FORBIDDEN = frozenset({"delete"})
"""The actions this gate can actually refuse; anything else is a config error, not a no-op."""

ALWAYS_REFUSED = (".git/",)
"""Paths no contract can authorise: rewriting history is not a change, it is a different box."""

KINDS_FILE = Path(".pantheon") / "kinds.toml"
"""The project's table of what each kind of task may write, versioned with the project."""

FRAME_PATHS = (".pantheon/**", ".trajan/context.toml")
"""The project's frame: the architecture model and the requirements it is checked against."""

COMMIT_AUTHOR = ("Pantheon", "pantheon@harness.invalid")
"""Who a committed change is attributed to: the harness, because the writer is not a person."""


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
class Kind:
    """What one kind of task may write: its own paths, and the paths it never touches."""

    name: str
    allow: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


DEFAULT_KINDS: dict[str, Kind] = {
    "architecture": Kind("architecture", allow=(*FRAME_PATHS, "docs/**")),
}
"""The frame belongs to architecture; every other kind may write what its contract allows,
except the frame itself. A project can replace any entry with its own ``kinds.toml``."""

ANY_KIND = Kind("*", forbid=FRAME_PATHS)


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
    seconds: float = 0.0
    """Wall time of the gate's own work: reading, judging and applying the change."""

    @property
    def applied(self) -> bool:
        return self.status == "applied"


@dataclass(frozen=True, slots=True)
class Commit:
    """Whether a reviewed change became the project's next commit, and if not, why not."""

    status: str  # "made", "skipped" or "failed"
    sha: str = ""
    reason: str = ""
    subject: str = ""
    notes: tuple[str, ...] = ()
    seconds: float = 0.0
    """Wall time of the commit work: building it from the recorded change, not staging a tree."""

    @property
    def made(self) -> bool:
        return self.status == "made"


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


def read_kinds(project: Path) -> dict[str, Kind]:
    """The project's kind policy, with the built-in default wherever nothing is declared.

    A malformed policy stops the gate instead of being ignored: a rule nobody can read is not a
    rule, and quietly falling back to "anything goes" would hide exactly the boundary it draws.
    """
    declared = dict(DEFAULT_KINDS)
    path = project / KINDS_FILE
    if not path.is_file():
        return declared
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"the kind policy {path} does not parse: {error}") from error
    unknown = set(data) - {"schema_version", "kind"}
    if unknown:
        raise boxes.HarnessError(
            f"the kind policy {path} has keys nobody reads: {', '.join(sorted(unknown))}"
        )
    version = data.get("schema_version")
    if version != 1:
        raise boxes.HarnessError(
            f"the kind policy {path} declares schema_version {version!r}, but this gate reads 1"
        )
    entries = data.get("kind", [])
    if not isinstance(entries, list):
        raise boxes.HarnessError(f"the kind policy {path}: `kind` must be a list of tables")
    for entry in entries:
        if not isinstance(entry, dict):
            raise boxes.HarnessError(f"the kind policy {path}: every `kind` entry must be a table")
        extra = set(entry) - {"name", "allow", "forbid"}
        if extra:
            raise boxes.HarnessError(
                f"the kind policy {path} has keys nobody reads: {', '.join(sorted(extra))}"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise boxes.HarnessError(f"the kind policy {path}: a kind entry has no name")
        declared[name] = Kind(
            name=name,
            allow=_patterns(entry.get("allow"), path),
            forbid=_patterns(entry.get("forbid"), path),
        )
    return declared


def _patterns(value: object, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise boxes.HarnessError(
            f"the kind policy {path}: paths must be a list of non-empty strings"
        )
    return tuple(value)


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


def violations(
    contract: Contract,
    paths: tuple[str, ...],
    diff: str,
    kinds: dict[str, Kind] | None = None,
) -> tuple[Violation, ...]:
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
        if kinds is not None and contract.kind is not None:
            kind = kinds.get(contract.kind, ANY_KIND)
            if kind.allow and not any(matches(pattern, path) for pattern in kind.allow):
                found.append(
                    Violation(
                        "outside-kind",
                        f"{path}: the kind {contract.kind!r} does not own this path",
                    )
                )
            if any(matches(pattern, path) for pattern in kind.forbid):
                found.append(
                    Violation(
                        "kind-forbidden",
                        f"{path}: the kind {contract.kind!r} never writes this path",
                    )
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
    began = time.monotonic()
    started = (when or datetime.now(UTC)).replace(microsecond=0)
    try:
        diff = patch.read_text(encoding="utf-8")
    except OSError as error:
        raise boxes.HarnessError(f"cannot read the patch {patch}: {error}") from error
    prefix = _prefix(project)
    change = _normalize(diff, prefix)
    change, repaired = _recount(change)
    paths = touched_paths(change)
    digest = patch_digest(project, diff)
    problems = violations(contract, paths, change, read_kinds(project))
    number = _next_number(box_root, contract.task)
    directory = box_root / "tabularium" / contract.task / str(number)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "change.diff").write_text(change, encoding="utf-8")
    if change != diff:
        # Kept, because the gate judged a rewritten change: the record must show both.
        (directory / "change.source.diff").write_text(diff, encoding="utf-8")

    status = "refused" if problems else "applied"
    reason = _reason(problems)
    if not problems:
        applied, told = _apply(project, directory / "change.diff", paths, prefix)
        if applied in ("ok", "ok-relaxed"):
            reason = "; ".join(item for item in (repaired, told) if item)
        elif applied == "git" and _already_applied(
            project, directory / "change.diff", prefix
        ):
            # A crash between the write and its record leaves the change in the tree with
            # nothing to show for it. Applying it again fails, applying it backwards
            # succeeds: the tree already has it, so the gate's answer is "applied", with
            # the reason said out loud instead of a second refusal nobody can act on.
            reason = (
                "the tree already had this change: an earlier attempt applied it and did "
                "not record the result"
            )
        else:
            status, reason = "refused", told
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
        seconds=round(time.monotonic() - began, 2),
    )
    _write_records(box_root, project, contract, attempt, started, finished)
    return attempt


def commit_change(
    project: Path,
    contract: Contract,
    attempt: Attempt,
    *,
    verdict: str,
    report: str,
    box_root: Path,
    when: datetime | None = None,
) -> Commit:
    """Make the reviewed change the project's next commit, and nothing else durable.

    The commit is built from ``HEAD`` plus the recorded change in a temporary index, never by
    staging whatever the working tree happens to hold: the verdict is about the change, so the
    commit must be about the same change. A tree that carried unrelated edits keeps them as
    edits; they are not allowed to enter a commit that says "this passed the checks".

    Ambiguity is answered with a record, not with a write: a project that moved, a repository
    that would not take the patch, or a change already in ``HEAD`` gives ``skipped`` or
    ``failed`` with the reason written down, and the verdict itself still stands.
    """
    began = time.monotonic()
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    commit, message, base = _make_commit(
        project, contract, attempt, verdict=verdict, report=report, when=stamp
    )
    commit = replace(commit, seconds=round(time.monotonic() - began, 2))
    _write_commit(box_root, contract, attempt, commit, verdict, report, stamp, message, base)
    return commit


def _make_commit(
    project: Path,
    contract: Contract,
    attempt: Attempt,
    *,
    verdict: str,
    report: str,
    when: datetime,
) -> tuple[Commit, str, str | None]:
    """The git work, split out so every outcome is recorded by the caller exactly once."""
    if attempt.status != "applied":
        reason = "the attempt was refused: there is nothing to commit"
        return Commit("skipped", reason=reason), "", None
    top = _git(project, "rev-parse", "--show-toplevel")
    if top is None:
        return Commit("skipped", reason=f"{project} is not a git repository"), "", None
    top_path = Path(top)
    base = _head(top_path)
    if base is None:
        return Commit("skipped", reason="the project has no commit to build on"), "", None
    patch = attempt.directory / "change.diff"
    if not patch.is_file():
        reason = f"the recorded change is gone from {attempt.directory}"
        return Commit("failed", reason=reason), "", base
    message = _message(contract, attempt, verdict, report)
    tree = _tree_of_change(top_path, base, _prefix(project), patch)
    if isinstance(tree, Commit):
        return tree, message, base
    made = _git_env(
        top_path,
        ["commit-tree", tree, "-p", base, "-F", "-"],
        {**_clean_env(), **_author_env(when)},
        message,
    )
    if made is None:
        return Commit("failed", reason="git refused to create the commit"), message, base
    current = _head(top_path)
    if current != base:
        where = current[:12] if current else "a detached state"
        reason = (
            f"the project moved to {where} while the verdict was being made: the change is "
            "not committed"
        )
        return Commit("failed", reason=reason), message, base
    moved = _run(
        top_path,
        ["update-ref", "-m", f"pantheon: {contract.task}", "HEAD", made, base],
        _clean_env(),
    )
    if moved.returncode != 0:
        reason = f"the branch moved while the change was being committed: {_tail(moved)}"
        return Commit("failed", reason=reason), message, base
    notes: tuple[str, ...] = ()
    paths = [_prefix(project) + path for path in attempt.paths]
    if paths and _run(top_path, ["reset", "-q", "--", *paths], _clean_env()).returncode != 0:
        notes = (
            "the index was not refreshed for the committed paths: `git status` may still show "
            "them as changes",
        )
    return Commit("made", sha=made, subject=_subject(contract), notes=notes), message, base


def _tree_of_change(top: Path, base: str, prefix: str, patch: Path) -> str | Commit:
    """``HEAD`` plus the recorded change, staged in a temporary index.

    The real index and the working tree are never read here, so a tree that carries unrelated
    edits cannot put them into a commit that says "this passed the checks". The tree object is
    all the commit needs; the index itself is thrown away with the temporary directory.
    """
    with tempfile.TemporaryDirectory(prefix="pantheon-commit-") as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        if _run(top, ["read-tree", base], env).returncode != 0:
            return Commit("failed", reason=f"git could not read {base[:12]} into an index")
        # The allowance the gate may have needed: a hunk with context on one side only applies
        # with --unidiff-zero, and the commit builds the same change from the same patch, so it
        # must accept the same diffs the gate accepted. Without it a passing change would be
        # verified and then refused at the commit step.
        staged = ["apply", "--cached", "--unidiff-zero", "--whitespace=nowarn"]
        reverse = [
            "apply",
            "--cached",
            "--reverse",
            "--check",
            "--unidiff-zero",
            "--whitespace=nowarn",
        ]
        for args in (staged, reverse):
            if prefix:
                args.append(f"--directory={prefix}")
            args.append(str(patch))
        done = _run(top, staged, env)
        if done.returncode != 0:
            if _run(top, reverse, env).returncode == 0:
                return Commit(
                    "skipped",
                    reason=f"the change is already in {base[:12]}: there is nothing to commit",
                )
            return Commit("failed", reason=f"git would not stage the change: {_tail(done)}")
        tree = _git_env(top, ["write-tree"], env)
        if tree is None:
            return Commit("failed", reason="git could not write a tree for the change")
        return tree


def commit_json(commit: Commit) -> dict[str, object]:
    """The commit for a machine: what became durable, and what stopped it."""
    return {
        "status": commit.status,
        "sha": commit.sha or None,
        "subject": commit.subject,
        "reason": commit.reason,
        "notes": list(commit.notes),
        "seconds": commit.seconds,
    }


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
        "seconds": attempt.seconds,
    }


def _subject(contract: Contract) -> str:
    """One line a person reads in the log: what the task was, not what the diff was."""
    kind = contract.kind or "task"
    goal = (contract.goal or contract.task).splitlines()[0].strip()
    if len(goal) > 68:
        goal = goal[:65].rstrip() + "..."
    return f"{kind}: {goal}"


def _message(contract: Contract, attempt: Attempt, verdict: str, report: str) -> str:
    """The commit message: the task, the verdict, and where the evidence is.

    The verdict is quoted, not implied: a commit that says only "done" cannot be told apart
    from one whose checks failed. The report lives in the box, so it is named as the box path
    a person would open, not silently dropped.
    """
    return "\n".join(
        [
            _subject(contract),
            "",
            f"Task: {contract.task}",
            f"Verdict: {verdict}",
            f"Report: {report}",
            f"Change: sha256:{attempt.patch_digest}",
            f"Attempt: {attempt.number}",
            "",
            "Caesar applied the change; the verdict was computed by the checks. The commit",
            "records that verdict, it does not replace it.",
            "",
            f"Pantheon-Task: {contract.task}",
            f"Pantheon-Verdict: {verdict}",
            "",
        ]
    )


def _author_env(when: datetime) -> dict[str, str]:
    """git needs an author; the harness signs its commits so a person is not blamed for them."""
    name, email = COMMIT_AUTHOR
    stamp = when.isoformat()
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }


def _clean_env() -> dict[str, str]:
    """The real environment, minus a temporary index: these calls are about the real one."""
    return {key: value for key, value in os.environ.items() if key != "GIT_INDEX_FILE"}


def _run(
    base: Path, args: list[str], env: dict[str, str], stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(base), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        input=stdin,
    )


def _git_env(
    base: Path, args: list[str], env: dict[str, str], stdin: str | None = None
) -> str | None:
    done = _run(base, args, env, stdin)
    return done.stdout.strip() if done.returncode == 0 else None


def _tail(done: subprocess.CompletedProcess[str]) -> str:
    lines = (done.stderr or done.stdout).strip().splitlines()
    return "; ".join(lines[-3:]) or "git refused without a reason"


def _write_commit(
    box_root: Path,
    contract: Contract,
    attempt: Attempt,
    commit: Commit,
    verdict: str,
    report: str,
    when: datetime,
    message: str,
    base: str | None,
) -> None:
    """Keep what happened to the commit beside the attempt, and say it in the ledger.

    An attempt built by hand has no directory of its own: the ledger still gets the event, but
    there is no place to keep the record, and inventing one would be worse than saying nothing.
    """
    directory = attempt.directory
    if directory == Path():
        _append_commit_event(box_root, contract, attempt, commit, when)
        return
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": SCHEMA_VERSION,
        "task": contract.task,
        "attempt": attempt.number,
        "status": commit.status,
        "sha": commit.sha,
        "base": base,
        "verdict": verdict,
        "report": report,
        "subject": commit.subject,
        "reason": commit.reason,
        "notes": list(commit.notes),
        "message": message,
        "at": _stamp(when),
        "seconds": commit.seconds,
    }
    (directory / "commit.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_commit_event(box_root, contract, attempt, commit, when)


def _append_commit_event(
    box_root: Path, contract: Contract, attempt: Attempt, commit: Commit, when: datetime
) -> None:
    ledger = box_root / "fasti"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    event = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": f"commit.{commit.status}",
        "at": _stamp(when),
        "task": contract.task,
        "attempt": attempt.number,
        "actor": "caesar",
        "reason": commit.reason or commit.sha,
        "links": [_under(attempt.directory, box_root)],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _under(path: Path, root: Path) -> str:
    """The path as the box sees it, or as it is: evidence outside the box is still evidence."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def patch_digest(project: Path, diff: str) -> str:
    """The digest of a change in the project's own coordinates.

    Two diffs that say the same thing about the project give one digest, whether they arrived
    from a model, from a file, or from the project sitting inside a larger repository. The
    attempt records this digest; a retry compares a new proposal against it before applying.
    """
    change = _normalize(diff, _prefix(project))
    return hashlib.sha256(change.encode("utf-8")).hexdigest()


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


HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _recount(change: str) -> tuple[str, str]:
    """Rewrite each hunk header from its body: git trusts the count and drops what exceeds it.

    A model that writes "3 lines" above a four-line hunk is not a refused change; it is a
    mistyped header. ``git apply`` takes the header at its word and silently leaves the last
    line out, so the box would judge a tree the model never wrote. The body is the change, so
    the header is corrected to what the body carries, and the record keeps the original diff
    beside it (``change.source.diff``) — the repair is visible, not quiet.
    """
    lines = change.splitlines(keepends=True)
    out: list[str] = []
    notes: list[str] = []
    index = 0
    while index < len(lines):
        match = HUNK_HEADER.match(lines[index].rstrip("\n"))
        if match is None:
            out.append(lines[index])
            index += 1
            continue
        start = index + 1
        removed = added = 0
        while start < len(lines):
            body = lines[start]
            if body.startswith(" "):
                removed += 1
                added += 1
            elif body.startswith("\\"):
                pass  # "no newline at end of file": a marker for the line above it
            elif body.startswith("+"):
                added += 1
            elif body.startswith("-"):
                removed += 1
            else:
                break
            start += 1
        declared = (int(match.group(2) or 1), int(match.group(4) or 1))
        if (removed, added) != declared:
            notes.append(
                f"a hunk header said -{declared[0]}/+{declared[1]} but its body carried "
                f"-{removed}/+{added}"
            )
            out.append(
                f"@@ -{match.group(1)},{removed} +{match.group(3)},{added} @@{match.group(5)}\n"
            )
        else:
            out.append(lines[index])
        out.extend(lines[index + 1 : start])
        index = start
    note = (
        ""
        if not notes
        else "; ".join(notes) + ": the header was corrected and the body applied as written"
    )
    return "".join(out), note


def _apply(
    project: Path, patch: Path, paths: tuple[str, ...], prefix: str
) -> tuple[str, str]:
    """Apply the patch where the project actually lives, or say why it did not.

    Three traps are handled here, all found on a real box rather than in a test:

    * the project may be a **subdirectory** of its repository. ``git apply`` run from that
      subdirectory resolves patch paths from the repository root, silently skips every path
      that is not under the current one, and still exits zero — so the gate said "applied"
      while the tree was untouched. The diff is normalised to the project and applied from the
      top level with ``--directory=<prefix>``;
    * a patch can succeed and change nothing (an empty diff, a hunk that was already
      applied). Success is therefore the tree changing, not the exit code;
    * **a hunk with context on one side only**. Models write them, ``patch`` applies them, and
      ``git apply`` refuses them unless told ``--unidiff-zero``: its default mode demands
      context after the change as well as before it. The strict apply is tried first; only
      when it refuses does the same patch get one more chance with the allowance, and the
      record says which path it took — a repair that is visible, not quiet.

    The answer comes back with its kind: ``"ok"`` and ``"ok-relaxed"`` mean applied (the
    second names the allowance), ``"git"`` means git refused it, ``"noop"`` means it applied
    and nothing changed. A git refusal may mean the change is already there; a no-op never does.
    """
    top = Path(_git(project, "rev-parse", "--show-toplevel") or project)
    before = _tree_signature(top, tuple(prefix + path for path in paths))
    done = _run_apply(top, patch, prefix)
    if done.returncode == 0:
        kind, told = "ok", ""
    else:
        relaxed = _run_apply(top, patch, prefix, relaxed=True)
        if relaxed.returncode != 0:
            detail = (done.stderr or done.stdout).strip().splitlines()
            return (
                "git",
                "the project would not take the patch: "
                + ("; ".join(detail[-3:]) or "git apply failed"),
            )
        kind, told = (
            "ok-relaxed",
            "the hunk carried context on one side only: the strict apply refused it and it "
            "applied with --unidiff-zero against the same context",
        )
    if _tree_signature(top, tuple(prefix + path for path in paths)) == before:
        return (
            "noop",
            "the patch changed nothing: git reported success, but the paths it names are the "
            "same afterwards",
        )
    return kind, told


def _run_apply(
    top: Path, patch: Path, prefix: str, *, relaxed: bool = False
) -> subprocess.CompletedProcess[str]:
    """One `git apply` run: strict by default, with the one-sided-context allowance if asked."""
    command = ["git", "-C", str(top), "apply", "--whitespace=nowarn"]
    if relaxed:
        command.append("--unidiff-zero")
    if prefix:
        command.append(f"--directory={prefix}")
    command.append(str(patch))
    return subprocess.run(command, capture_output=True, text=True, check=False)


def revert(project: Path, patch: Path) -> tuple[bool, str]:
    """Undo the draft a failed attempt left in the tree, using the attempt's own change.

    Applying the recorded change backwards touches only the lines that attempt wrote: anything
    else in the tree stays. A tree that moved since fails here, with git's own words, instead
    of failing later in the commit with a message about the wrong thing.
    """
    prefix = _prefix(project)
    top = Path(_git(project, "rev-parse", "--show-toplevel") or project)
    command = [
        "git",
        "-C",
        str(top),
        "apply",
        "--reverse",
        "--unidiff-zero",
        "--whitespace=nowarn",
    ]
    if prefix:
        command.append(f"--directory={prefix}")
    command.append(str(patch))
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return False, "; ".join(detail[-3:]) or "git apply --reverse failed"
    return True, ""


def _already_applied(project: Path, patch: Path, prefix: str) -> bool:
    """Does the tree already contain this change? Asked by applying it backwards, dry-run."""
    top = _git(project, "rev-parse", "--show-toplevel")
    if top is None:
        return False
    command = [
        "git",
        "-C",
        top,
        "apply",
        "--reverse",
        "--check",
        "--unidiff-zero",
        "--whitespace=nowarn",
    ]
    if prefix:
        command.append(f"--directory={prefix}")
    command.append(str(patch))
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    return done.returncode == 0


def head(project: Path) -> str | None:
    """The commit the project is on, or ``None`` outside git: a tree needs a base to resume."""
    return _head(project)


def contains(project: Path, patch: Path) -> bool:
    """Whether the tree still holds the change a recorded attempt applied."""
    return _already_applied(project, patch, _prefix(project))


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
    base = box_root / "tabularium" / task
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
        "seconds": attempt.seconds,
    }
    (base / "attempt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_event(box_root, contract, attempt, finished)


def _append_event(
    box_root: Path, contract: Contract, attempt: Attempt, when: datetime
) -> None:
    ledger = box_root / "fasti"
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
        "links": [_under(attempt.directory, box_root)],
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
