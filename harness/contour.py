"""The box calls trajan: a change and a policy become a report the box keeps.

The box does not re-implement verification and does not read the verdict out of the exit code
alone: it runs trajan as a separate process, keeps the run directory inside the box's
``tabularium/``, and reports the verdict trajan computed. That is the stage 1.2 contract — the
answer here must equal the answer of running trajan directly on the same change, and a test
compares the two instead of trusting the wiring.

Which trajan: ``--trajan``, then ``$PANTHEON_TRAJAN``, then ``trajan/`` next to this
checkout. Which interpreter: ``--python``, then that checkout's venv, then the interpreter
running the harness. The contour's own checks resolve ``python`` on PATH, so the interpreter
that runs trajan is put in front of PATH — otherwise a box would verify a project with a
different Python than the one it was told to use.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import box as boxes
from . import caesar

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAJAN_ENV = "PANTHEON_TRAJAN"
DEFAULT_TIMEOUT = 900
RECORD_FILE = "box-verify.json"
"""The box's own note beside a run: what the verdict depended on, and where it came from."""
RECORD_SCHEMA = 1
REUSE_WINDOW = timedelta(hours=24)
"""A settled verdict is reused only if it is not older than this: time is not in the digest."""
REUSABLE = frozenset({"pass", "pass_with_conditions"})
# A hold is re-run: the usual reason to look again is that something was fixed, and the cheap
# way to find out is to run the checks. Only a settled "this tree is shippable" is reused.

REVIEW_SPEC = "trajan.llm.command:from_env"
"""The reviewer trajan runs as a command: the box's agent, asked to read instead of write."""
REVIEW_COMMAND = Path("bin") / "pantheon-review"
REVIEW_DEFAULT_MODEL = "deepseek-flash"
REVIEW_DEFAULT_SECONDS = 240
REVIEW_TIMEOUT_SLACK = 60
"""The command stops at ``REVIEW_DEFAULT_SECONDS``; trajan's own timeout is above it, so a
slow review is reported by the reviewer that ran it, not by the caller killing the process."""
REVIEW_LOG_FILE = "review-usage.jsonl"
"""One line per reviewer call, written into the run directory: what the reading cost."""

REVIEW_BOX_ENV = "PANTHEON_REVIEW_BOX"
REVIEW_MODEL_ENV = "PANTHEON_REVIEW_MODEL"
REVIEW_SECONDS_ENV = "PANTHEON_REVIEW_SECONDS"
REVIEW_LOG_ENV = "PANTHEON_REVIEW_LOG"
REVIEW_COMMAND_ENV = "TRAJAN_REVIEW_COMMAND"
REVIEW_TIMEOUT_ENV = "TRAJAN_REVIEW_COMMAND_TIMEOUT"


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """What one trajan run said, and where it said it."""

    run: Path
    report: Path
    run_id: str
    status: str
    reasons: tuple[str, ...]
    findings: int
    blocking: int
    checks: int
    exit_code: int
    reused_from: Path | None = None
    reviewed: bool = False
    """Whether a reviewer read the change: a checks-only run says nothing about the change."""

    review: Review | None = None
    """The reading of the change, when a reviewer ran: who read it, what it found, what it cost."""

    skipped: tuple[str, ...] = ()
    """Checks the plan did not select: a decision the report explains, not a gap."""

    seconds: float = 0.0
    """Wall time the checks took; zero when the verdict was reused, because nothing ran."""

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass(frozen=True, slots=True)
class Review:
    """One reviewer's answer, in the box's words: model, counts, and the bill it left."""

    model: str | None
    status: str
    proposed: int
    confirmed: int
    prompt_tokens: int
    completion_tokens: int
    calls: int
    cost_cents: float | None
    error: str | None = None
    cached: bool = False
    """Served from the review cache: the same diff was read before, so this run paid nothing."""

    files_reviewed: int | None = None
    """How many files the reviewer was actually shown; ``None`` when the report did not say."""

    truncated: bool = False
    """The change was cut before the reviewer saw it: the reading is partial, not empty."""

    @property
    def read(self) -> bool:
        """A reader answered. A failed review is a reader that was asked and could not answer."""
        return self.status == "ok"

    def line(self) -> str:
        """The review as one line for a person: who, how much found, what it cost."""
        if not self.read:
            return f"failed — {self.error or 'no reason recorded'}"
        if self.cached:
            return (
                f"{self.model or 'not named'}: {self.confirmed} confirmed of {self.proposed} "
                "proposed, served from the review cache — the same diff was read before, so "
                "this run paid nothing"
            )
        money = "cost not reported" if self.cost_cents is None else f"{self.cost_cents:.2f} cents"
        return (
            f"{self.model or 'not named'}: {self.confirmed} confirmed of {self.proposed} "
            f"proposed, {self.prompt_tokens}+{self.completion_tokens} tokens, {money}"
        )


