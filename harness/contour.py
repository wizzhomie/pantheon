"""The box calls trajan: a change and a policy become a report the box keeps.

The box does not re-implement verification and does not read the verdict out of the exit code
alone: it runs trajan as a separate process, keeps the run directory inside the box's
``artifacts/``, and reports the verdict trajan computed. That is the stage 1.2 contract — the
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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import box as boxes

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

    @property
    def passed(self) -> bool:
        return self.status == "pass"


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
    reviewer: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    box_root: Path | None = None,
) -> VerifyResult:
    """Run ``trajan verify`` for one project and read back the report it wrote.

    ``box_root`` turns on reuse: a checks-only run on a tree, policy and checker version that
    already produced a settled verdict is answered from that run instead of running again.
    The answer is the earlier one, the record says where it came from, and a verdict that was
    not settled — ``inconclusive`` — is never reused, because it may have been the environment.
    """
    out.mkdir(parents=True, exist_ok=True)
    policy_path = policy or project / ".trajan" / "policy.toml"
    evidence = (
        digest(project, policy_path, root)
        if box_root is not None and diff is None and since is None and reviewer is None
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
    if reviewer is not None:
        command += ["--reviewer", reviewer]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=_environment(root, python),
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


def digest(project: Path, policy: Path, root: Path) -> dict[str, str]:
    """What a verdict depends on: the tree, the policy, and the version of the checker."""
    return {
        "tree": _tree_digest(project),
        "policy": _file_digest(policy),
        "trajan": _trajan_digest(root),
    }


def _previous(
    box_root: Path, project: Path, digest_map: dict[str, str]
) -> tuple[Path, dict[str, object]] | None:
    """The newest earlier run whose evidence still matches: same tree, policy and checker."""
    artifacts = box_root / "artifacts"
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
    """Answer with the recorded verdict, and say in this run's own record where it came from."""
    record, data = found
    report = Path(str(data.get("report")))
    earlier = read_report(report)
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
    """
    top = _git(project, "rev-parse", "--show-toplevel")
    if top is None:
        return _plain_digest(project)
    # `git ls-files` names paths from the repository root even inside a subdirectory.
    base = Path(top.strip())
    tracked = _git(project, "ls-files", "-z", "--", ".") or ""
    untracked = (
        _git(project, "ls-files", "--others", "--exclude-standard", "-z", "--", ".") or ""
    )
    paths = sorted({item for item in (tracked + "\0" + untracked).split("\0") if item})
    parts = [f"{relative} {_file_digest(base / relative)}" for relative in paths]
    return _sha("\n".join(parts))


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
        checks=len(run.get("checks", [])) if isinstance(run.get("checks"), list) else 0,
        exit_code=exit_code,
        reviewed=reviewed,
    )


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
    if result.reused_from is not None:
        lines.append(f"reused     {result.reused_from}")
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
        "findings": result.findings,
        "blocking": result.blocking,
        "report": str(result.report),
        "reused_from": None if result.reused_from is None else str(result.reused_from),
        "exit_code": result.exit_code,
    }


def _environment(root: Path, python: Path) -> dict[str, str]:
    """Trajan imports from its ``src``, and its checks resolve ``python`` from PATH."""
    env = dict(os.environ)
    source = str(root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{source}{os.pathsep}{existing}" if existing else source
    env["PATH"] = f"{python.parent}{os.pathsep}{env.get('PATH', os.defpath)}"
    return env
