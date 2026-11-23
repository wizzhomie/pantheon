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
import re
import signal
import subprocess
import time
import tomllib
from collections.abc import Callable, Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..context import ProjectProfile
from .contracts import (
    MAX_OUTPUT_TAIL,
    CheckRun,
    CheckStatus,
    CommandSpec,
    PlanEntry,
    Sandbox,
    SandboxRecord,
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

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
"""A run id ends up in file names and in reports, so it may not be a path or a sentence."""

UNSUPPORTED_SANDBOX = (
    "sandbox: limits are declared but cannot be applied on this platform; "
    "the check did not run without the ceiling it asked for"
)
"""The one honest answer when the policy asks for a limit the OS cannot give."""


def read_policy(path: Path) -> VerificationPolicy:
    """Load a policy from TOML. Unknown keys are an error, not a silent default."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return VerificationPolicy.model_validate(data)


def build_plan(
    policy: VerificationPolicy,
    profile: ProjectProfile | None = None,
    *,
    changed: Collection[str] | None = None,
) -> list[PlanEntry]:
    """What the policy asks for, what the project can answer, and what this change needs.

    A check that requires a tool the profile did not find is *not applicable*, not a failure:
    reporting a missing tool as a broken project would punish repositories that simply do not
    have that linter configured.

    ``changed`` is the set of paths the change touches, or ``None`` when there is no change to
    reason about. ``None`` plans every check: an unknown change is assumed to affect everything,
    because a cheap plan that skips a gate is not cheap. With a change in hand, a check that
    declares ``when_paths`` and matches none of them is *not selected* — a decision the report
    explains, not a gap in the evidence.
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
        if changed is not None and check.when_paths and not selects(check.when_paths, changed):
            entries.append(
                PlanEntry(
                    check=check,
                    reason="not-selected",
                    detail="no changed path matches " + ", ".join(check.when_paths),
                )
            )
            continue
        entries.append(
            PlanEntry(check=check, reason="optional" if check.optional else "required")
        )
    return entries


def selects(patterns: Collection[str], changed: Collection[str]) -> bool:
    """Does any changed path fall under one of the check's selectors?

    A selector with a slash is matched against the project-relative path (``db/**``); one
    without a slash is matched against the file name anywhere (``*.sql``). ``*`` and ``?``
    stay inside one path segment, ``**`` crosses segments — a linter that silently matched
    across directories would select a check nobody asked for.
    """
    for path in changed:
        normalised = path.replace("\\", "/")
        for pattern in patterns:
            if _glob(pattern).match(normalised):
                return True
            if "/" not in pattern and _glob(pattern).match(normalised.rsplit("/", 1)[-1]):
                return True
    return False


def _glob(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*" and pattern[index : index + 2] == "**":
            parts.append(".*")
            index += 2
            continue
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(parts) + "$")


def describe_plan(plan: list[PlanEntry]) -> list[str]:
    """Human lines for `trajan run --dry-run`, in execution order."""
    lines: list[str] = []
    for entry in plan:
        marker = {"required": "required", "optional": "optional"}.get(entry.reason, entry.reason)
        command = " ".join(entry.check.argv)
        detail = f" — {entry.detail}" if entry.detail else ""
        purpose = f"  ({entry.check.purpose})" if entry.check.purpose else ""
        lines.append(f"{entry.check.id}: {command} [{marker}]{detail}{purpose}")
    return lines


def describe_sandbox(sandbox: Sandbox) -> str | None:
    """What a run would limit, said together with what it would not isolate.

    The second half is not a disclaimer bolted on: a resource ceiling and an isolation
    boundary are different promises, and a reader who is told only about the ceiling will
    assume the boundary.
    """
    if not sandbox.enabled:
        return None
    parts: list[str] = []
    if sandbox.memory_mb:
        parts.append(f"{sandbox.memory_mb} MB memory")
    if sandbox.file_mb:
        parts.append(f"{sandbox.file_mb} MB per file")
    return ", ".join(parts) + "; network and file system are not isolated"


def describe_limits(record: SandboxRecord) -> str:
    """The applied limits as a short phrase, for a header row or one screen of text."""
    parts: list[str] = []
    if record.memory_mb:
        parts.append(f"{record.memory_mb} MB memory")
    if record.file_mb:
        parts.append(f"{record.file_mb} MB per file")
    return ", ".join(parts) if parts else "none"


def _sandbox_applicable(platform: str | None = None) -> bool:
    """Can this platform cap a child's address space and file size at all?

    ``platform`` exists so the unsupported answer can be tested where the limits do exist;
    the caller never passes it.
    """
    if (os.name if platform is None else platform) != "posix":
        return False
    try:
        import resource
    except ImportError:  # pragma: no cover - posix without resource is exotic
        return False
    return hasattr(resource, "RLIMIT_AS") and hasattr(resource, "RLIMIT_FSIZE")


def _limits(sandbox: Sandbox | None) -> Callable[[], None] | None:
    """The limits to apply in the child, between fork and exec.

    Both the soft and the hard limit are set to the same value: a check that could raise its
    own hard limit would be reporting a ceiling it does not have.

    ``preexec_fn`` is the only hook asyncio gives us here (``posix_spawn`` is not exposed),
    and Python warns that it is unsafe in a threaded process: the child inherits whatever
    locks were held at fork time. The callable therefore does no more than three ``setrlimit``
    calls on a module imported before the fork — no allocation, no import, no locks. trajan
    itself spawns checks from one event loop, and a host that embeds it must keep to that.
    """
    if sandbox is None or not sandbox.enabled or not _sandbox_applicable():
        return None
    import resource

    address_space = sandbox.memory_mb * 1024 * 1024
    file_size = sandbox.file_mb * 1024 * 1024

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if address_space:
            resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
        if file_size:
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_size, file_size))

    return apply


