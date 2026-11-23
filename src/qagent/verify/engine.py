"""Run a project's checks, keep the evidence, spend no more than the budget.

The engine is intentionally small. It has three jobs — decide what the plan contains, run
each check as a child process, and write down what happened — and it refuses the fourth
("decide whether the project is good"), which belongs to the verdict layer. That separation
is what keeps a failed test from being quietly reinterpreted by the thing that found it.

Nothing here is async by accident: checks are process-bound, so concurrency buys little
before the reviewer exists, and running them in order makes the spent-time budget honest.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from ..context import ProjectProfile
from .contracts import (
    MAX_OUTPUT_TAIL,
    CheckRun,
    CheckStatus,
    CommandSpec,
    PlanEntry,
    VerificationPolicy,
    VerificationRun,
)

MAX_CAPTURE = 256 * 1024
"""Bytes of stdout/stderr kept per stream; a runaway check must not fill the disk."""

ENV_WHITELIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "VIRTUAL_ENV",
    "PYTHONPATH",
)
"""The child inherits these and nothing else: a check has no business reading API keys."""

ARTIFACT_DIR = "checks"

MIN_CHECK_SECONDS = 0.5
"""Below this much remaining budget a check is skipped, not started to die on the floor."""


def read_policy(path: Path) -> VerificationPolicy:
    """Load a policy from TOML. Unknown keys are an error, not a silent default."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return VerificationPolicy.model_validate(data)


def build_plan(
    policy: VerificationPolicy, profile: ProjectProfile | None = None
) -> list[PlanEntry]:
    """What the policy asks for, and what the project can actually answer.

    A check that requires a tool the profile did not find is *not applicable*, not a failure:
    reporting a missing tool as a broken project would punish repositories that simply do not
    have that linter configured.
    """
    entries: list[PlanEntry] = []
    for check in policy.checks:
        if check.requires and profile is not None and profile.tool(check.requires) is None:
            entries.append(
                PlanEntry(
                    check=check,
                    reason="tool-missing",
                    detail=f"{check.requires} not found in the project profile",
                )
            )
            continue
        entries.append(
            PlanEntry(check=check, reason="optional" if check.optional else "required")
        )
    return entries


def describe_plan(plan: list[PlanEntry]) -> list[str]:
    """Human lines for `qagent run --dry-run`, in execution order."""
    lines: list[str] = []
    for entry in plan:
        marker = {"required": "required", "optional": "optional"}.get(entry.reason, entry.reason)
        command = " ".join(entry.check.argv)
        detail = f" — {entry.detail}" if entry.detail else ""
        purpose = f"  ({entry.check.purpose})" if entry.check.purpose else ""
        lines.append(f"{entry.check.id}: {command} [{marker}]{detail}{purpose}")
    return lines


async def run_policy(
    policy: VerificationPolicy,
    root: Path,
    *,
    out_dir: Path | None = None,
    profile: ProjectProfile | None = None,
) -> tuple[VerificationRun, Path]:
    """Execute the plan, write artifacts, return the record.

    The budget is enforced before each check, not after: a check that cannot finish inside
    what is left is skipped with a reason, so the report can say "not checked, no time".
    """
    root = Path(root).resolve()
    started_at = datetime.now(UTC)
    run_id = f"{started_at:%Y%m%dT%H%M%S}-{policy.name}"
    target = Path(out_dir) if out_dir is not None else root / ".qagent" / "runs" / run_id
    artifacts = target / ARTIFACT_DIR
    plan = build_plan(policy, profile)

    checks: list[CheckRun] = []
    spent = 0.0
    for entry in plan:
        remaining = policy.budget.max_seconds - spent
        if entry.reason == "tool-missing":
            checks.append(_skipped(entry, "not_applicable", entry.detail))
            continue
        if len(checks) >= policy.budget.max_checks:
            checks.append(_skipped(entry, "skipped", "budget: check count exhausted"))
            continue
        if remaining < MIN_CHECK_SECONDS:
            checks.append(_skipped(entry, "skipped", "budget: no time left"))
            continue

        check_started = time.monotonic()
        checks.append(
            await run_check(
                entry.check,
                root,
                artifacts,
                timeout_s=min(entry.check.timeout_s, remaining),
            )
        )
        spent += time.monotonic() - check_started

    run = VerificationRun(
        run_id=run_id,
        policy_name=policy.name,
        policy_digest=policy.digest(),
        profile_digest=profile.digest() if profile is not None else None,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        checks=checks,
        planned=len(plan),
        spent_seconds=round(spent, 3),
    )
    write_run(run, target)
    return run, target


