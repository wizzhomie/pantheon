"""The check a repair task brings is checked before the model is paid for.

The gap: dispatch refuses a reproduction that already passes, but it cannot show that a
correct fix makes the check pass. A wrong criterion then spends a call and lands in the record
as a failed task. The preflight runs the same command on the base and on a reference commit,
and writes nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import box as boxes
from harness import preflight
from harness.cli import main

CHECKS_VALUE_TWO = (
    "import sys; sys.exit(0 if open('value.txt').read().strip() == '2' else 1)"
)
CHECKS_VALUE_THREE = (
    "import sys; sys.exit(0 if open('value.txt').read().strip() == '3' else 1)"
)
ALWAYS_PASSES = "raise SystemExit(0)"


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    """A repository with a defect on the base and the known fix on a branch."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "value.txt").write_text("1\n", encoding="utf-8")
    git(project, "init", "-q", "-b", "main")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    git(project, "checkout", "-q", "-b", "fix")
    (project / "value.txt").write_text("2\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fix")
    git(project, "checkout", "-q", "main")
    return project


def run(project: Path, command: str, *, fix: str | None = "fix") -> preflight.Preflight:
    task = SimpleNamespace(id="t-1", repro=(sys.executable, "-c", command))
    return preflight.check(
        project,
        task,
        fix=fix,
        root=Path(sys.prefix),
        python=Path(sys.executable),
    )


def test_a_check_that_fails_on_the_base_and_passes_on_the_fix_is_live(tmp_path: Path) -> None:
    result = run(make_project(tmp_path), CHECKS_VALUE_TWO)
    assert result.ok
    assert not result.base.passed
    assert result.fix is not None and result.fix.passed
    assert result.fix.where.startswith("the fix")
    lines = preflight.render(result)
    assert any(line.startswith("base") and "failed" in line for line in lines)
    assert any(line.startswith("fix") and "passed" in line for line in lines)
    assert "live check" in lines[-1]


def test_a_check_that_already_passes_on_the_base_is_refused(tmp_path: Path) -> None:
    result = run(make_project(tmp_path), ALWAYS_PASSES)
    assert not result.ok
    assert result.fix is None
    assert "does not describe a live defect" in str(result.refusal)


def test_a_check_that_does_not_pass_on_the_fix_is_refused(tmp_path: Path) -> None:
    result = run(make_project(tmp_path), CHECKS_VALUE_THREE)
    assert not result.ok
    assert result.base.passed is False
    assert result.fix is not None and not result.fix.passed
    assert "the criterion and the reference disagree" in str(result.refusal)


def test_a_task_without_a_reproduction_is_an_error(tmp_path: Path) -> None:
    task = SimpleNamespace(id="t-1", repro=())
    with pytest.raises(boxes.HarnessError, match="declares no reproduction"):
        preflight.check(
            make_project(tmp_path),
            task,
            fix=None,
            root=Path(sys.prefix),
            python=Path(sys.executable),
        )


def test_an_unknown_fix_ref_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(boxes.HarnessError, match="has no commit"):
        run(make_project(tmp_path), CHECKS_VALUE_TWO, fix="no-such-branch")


def test_the_preflight_leaves_the_project_and_its_worktrees_as_they_were(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = git(project, "status", "--porcelain")
    worktrees = git(project, "worktree", "list", "--porcelain")
    assert run(project, CHECKS_VALUE_TWO).ok
    assert git(project, "status", "--porcelain") == before
    assert git(project, "worktree", "list", "--porcelain") == worktrees


def test_the_payload_names_both_runs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = run(project, CHECKS_VALUE_TWO)
    data = preflight.payload(result)
    assert data["ok"] is True
    assert data["refusal"] is None
    assert data["base"]["passed"] is False  # type: ignore[index]
    assert data["fix"]["passed"] is True  # type: ignore[index]
    assert data["repro"][0] == sys.executable  # type: ignore[index]


REPRO = (
    'repro = ["python", "-c", '
    '\'import sys; sys.exit(0 if open("value.txt").read().strip() == "2" else 1)\']'
)

TASK = """\
schema_version = 1
id = "t-1"
goal = "fix the value"
kind = "bugfix"
paths = ["value.txt"]
expected = "the value is two"
{repro}

[budget]
max_cents = 3
max_seconds = 60
"""


def write_task(tmp_path: Path, repro: str = REPRO) -> Path:
    task = tmp_path / "t-1.toml"
    task.write_text(TASK.format(repro=repro), encoding="utf-8")
    return task


def test_the_cli_runs_the_preflight_and_prints_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    task = write_task(tmp_path)
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "preflight",
                "proj",
                "--task",
                str(task),
                "--fix",
                "fix",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["task"] == "t-1"
    assert data["base"]["passed"] is False
    assert data["fix"]["passed"] is True


def test_the_cli_returns_one_when_the_check_is_not_live(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    task = write_task(tmp_path, 'repro = ["python", "-c", "raise SystemExit(0)"]')
    assert main(["--castra", str(box.root.parent), "preflight", "proj", "--task", str(task)]) == 1
    out = capsys.readouterr().out
    assert "refused" in out
    assert "live defect" in out


def test_the_check_uses_the_projects_own_python(tmp_path: Path) -> None:
    """The reproduction resolves ``python`` from the project's venv, like every other check."""
    project = make_project(tmp_path)
    shim = project / ".venv" / "bin"
    shim.mkdir(parents=True)
    python = shim / "python"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    result = preflight.check(
        project,
        SimpleNamespace(id="t-1", repro=("python", "-c", "raise SystemExit(1)")),
        fix="fix",
        root=Path(sys.prefix),
        python=Path(sys.executable),
    )
    # The project's python says 0 even though the code would exit 1: the base reads as green,
    # which is exactly what the preflight refuses.
    assert result.base.passed
    assert "does not describe a live defect" in str(result.refusal)