def _sandbox_record(sandbox: Sandbox) -> SandboxRecord | None:
    """What the run will claim about limits; ``None`` when the policy declares none."""
    if not sandbox.enabled:
        return None
    backend: Literal["rlimit", "unsupported"] = (
        "rlimit" if _sandbox_applicable() else "unsupported"
    )
    return SandboxRecord(backend=backend, memory_mb=sandbox.memory_mb, file_mb=sandbox.file_mb)


async def run_policy(
    policy: VerificationPolicy,
    root: Path,
    *,
    out_dir: Path | None = None,
    profile: ProjectProfile | None = None,
    adopted: Sequence[str] = (),
    changed: Collection[str] | None = None,
) -> tuple[VerificationRun, Path]:
    """Execute the plan, write artifacts, return the record.

    The budget is enforced before each check, not after: a check that cannot finish inside
    what is left is skipped with a reason, so the report can say "not checked, no time".
    """
    root = Path(root).resolve()
    started_at = datetime.now(UTC)
    base = f"{started_at:%Y%m%dT%H%M%S}-{policy.name}"
    run_id, target = _run_target(root, base, out_dir)
    artifacts = target / ARTIFACT_DIR
    plan = build_plan(policy, profile, changed=changed)

    checks: list[CheckRun] = []
    spent = 0.0
    sandbox_error = None
    if policy.sandbox.enabled and not _sandbox_applicable():
        sandbox_error = UNSUPPORTED_SANDBOX
    for entry in plan:
        remaining = policy.budget.max_seconds - spent
        if entry.reason == "tool-missing":
            checks.append(_skipped(entry, "not_applicable", entry.detail))
            continue
        if entry.reason == "not-selected":
            checks.append(_skipped(entry, "skipped", entry.detail, selected=False))
            continue
        if len(checks) >= policy.budget.max_checks:
            checks.append(_skipped(entry, "skipped", "budget: check count exhausted"))
            continue
        if remaining < MIN_CHECK_SECONDS:
            checks.append(_skipped(entry, "skipped", "budget: no time left"))
            continue
        # After the budget, so the errors count exactly the checks that would have run: a
        # check the budget already excluded did not fail because of the sandbox.
        if sandbox_error is not None:
            checks.append(_skipped(entry, "error", sandbox_error))
            continue

        check_started = time.monotonic()
        checks.append(
            await run_check(
                entry.check,
                root,
                artifacts,
                timeout_s=min(entry.check.timeout_s, remaining),
                sandbox=policy.sandbox,
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
        adopted=list(adopted),
        sandbox=_sandbox_record(policy.sandbox),
    )
    write_run(run, target)
    return run, target


async def run_check(
    spec: CommandSpec,
    root: Path,
    artifacts: Path,
    *,
    timeout_s: float | None = None,
    sandbox: Sandbox | None = None,
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
            preexec_fn=_limits(sandbox),
        )
    except FileNotFoundError:
        return CheckRun(
            check_id=spec.id,
            name=spec.name or spec.id,
            status="error",
            optional=spec.optional,
            reason=f"command not found: {spec.argv[0]}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # A limit the kernel refuses (or a failing preexec) is "could not run", not a failure
        # of the project: the command never got the chance to say anything.
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
        reason = (
            None
            if status == "passed"
            else _exit_reason(exit_code, spec, sandbox, err_text)
        )
        if status == "failed":
            missing = _missing_module(spec.argv, err_text)
            if missing is not None:
                # `python -m mypy` exits 1 when mypy is not installed. That is a tool the
                # machine lacks, not a project whose types are broken: calling it a failure
                # makes the report point at a type error that does not exist.
                status = "not_applicable"
                reason = f"the {missing} module is not installed, so the check could not run"

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


_MODULE_MISSING = re.compile(r"No module named ['\"]?(?P<name>[^\s'\"]+)['\"]?")


def _missing_module(argv: list[str], stderr: str) -> str | None:
    """The module a ``python -m`` check could not import, when that is why it failed.

    Only the module named in ``argv`` counts: a *dependency* that is missing inside the
    project's own tests is a real failure and stays one, while ``python -m mypy`` saying
    "No module named mypy" is a tool this machine does not have.
    """
    if len(argv) < 3 or argv[1] != "-m" or "python" not in Path(argv[0]).name:
        return None
    module = argv[2]
    for match in _MODULE_MISSING.finditer(stderr):
        if match.group("name") == module:
            return module
    return None


def _exit_reason(
    exit_code: int | None,
    spec: CommandSpec,
    sandbox: Sandbox | None,
    stderr: str,
) -> str:
    """Name the failure the OS actually reported; ``exit -25`` is not a sentence.

    A check killed by ``RLIMIT_FSIZE`` dies on ``SIGXFSZ``; Python children ignore that
    signal, so their write fails with ``EFBIG`` instead and they exit with an ordinary code.
    Both end the check the same way, and both should say which ceiling they hit rather than
    send the reader looking for a bug in the check.
    """
    if sandbox is not None and sandbox.file_mb:
        if (
            exit_code is not None
            and hasattr(signal, "SIGXFSZ")
            and exit_code == -signal.SIGXFSZ
        ):
            return f"exceeded the {sandbox.file_mb} MB file size limit (SIGXFSZ)"
        if "File too large" in stderr or "EFBIG" in stderr:
            return f"exceeded the {sandbox.file_mb} MB file size limit (EFBIG)"
    return f"exit {exit_code}, expected {spec.expect_exit}"


def _run_target(root: Path, base: str, out_dir: Path | None) -> tuple[str, Path]:
    """The run's id and directory, chosen so that two runs never share an id by accident.

    An explicit ``--out`` names the run: ``--out reports/demo`` produces the run ``demo``,
    which is what a reader needs when the directory is committed. Without one, the id is the
    start time — and if a run already claimed that second, the next one is ``-2`` rather than
    silently overwriting it, because a run that is overwritten did not happen.
    """
    if out_dir is not None:
        target = Path(out_dir)
        if RUN_ID_PATTERN.match(target.name):
            return target.name, target
        return base, target
    attempt = 1
    while True:
        run_id = base if attempt == 1 else f"{base}-{attempt}"
        target = root / ".trajan" / "runs" / run_id
        if not target.exists():
            return run_id, target
        attempt += 1


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


def _skipped(
    entry: PlanEntry, status: CheckStatus, reason: str, *, selected: bool = True
) -> CheckRun:
    return CheckRun(
        check_id=entry.check.id,
        name=entry.check.name or entry.check.id,
        status=status,
        optional=entry.check.optional,
        selected=selected,
        reason=reason,
    )
