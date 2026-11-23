"""Limits around a check — and the claim the report is allowed to make about them.

The feature is small; the honesty it owes the reader is not. A limit is not isolation, a
platform that cannot apply a limit must say so instead of running unguarded, and a check
killed by the file-size ceiling has to be named as that, not as `exit -25`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from trajan.orchestrator import plan_lines
from trajan.verify import (
    Budget,
    CommandSpec,
    Sandbox,
    VerificationPolicy,
    VerificationRequest,
    VerificationRun,
    build_report,
    decide,
    describe_sandbox,
    engine,
    render_markdown,
    run_policy,
)

try:  # pragma: no cover - exercised on the platform that has them
    import resource

    HAS_RLIMIT = hasattr(resource, "RLIMIT_AS") and hasattr(resource, "RLIMIT_FSIZE")
except ImportError:  # pragma: no cover - windows
    HAS_RLIMIT = False

posix_limits = pytest.mark.skipif(not HAS_RLIMIT, reason="POSIX resource limits are absent")


def code(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def spec(check_id: str = "check", argv: list[str] | None = None, **kwargs: object) -> CommandSpec:
    return CommandSpec(id=check_id, argv=argv or code("print('ok')"), **kwargs)  # type: ignore[arg-type]


def policy(*checks: CommandSpec, sandbox: Sandbox | None = None) -> VerificationPolicy:
    return VerificationPolicy(
        name="test",
        checks=list(checks),
        budget=Budget(),
        sandbox=sandbox or Sandbox(),
    )


def run(spec_: CommandSpec, root: Path, sandbox: Sandbox | None = None):
    sandbox_ = sandbox or Sandbox()
    return asyncio.run(
        run_policy(policy(spec_, sandbox=sandbox_), root, out_dir=root / "run")
    )


def report_markdown(run_: VerificationRun) -> str:
    return render_markdown(build_report(run_, [], decide(run_, [])))


def test_no_limits_is_the_default_and_no_claim_is_made(tmp_path: Path) -> None:
    run_, _ = run(spec(argv=code("print('ok')")), tmp_path)

    assert policy().sandbox.enabled is False
    assert run_.sandbox is None
    assert describe_sandbox(Sandbox()) is None
    assert "## Sandbox" not in report_markdown(run_)


def test_a_limit_is_a_non_negative_number() -> None:
    assert Sandbox(memory_mb=0, file_mb=0).enabled is False
    assert Sandbox(memory_mb=2048).enabled is True
    for bad in (-1, -1024):
        with pytest.raises(ValidationError):
            Sandbox(memory_mb=bad)


def test_the_digest_covers_the_limits() -> None:
    plain = VerificationPolicy(name="test", checks=[spec()])
    limited = VerificationPolicy(name="test", checks=[spec()], sandbox=Sandbox(memory_mb=512))

    assert plain.digest() != limited.digest()


def test_the_description_does_not_let_a_limit_pose_as_isolation() -> None:
    described = describe_sandbox(Sandbox(memory_mb=2048, file_mb=64))

    assert described is not None
    assert "2048 MB memory" in described
    assert "64 MB per file" in described
    assert "not isolated" in described


@posix_limits
def test_the_memory_limit_reaches_the_child(tmp_path: Path) -> None:
    # The child passes only if it can see the ceiling the policy declared.
    source = (
        "import resource, sys\n"
        "soft, hard = resource.getrlimit(resource.RLIMIT_AS)\n"
        "sys.exit(0 if soft == 512 * 1024 * 1024 and hard == soft else 1)\n"
    )
    run_, _ = run(spec(argv=code(source)), tmp_path, Sandbox(memory_mb=512))

    assert run_.checks[0].status == "passed"


@posix_limits
def test_a_check_over_the_memory_limit_fails(tmp_path: Path) -> None:
    run_, _ = run(
        spec(argv=code("buffer = bytearray(3 * 1024**3)\nprint(len(buffer))")),
        tmp_path,
        Sandbox(memory_mb=512),
    )

    check = run_.checks[0]
    assert check.status == "failed"
    assert check.exit_code not in (None, 0)


@posix_limits
def test_the_file_limit_is_named_not_just_a_number(tmp_path: Path) -> None:
    source = "open('big.bin', 'wb').write(b'x' * (4 * 1024 * 1024))\n"
    run_, _ = run(spec(argv=code(source)), tmp_path, Sandbox(file_mb=1))

    check = run_.checks[0]
    assert check.status == "failed"
    assert check.reason is not None and "file size limit" in check.reason
    assert check.reason is not None and "1 MB" in check.reason


@posix_limits
def test_the_run_records_what_it_applied(tmp_path: Path) -> None:
    run_, _ = run(
        spec(argv=code("print('ok')")), tmp_path, Sandbox(memory_mb=2048, file_mb=64)
    )

    assert run_.sandbox is not None
    assert run_.sandbox.backend == "rlimit"
    assert (run_.sandbox.memory_mb, run_.sandbox.file_mb) == (2048, 64)
    markdown = report_markdown(run_)
    assert "| Sandbox | 2048 MB memory, 64 MB per file |" in markdown
    assert "not isolation" in markdown
    assert "cannot claim" not in markdown


@posix_limits
def test_a_platform_without_limits_refuses_to_run_unguarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "_sandbox_applicable", lambda: False)
    run_, _ = run(spec(argv=code("print('ok')")), tmp_path, Sandbox(memory_mb=2048))

    assert run_.sandbox is not None
    assert run_.sandbox.backend == "unsupported"
    check = run_.checks[0]
    assert check.status == "error"
    assert check.reason is not None and "cannot be applied" in check.reason
    assert decide(run_, []).status == "inconclusive"
    markdown = report_markdown(run_)
    assert "cannot apply them" in markdown


def test_another_platform_is_recognised_as_unsupported() -> None:
    # The branch cannot run here, so the detection is asked about a platform by name.
    assert engine._sandbox_applicable("nt") is False
    assert engine._sandbox_applicable("posix") is True


@posix_limits
def test_limits_the_kernel_refuses_are_could_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> None:
        raise OSError("limits refused")

    monkeypatch.setattr(engine, "_limits", lambda sandbox: refuse)
    run_, _ = run(spec(argv=code("print('never runs')")), tmp_path, Sandbox(memory_mb=512))

    check = run_.checks[0]
    assert check.status == "error"
    assert check.reason is not None and "could not start" in check.reason


def test_the_plan_says_what_would_be_limited(tmp_path: Path) -> None:
    (tmp_path / ".trajan").mkdir()
    (tmp_path / ".trajan" / "policy.toml").write_text(
        'name = "test"\n\n[sandbox]\nmemory_mb = 512\n\n'
        '[[checks]]\nid = "check"\nargv = ["python", "-c", "print(1)"]\n',
        encoding="utf-8",
    )
    request = VerificationRequest(policy=".trajan/policy.toml")

    lines = plan_lines(request, tmp_path)

    assert any("sandbox: 512 MB memory" in line for line in lines)
    assert any("not isolated" in line for line in lines)