@dataclass(frozen=True, slots=True)
class Reviewer:
    """How trajan reaches the reader: a command, the environment, and what the box allows.

    ``max_cents`` is the ceiling the box declared for one reading, or ``None`` when nobody
    declared one. It cannot stop a call that already happened, so it is spent as a judgement:
    a reading above the ceiling is recorded and named as a gap.
    """

    spec: str
    env: dict[str, str]
    max_cents: float | None = None
    when_paths: tuple[str, ...] = ()
    """The changes this reader reads by declaration. Empty means every change."""

    def declines(self, paths: tuple[str, ...]) -> str | None:
        """Why this change is not read, or ``None``: the box may read only some paths.

        Only touched paths decide: a path list that cannot be read is not a reason to skip,
        because "I could not tell" must not spend like "I decided not to read".
        """
        if not self.when_paths or not paths:
            return None
        if any(
            caesar.matches(pattern, path) for pattern in self.when_paths for path in paths
        ):
            return None
        return (
            "the change touches no path this box reads (declared: "
            + ", ".join(self.when_paths)
            + ")"
        )


def box_reviewer(
    box_root: Path,
    *,
    model: str | None = None,
    seconds: int | None = None,
    max_cents: float | None = None,
    when_paths: tuple[str, ...] = (),
) -> Reviewer:
    """The box's agent as a reviewer. Opt-in: a review is a paid call, never a default."""
    wait = seconds or REVIEW_DEFAULT_SECONDS
    return Reviewer(
        spec=REVIEW_SPEC,
        env={
            REVIEW_COMMAND_ENV: str(REPO_ROOT / REVIEW_COMMAND),
            REVIEW_TIMEOUT_ENV: str(wait + REVIEW_TIMEOUT_SLACK),
            REVIEW_BOX_ENV: str(box_root),
            REVIEW_MODEL_ENV: model or REVIEW_DEFAULT_MODEL,
            REVIEW_SECONDS_ENV: str(wait),
        },
        max_cents=max_cents,
        when_paths=when_paths,
    )