async def run_check(
    spec: CommandSpec,
    root: Path,
    artifacts: Path,
    *,
    timeout_s: float | None = None,
) -> CheckRun:
    """Run one command and describe the outcome without judging it."""
    path_error = _path_error(root, spec.argv[0])
    if path_error is not None:
        return CheckRun(
            check_id=spec.id,
            name=spec.name or spec.id,
            status="error",
            optional=spec.optional,
            reason=path_error,
        )

    env = _environment(spec)
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *spec.argv,
            cwd=str(root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return CheckRun(
            check_id=spec.id,
            name=spec.name or spec.id,
            status="error",
            optional=spec.optional,
            reason=f"command not found: {spec.argv[0]}",
        )
    except OSError as exc:
        return CheckRun(
            check_id=spec.id,
            name=spec.name or spec.id,
            status="error",
            optional=spec.optional,
            reason=f"could not start: {exc}",
        )

    limit = timeout_s if timeout_s is not None else spec.timeout_s
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=limit)
    except TimeoutError:
        timed_out = True
        _terminate(process)
        stdout, stderr = await _drain(process)

    duration_ms = round((time.monotonic() - started) * 1000, 1)
    out_text = _decode(stdout)
    err_text = _decode(stderr)
    stdout_path = _write_artifact(artifacts, spec.id, "stdout", out_text)
    stderr_path = _write_artifact(artifacts, spec.id, "stderr", err_text)
    combined = (out_text + err_text).strip()
    tail = combined[-MAX_OUTPUT_TAIL:]

    status: CheckStatus
    reason: str | None
    exit_code: int | None
    if timed_out:
        status = "timeout"
        reason = f"exceeded {limit:g}s"
        exit_code = None
    else:
        exit_code = process.returncode
        status = "passed" if exit_code == spec.expect_exit else "failed"
        reason = None if status == "passed" else f"exit {exit_code}, expected {spec.expect_exit}"

    return CheckRun(
        check_id=spec.id,
        name=spec.name or spec.id,
        status=status,
        optional=spec.optional,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        output_tail=tail,
        reason=reason,
    )


def write_run(run: VerificationRun, out_dir: Path) -> Path:
    """Persist the record next to its artifacts; a run without a file did not happen."""
    target = Path(out_dir) / "run.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target


def _environment(spec: CommandSpec) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_WHITELIST if name in os.environ}
    env.update(spec.env)
    return env


def _path_error(root: Path, program: str) -> str | None:
    """Reject a *relative* program that escapes the project; leave explicit paths alone.

    A bare name is resolved on PATH by the OS. A path written by the policy author is
    honoured as written — pointing at ``/usr/bin/pytest`` is a legitimate choice. What is
    refused is the ambiguous case: a path relative to the project that climbs out of it.
    """
    if program.startswith("/") or (os.sep not in program and "/" not in program):
        return None
    target = (root / program).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return f"refusing to run {program}: outside the project"
    if not target.exists():
        return f"command not found: {program}"
    return None


def _terminate(process: asyncio.subprocess.Process) -> None:
    """Kill the whole group: a check that spawned children must not outlive its timeout."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:  # pragma: no cover - windows
            process.kill()
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            process.kill()


async def _drain(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=5.0)
    except (TimeoutError, ValueError):
        return b"", b""


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_CAPTURE:
        return text[:MAX_CAPTURE] + "\n...[truncated]"
    return text


def _write_artifact(artifacts: Path, check_id: str, stream: str, text: str) -> str | None:
    if not text:
        return None
    artifacts.mkdir(parents=True, exist_ok=True)
    name = f"{check_id}.{stream}.txt"
    (artifacts / name).write_text(text, encoding="utf-8")
    return f"{ARTIFACT_DIR}/{name}"


def _skipped(entry: PlanEntry, status: CheckStatus, reason: str) -> CheckRun:
    return CheckRun(
        check_id=entry.check.id,
        name=entry.check.name or entry.check.id,
        status=status,
        optional=entry.check.optional,
        reason=reason,
    )
