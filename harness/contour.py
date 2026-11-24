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

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import box as boxes

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAJAN_ENV = "PANTHEON_TRAJAN"
DEFAULT_TIMEOUT = 900


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
) -> VerifyResult:
    """Run ``trajan verify`` for one project and read back the report it wrote."""
    out.mkdir(parents=True, exist_ok=True)
    command = [
        str(python),
        "-m",
        "trajan",
        "verify",
        "--repo",
        str(project),
        "--policy",
        str(policy or project / ".trajan" / "policy.toml"),
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
    return read_report(report, exit_code=done.returncode)


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