def trajan_root(chosen: str | None = None) -> Path:
    """The trajan checkout to run: the flag, the environment, then the sibling directory."""
    candidates = [chosen, os.environ.get(TRAJAN_ENV), str(REPO_ROOT / "trajan")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if (path / "src" / "trajan" / "__init__.py").is_file():
            return path
    raise boxes.HarnessError(
        "no trajan checkout to call: pass --trajan, set PANTHEON_TRAJAN, or keep trajan/ "
        f"next to the harness (looked at {REPO_ROOT / 'trajan'})"
    )


def trajan_python(root: Path, chosen: str | None = None) -> Path:
    """The interpreter that runs trajan: the flag, the checkout's venv, then this one."""
    if chosen:
        return Path(chosen).expanduser().resolve()
    venv = root / ".venv" / "bin" / "python"
    return venv if venv.is_file() else Path(sys.executable)


def run_verify(
    project: Path,
    *,
    root: Path,
    python: Path,
    out: Path,
    policy: Path | None = None,
    since: str | None = None,
    diff: Path | None = None,
    paths: tuple[str, ...] | None = None,
    reviewer: str | None = None,
    reviewer_env: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    box_root: Path | None = None,
) -> VerifyResult:
    """Run ``trajan verify`` for one project and read back the report it wrote.

    ``box_root`` turns on reuse: a checks-only run on a tree, policy and checker version that
    already produced a settled verdict is answered from that run instead of running again.
    The answer is the earlier one, the record says where it came from, and a verdict that was
    not settled — ``inconclusive`` — is never reused, because it may have been the environment.

    ``reviewer_env`` carries what the reviewer command needs to find its box, and where to
    write its bill; the bill is read back from the run directory, so a review's cost lives
    with the run that paid it.
    """
    out.mkdir(parents=True, exist_ok=True)
    policy_path = policy or project / ".trajan" / "policy.toml"
    evidence = (
        digest(
            project,
            policy_path,
            root,
            change=diff,
            reviewed=reviewer is not None,
            paths=None if diff is not None else paths,
        )
        if box_root is not None and since is None
        else None
    )
    if evidence is not None and box_root is not None:
        found = _previous(box_root, project, evidence)
        if found is not None:
            return _reuse(found, out, project, policy_path, evidence)
    command = [
        str(python),
        "-m",
        "trajan",
        "verify",
        "--repo",
        str(project),
        "--policy",
        str(policy_path),
        "--out",
        str(out),
    ]
    # Trajan refuses a change without a reader ("a request with a change needs a reviewer, or
    # the change is not read"), so a checks-only run passes no change at all and the report
    # says the change was not read. That refusal is the contour's doctrine; the box repeats it
    # in its own words before spending a call.
    if diff is not None:
        command += ["--diff", str(diff)]
    elif since is not None:
        command += ["--since", since]
    elif paths:
        # The change is not read, but the plan still narrows by what it touches: a check
        # whose paths the change never touched is a decision, not a gap in the evidence.
        command += ["--paths", *paths]
    if reviewer is not None:
        command += ["--reviewer", reviewer]
    log = out / REVIEW_LOG_FILE
    env = _environment(root, python, project)
    began = time.monotonic()
    if reviewer is not None and reviewer_env is not None:
        env.update(reviewer_env)
        env[REVIEW_LOG_ENV] = str(log)
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as expired:
        raise boxes.HarnessError(
            f"trajan did not finish in {timeout}s; the run is in {out} and was stopped"
        ) from expired
    except OSError as error:
        raise boxes.HarnessError(f"cannot run trajan with {python}: {error}") from error
    report = out / "report.json"
    if not report.is_file():
        tail = (done.stderr or done.stdout).strip().splitlines()[-5:]
        hint = "; ".join(tail) if tail else f"trajan exited {done.returncode}"
        raise boxes.HarnessError(f"trajan wrote no report to {report}: {hint}")
    result = read_report(report, exit_code=done.returncode)
    result = replace(result, seconds=round(time.monotonic() - began, 2))
    if result.review is not None:
        result = replace(result, review=_with_bill(result.review, log))
    if evidence is not None and box_root is not None:
        _remember(out, project, policy_path, evidence, result)
    return result


def run_directory(parent: Path, when: datetime) -> Path:
    """A run directory that never overwrites an earlier run: the stamp, then a counter."""
    base = "verify-" + when.strftime("%Y%m%dT%H%M%SZ")
    candidate = base
    number = 2
    while (parent / candidate).exists():
        candidate = f"{base}-{number}"
        number += 1
    return parent / candidate


def digest(
    project: Path,
    policy: Path,
    root: Path,
    *,
    change: Path | None = None,
    reviewed: bool = False,
    paths: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """What a verdict depends on: the tree, the policy, the checker — and what was asked.

    A change makes a run narrower: the plan may not select every check, and a reader may have
    produced findings. So the change's own digest and whether someone read it are part of the
    evidence: the same tree with a different diff, or the same diff read by someone versus
    nobody, is a different question and gets its own answer.
    """
    return {
        "tree": _tree_digest(project),
        "policy": _file_digest(policy),
        "trajan": _trajan_digest(root),
        "change": "none" if change is None else _file_digest(change),
        "paths": "all" if not paths else "|".join(sorted(paths)),
        "review": "yes" if reviewed else "no",
    }


def _previous(
    box_root: Path, project: Path, digest_map: dict[str, str]
) -> tuple[Path, dict[str, object]] | None:
    """The newest earlier run whose evidence still matches: same tree, policy and checker."""
    artifacts = box_root / "tabularium"
    if not artifacts.is_dir():
        return None
    target = str(project.resolve())
    now = datetime.now(UTC)
    best: tuple[Path, dict[str, object]] | None = None
    best_at: datetime | None = None
    for record in artifacts.rglob(RECORD_FILE):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("project") != target or data.get("digest") != digest_map:
            continue
        if data.get("status") not in REUSABLE or data.get("reused_from"):
            continue
        report = data.get("report")
        if not isinstance(report, str) or not Path(report).is_file():
            continue
        at = _parse_at(data.get("at"))
        if at is None or now - at > REUSE_WINDOW:
            continue
        if best_at is None or at > best_at:
            best, best_at = (record, {str(k): v for k, v in data.items()}), at
    return best


def _reuse(
    found: tuple[Path, dict[str, object]],
    out: Path,
    project: Path,
    policy: Path,
    digest_map: dict[str, str],
) -> VerifyResult:
    """Answer with the recorded verdict, and say in this run's own record where it came from.

    The reading comes back with its bill: a reused review keeps the cents it cost when it ran,
    because dropping them would make one dispatch look cheaper than the one that paid.
    """
    record, data = found
    report = Path(str(data.get("report")))
    earlier = read_report(report)
    stored = data.get("review")
    if earlier.review is not None and isinstance(stored, dict):
        cents = stored.get("cost_cents")
        calls = stored.get("calls")
        earlier = replace(
            earlier,
            review=replace(
                earlier.review,
                cost_cents=float(cents) if isinstance(cents, (int, float)) else None,
                calls=int(calls) if isinstance(calls, int) else 0,
            ),
        )
    payload: dict[str, object] = {
        "schema_version": RECORD_SCHEMA,
        "project": str(project.resolve()),
        "policy": str(policy.resolve()),
        "digest": digest_map,
        "status": earlier.status,
        "report": str(report),
        "at": _stamp(datetime.now(UTC)),
        "reused_from": str(record),
        "earlier_at": data.get("at"),
    }
    (out / RECORD_FILE).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return replace(
        earlier,
        run=out,
        reused_from=record.parent,
        exit_code=0 if earlier.status in REUSABLE else 1,
    )


def _remember(
    out: Path, project: Path, policy: Path, digest_map: dict[str, str], result: VerifyResult
) -> None:
    """Note what this run's verdict rested on, so a later identical request can reuse it."""
    payload: dict[str, object] = {
        "schema_version": RECORD_SCHEMA,
        "project": str(project.resolve()),
        "policy": str(policy.resolve()),
        "digest": digest_map,
        "status": result.status,
        "report": str(result.report),
        "at": _stamp(datetime.now(UTC)),
        "reused_from": None,
        "review": None
        if result.review is None
        else {
            "model": result.review.model,
            "status": result.review.status,
            "proposed": result.review.proposed,
            "confirmed": result.review.confirmed,
            "prompt_tokens": result.review.prompt_tokens,
            "completion_tokens": result.review.completion_tokens,
            "calls": result.review.calls,
            "cost_cents": result.review.cost_cents,
            "cached": result.review.cached,
            "files_reviewed": result.review.files_reviewed,
            "truncated": result.review.truncated,
            "error": result.review.error,
        },
    }
    (out / RECORD_FILE).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "absent"


def _tree_digest(project: Path) -> str:
    """The working tree as a value: the content of every tracked and untracked file.

    Not the commit: moving a file into a commit does not change what the checks read, and a
    verdict about the same bytes should survive one. Ignored files are not covered — a check
    that reads them can answer differently on the same digest, which is why reuse lives in a
    window and says where it came from.

    Trajan's own bookkeeping inside the repository — its run history and its review cache —
    is skipped on purpose. Those files are written by the checker while it runs, so counting
    them would change the digest of the very tree that was just judged, and no verdict could
    ever be reused. They cannot change an answer either: the cache is keyed by the request,
    and a cached review of the same diff is the same review.
    """
    top = _git(project, "rev-parse", "--show-toplevel")
    if top is None:
        return _plain_digest(project)
    base = Path(top.strip())
    # `ls-files` names paths relative to the directory it runs in, and the files are read from
    # the repository root: `--full-name` is what makes a project in a subdirectory digest its
    # own content instead of a list of missing files. Found on the demo box, where editing the
    # model left the digest unchanged and an earlier verdict about a different tree was reused.
    scope = "." if base == project.resolve() else str(project.resolve().relative_to(base))
    tracked = _git(base, "ls-files", "-z", "--full-name", "--", scope) or ""
    untracked = (
        _git(base, "ls-files", "--others", "--exclude-standard", "-z", "--full-name", "--", scope)
        or ""
    )
    paths = sorted({item for item in (tracked + "\0" + untracked).split("\0") if item})
    parts = [
        f"{relative} {_file_digest(base / relative)}"
        for relative in paths
        if not _is_checker_state(project, base, relative)
    ]
    return _sha("\n".join(parts))


CHECKER_STATE = (".trajan/runs", ".trajan/cache")
"""Where trajan keeps its own records inside a project: not project content, noise in a digest."""

PROFILE_FILE = ".trajan/profile.json"
"""Derived from the tree by `trajan inspect`: a second copy of what is already digested."""


def _is_checker_state(project: Path, base: Path, relative: str) -> bool:
    """Whether a path is trajan's own file rather than part of what the checks read."""
    try:
        inside = (base / relative).relative_to(project.resolve()).as_posix()
    except ValueError:
        return False
    if inside == PROFILE_FILE:
        return True
    return any(inside == state or inside.startswith(state + "/") for state in CHECKER_STATE)


def _plain_digest(project: Path) -> str:
    """No git: hash every file, so the digest still means what it says."""
    parts: list[str] = []
    for path in sorted(project.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            parts.append(f"{path.relative_to(project)} {_file_digest(path)}")
    return _sha("\n".join(parts))


def _trajan_digest(root: Path) -> str:
    """The checker itself: its package and its declared version, not the checkout's HEAD."""
    parts: list[str] = []
    package = root / "src" / "trajan"
    for path in sorted(package.rglob("*.py")):
        parts.append(f"{path.relative_to(root)} {_file_digest(path)}")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        parts.append(f"pyproject.toml {_file_digest(pyproject)}")
    return _sha("\n".join(parts))


def _git(path: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else None


def read_report(path: Path, exit_code: int = 0) -> VerifyResult:
    """The one place that knows the shape of trajan's report, so the box reads it once."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"report {path} is not readable json: {error}") from error
    verdict = data.get("verdict", {})
    run = data.get("run", {})
    if not isinstance(verdict, dict) or not isinstance(run, dict) or not verdict or not run:
        raise boxes.HarnessError(
            f"report {path} has no verdict or run section: a report without an answer is not "
            "an answer"
        )
    reasons = tuple(
        f"{reason.get('code')}: {reason.get('detail')}"
        for reason in verdict.get("reasons", [])
        if isinstance(reason, dict)
    )
    findings = data.get("findings", [])
    blocking = verdict.get("blocking_findings", [])
    checks = run.get("checks", [])
    skipped = tuple(
        str(check.get("check_id"))
        for check in checks
        if isinstance(check, dict) and check.get("selected") is False
    ) if isinstance(checks, list) else ()
    request = data.get("request")
    reviewed = isinstance(request, dict) and request.get("reviewer") is not None
    return VerifyResult(
        run=path.parent,
        report=path,
        run_id=str(run.get("run_id", path.parent.name)),
        status=str(verdict.get("status", "unknown")),
        reasons=reasons,
        findings=len(findings) if isinstance(findings, list) else 0,
        blocking=len(blocking) if isinstance(blocking, list) else 0,
        checks=len(checks) if isinstance(checks, list) else 0,
        exit_code=exit_code,
        reviewed=reviewed,
        review=_read_review(data.get("review")),
        skipped=skipped,
    )


def _read_review(section: object) -> Review | None:
    """The reviewer's answer as the report carries it; the bill comes later, from the run dir."""
    if not isinstance(section, dict):
        return None
    return Review(
        model=section.get("model") if isinstance(section.get("model"), str) else None,
        status=str(section.get("status") or "unknown"),
        proposed=_int(section.get("proposed")),
        confirmed=_int(section.get("confirmed")),
        prompt_tokens=_int(section.get("prompt_tokens")),
        completion_tokens=_int(section.get("completion_tokens")),
        calls=0,
        cost_cents=None,
        error=section.get("error") if isinstance(section.get("error"), str) else None,
        cached=section.get("cached") is True,
        files_reviewed=_int(section.get("files_reviewed"))
        if "files_reviewed" in section
        else None,
        truncated=section.get("truncated") is True,
    )


def _with_bill(review: Review, log: Path) -> Review:
    """Add what the reviewer calls cost, summed over every call it made in this run.

    A call with no bill in its line stays unknown: the count says how many calls ran, and
    ``cost_cents`` is ``None`` unless every call reported a price. A half-known bill is not
    a bill.
    """
    calls, cents, prompt, completion = 0, 0.0, 0, 0
    unknown = False
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return review
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        calls += 1
        value = entry.get("cost_cents")
        if isinstance(value, (int, float)):
            cents += float(value)
        else:
            unknown = True
        prompt += _int(entry.get("prompt_tokens"))
        completion += _int(entry.get("completion_tokens"))
    if calls == 0:
        return review
    return replace(
        review,
        calls=calls,
        cost_cents=None if unknown else round(cents, 4),
        prompt_tokens=prompt or review.prompt_tokens,
        completion_tokens=completion or review.completion_tokens,
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def render_result(result: VerifyResult, project: Path) -> list[str]:
    """The result as a person reads it, with the report path last so a script can find it."""
    lines = [
        f"project    {project}",
        f"run        {result.run_id}",
        f"verdict    {result.status}",
    ]
    for reason in result.reasons:
        lines.append(f"reason     {reason}")
    lines.append(
        f"checks     {result.checks}, findings {result.findings} ({result.blocking} blocking)"
    )
    if result.skipped:
        lines.append(f"skipped    {', '.join(result.skipped)} (no changed path they guard)")
    if result.reused_from is not None:
        lines.append(f"reused     {result.reused_from}")
    if result.review is not None:
        lines.append(f"review     {result.review.line()}")
    elif not result.reviewed:
        lines.append("review     none — the change was not read")
    lines.append(f"report     {result.report}")
    return lines


def result_json(result: VerifyResult, project: Path, box: str) -> dict[str, object]:
    """The same answer for a machine: verdict, reasons, findings, and where the evidence is."""
    return {
        "box": box,
        "project": str(project),
        "run_id": result.run_id,
        "verdict": result.status,
        "reasons": list(result.reasons),
        "checks": result.checks,
        "skipped": list(result.skipped),
        "findings": result.findings,
        "blocking": result.blocking,
        "report": str(result.report),
        "reused_from": None if result.reused_from is None else str(result.reused_from),
        "reviewed": result.reviewed,
        "review": None
        if result.review is None
        else {
            "model": result.review.model,
            "status": result.review.status,
            "proposed": result.review.proposed,
            "confirmed": result.review.confirmed,
            "prompt_tokens": result.review.prompt_tokens,
            "completion_tokens": result.review.completion_tokens,
            "calls": result.review.calls,
            "cost_cents": result.review.cost_cents,
            "cached": result.review.cached,
            "files_reviewed": result.review.files_reviewed,
            "truncated": result.review.truncated,
            "error": result.review.error,
        },
        "exit_code": result.exit_code,
    }


def _environment(root: Path, python: Path, project: Path | None = None) -> dict[str, str]:
    """Trajan imports from its ``src``, and its checks resolve ``python`` from PATH.

    The project's own ``.venv/bin`` goes first when it exists: a foreign project's checks must
    run with the interpreter its dependencies were installed into, not with trajan's. The
    interpreter that runs trajan stays on PATH behind it, because trajan itself needs nothing
    from the project's environment.
    """
    env = dict(os.environ)
    source = str(root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{source}{os.pathsep}{existing}" if existing else source
    parts = []
    if project is not None and (project / ".venv" / "bin").is_dir():
        parts.append(str(project / ".venv" / "bin"))
    parts.append(str(python.parent))
    env["PATH"] = os.pathsep.join([*parts, env.get("PATH", os.defpath)])
    return env
