"""The deterministic engine: run a command, keep the evidence, never confuse the two.

The tests that matter here are about honesty, not features: a missing tool must not look like
a pass, a timeout must not look like a failure of the project, a command must not reach a
shell, and a check must not see the reviewer's secrets.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from trajan.cli import main
from trajan.context import ProfileTool, ProjectProfile
from trajan.verify import (
    Budget,
    CommandSpec,
    VerificationPolicy,
    VerificationRun,
    build_plan,
    describe_plan,
    read_policy,
    run_policy,
)


def code(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def spec(check_id: str = "check", argv: list[str] | None = None, **kwargs: object) -> CommandSpec:
    return CommandSpec(id=check_id, argv=argv or code("print('ok')"), **kwargs)  # type: ignore[arg-type]


def policy(*checks: CommandSpec, budget: Budget | None = None) -> VerificationPolicy:
    return VerificationPolicy(
        name="test",
        checks=list(checks),
        budget=budget or Budget(),
    )


async def run_one(spec_: CommandSpec, root: Path) -> tuple[VerificationRun, Path]:
    return await run_policy(policy(spec_), root, out_dir=root / "run")


def test_a_passing_check_is_recorded_with_its_evidence(tmp_path: Path) -> None:
    run, target = asyncio.run(run_one(spec(argv=code("print('all good')")), tmp_path))

    check = run.checks[0]
    assert check.status == "passed"
    assert check.exit_code == 0
    assert check.output_tail == "all good"
    assert check.stdout_path is not None
    assert (target / check.stdout_path).read_text(encoding="utf-8") == "all good\n"
    assert (target / "run.json").exists()


def test_a_failing_check_is_failed_not_error(tmp_path: Path) -> None:
    run, _ = asyncio.run(run_one(spec(argv=code("import sys; sys.exit(3)")), tmp_path))

    check = run.checks[0]
    assert check.status == "failed"
    assert check.exit_code == 3
    assert check.reason == "exit 3, expected 0"
    assert check.blocking


def test_a_check_can_expect_a_non_zero_exit_code(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_one(spec(argv=code("import sys; sys.exit(2)"), expect_exit=2), tmp_path)
    )

    assert run.checks[0].status == "passed"


def test_a_missing_program_is_an_error_not_a_failure(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_one(spec(argv=["trajan-definitely-not-installed-xyz"]), tmp_path)
    )

    check = run.checks[0]
    assert check.status == "error"
    assert check.reason is not None and "command not found" in check.reason
    assert check.blocking  # a check that could not run is not a pass


def test_a_module_that_is_not_installed_is_not_applicable(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_one(spec(argv=[sys.executable, "-m", "trajan-not-installed-xyz"]), tmp_path)
    )

    check = run.checks[0]
    assert check.status == "not_applicable"
    assert check.reason == (
        "the trajan-not-installed-xyz module is not installed, so the check could not run"
    )
    assert not check.blocking


def test_a_missing_dependency_inside_the_check_stays_a_failure(tmp_path: Path) -> None:
    """A tool that cannot import *its own* dependency is a broken project, not an absent tool."""
    (tmp_path / "faketool.py").write_text(
        "import sys\nsys.stderr.write(\"ModuleNotFoundError: No module named 'dep_xyz'\\n\")\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    run, _ = asyncio.run(
        run_one(
            spec(
                argv=[sys.executable, "-m", "faketool"],
                env={"PYTHONPATH": str(tmp_path)},
            ),
            tmp_path,
        )
    )

    check = run.checks[0]
    assert check.status == "failed"
    assert check.reason == "exit 1, expected 0"
    assert check.blocking


def test_a_timeout_is_its_own_status(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_one(spec(argv=code("import time; time.sleep(30)"), timeout_s=0.3), tmp_path)
    )

    check = run.checks[0]
    assert check.status == "timeout"
    assert check.exit_code is None
    assert check.duration_ms < 5_000  # killed, not waited out


def test_the_argument_list_is_never_given_to_a_shell(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_one(spec(argv=code("print('a; rm -rf / && echo pwned')")), tmp_path)
    )

    assert run.checks[0].status == "passed"
    assert "a; rm -rf / && echo pwned" in run.checks[0].output_tail


def test_a_shell_string_in_one_element_is_rejected_at_load_time() -> None:
    with pytest.raises(ValidationError, match="not a shell string"):
        CommandSpec(id="bad", argv=["pytest && rm -rf /"])


def test_the_child_does_not_inherit_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRAJAN_TEST_SECRET", "do-not-leak")

    run, _ = asyncio.run(
        run_one(
            spec(
                argv=code(
                    "import os; print(os.environ.get('TRAJAN_TEST_SECRET', 'absent'))"
                )
            ),
            tmp_path,
        )
    )

    assert run.checks[0].output_tail == "absent"


def test_a_check_may_add_its_own_environment(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_one(
            spec(
                argv=code("import os; print(os.environ.get('EXTRA', 'absent'))"),
                env={"EXTRA": "white-listed"},
            ),
            tmp_path,
        )
    )

    assert "white-listed" in run.checks[0].output_tail


def test_a_relative_program_outside_the_project_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.sh"
    outside.write_text("echo nope\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    run, _ = asyncio.run(run_one(spec(argv=["../outside.sh"]), repo))

    check = run.checks[0]
    assert check.status == "error"
    assert check.reason is not None and "outside the project" in check.reason


def test_the_check_count_budget_skips_the_rest(tmp_path: Path) -> None:
    run, _ = asyncio.run(
        run_policy(
            policy(spec("first"), spec("second"), budget=Budget(max_checks=1)),
            tmp_path,
            out_dir=tmp_path / "run",
        )
    )

    assert [check.status for check in run.checks] == ["passed", "skipped"]
    assert run.checks[1].reason == "budget: check count exhausted"


def test_the_time_budget_skips_without_running(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    run, _ = asyncio.run(
        run_policy(
            policy(
                spec(argv=code(f"open({str(marker)!r}, 'w').close()")),
                budget=Budget(max_seconds=0.0001),
            ),
            tmp_path,
            out_dir=tmp_path / "run",
        )
    )

    assert run.checks[0].status == "skipped"
    assert run.checks[0].reason == "budget: no time left"
    assert not marker.exists()


def test_a_check_whose_tool_is_missing_from_the_project_is_not_applicable(
    tmp_path: Path,
) -> None:
    profile = ProjectProfile(
        name="mini",
        tools=[ProfileTool(name="ruff", command=["ruff"], source="pyproject.toml")],
    )
    run, _ = asyncio.run(
        run_policy(
            policy(spec("lint", requires="ruff"), spec("types", requires="mypy")),
            tmp_path,
            out_dir=tmp_path / "run",
            profile=profile,
        )
    )

    statuses = {check.check_id: check.status for check in run.checks}
    assert statuses["lint"] == "passed"
    assert statuses["types"] == "not_applicable"
    assert run.checks[1].reason is not None and "mypy" in run.checks[1].reason


def test_the_plan_says_what_will_run_and_why() -> None:
    config = policy(
        spec("tests", purpose="a red suite is a red project"),
        spec("slow", optional=True),
    )
    profile = ProjectProfile(name="mini")

    entries = build_plan(config, profile)
    lines = describe_plan(entries)

    assert [entry.reason for entry in entries] == ["required", "optional"]
    assert "required" in lines[0] and "a red suite" in lines[0]


def test_long_output_is_truncated_and_flagged(tmp_path: Path) -> None:
    run, target = asyncio.run(
        run_one(spec(argv=code("print('x' * 300000)"), timeout_s=30), tmp_path)
    )

    artifact = target / str(run.checks[0].stdout_path)
    saved = artifact.read_text(encoding="utf-8")
    assert saved.endswith("...[truncated]")
    assert len(saved) < 300_000


def test_the_run_record_round_trips(tmp_path: Path) -> None:
    run, target = asyncio.run(run_one(spec(), tmp_path))

    stored = VerificationRun.model_validate_json((target / "run.json").read_text(encoding="utf-8"))

    assert stored == run


def test_a_policy_from_toml_is_strict_and_stable(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(
        'name = "mini"\n\n[budget]\nmax_checks = 3\n\n'
        '[[checks]]\nid = "tests"\nargv = ["pytest", "-q"]\ntimeout_s = 5\n',
        encoding="utf-8",
    )

    loaded = read_policy(path)

    assert loaded.checks[0].argv == ["pytest", "-q"]
    assert loaded.digest() == read_policy(path).digest()
    assert loaded.budget.max_checks == 3


def test_duplicate_check_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        policy(spec("same"), spec("same"))


def test_the_cli_runs_a_policy_and_reports_blocking_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path = tmp_path / "policy.toml"
    path.write_text(
        f'name = "cli"\n\n[[checks]]\nid = "fine"\n'
        f"argv = {json.dumps([sys.executable, '-c', 'print(1)'])}\n",
        encoding="utf-8",
    )

    assert (
        main(["run", "--repo", str(repo), "--policy", str(path), "--out", str(tmp_path / "r")])
        == 0
    )
    assert "passed" in capsys.readouterr().out
    assert json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))["checks"][0][
        "status"
    ] == "passed"


def test_the_cli_exits_one_when_a_mandatory_check_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path = tmp_path / "policy.toml"
    path.write_text(
        f'name = "cli"\n\n[[checks]]\nid = "red"\n'
        f"argv = {json.dumps([sys.executable, '-c', 'raise SystemExit(1)'])}\n",
        encoding="utf-8",
    )

    assert (
        main(["run", "--repo", str(repo), "--policy", str(path), "--out", str(tmp_path / "r")])
        == 1
    )


def test_the_cli_dry_run_prints_the_plan_and_runs_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    path = tmp_path / "policy.toml"
    path.write_text(
        'name = "dry"\n\n[[checks]]\nid = "noop"\nargv = ["echo", "hi"]\n', encoding="utf-8"
    )

    assert main(["run", "--repo", str(repo), "--policy", str(path), "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "noop: echo hi [required]" in out
    assert not (repo / ".trajan").exists()
