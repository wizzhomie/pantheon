"""Resume: a task that stopped before its verdict is continued, not rebuilt by hand.

The cases are the three ways a dispatch can stop: the executor never answered, the gate
applied the change and the run died before verification, and the call cost more than the
budget allowed. In every case the record must say where the continuation started, the tree
must be the tree the gate changed, and the model must not be asked again for a change that is
already on disk.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import augustus, contour
from harness import box as boxes
from harness.augustus import SNAPSHOT_FILE
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

TASK = """\
schema_version = 1
id = "t-1"
goal = "change the app value"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[budget]
max_cents = 2
max_seconds = 60
"""

PATCH = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path, name: str = "probe") -> Path:
    project = tmp_path / name
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path, project: Path | None = None) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project or make_project(tmp_path))


def with_change(patch: Path, text: str = TASK) -> str:
    """A task that carries its own patch: ``change`` belongs before the ``[budget]`` table."""
    body = text.split("\n[budget]")[0]
    budget = "\n[budget]" + text.split("\n[budget]")[1]
    return body + f'change = "{patch}"\n' + budget


def write_task(tmp_path: Path, text: str = TASK, name: str = "t-1.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def make_patch(tmp_path: Path, project: Path) -> Path:
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def write_executor(tmp_path: Path, cost: str = "0.5", name: str = "fake-executor.py") -> Path:
    script = tmp_path / name
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        'with open(os.environ["FAKE_EXECUTOR_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write("called\\n")\n'
        "print(json.dumps({\n"
        f'    "patch": {json.dumps(PATCH)},\n'
        '    "model": "fake-model",\n'
        f'    "usage": {{"prompt_tokens": 100, "completion_tokens": 20, "cost_cents": {cost}}},\n'
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_killed_executor(tmp_path: Path) -> Path:
    script = tmp_path / "killed-executor.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        'print("the executor was killed", file=sys.stderr)\n'
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_profile(box: boxes.Box, command: Path, extra: str = "") -> None:
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 60\n{extra}',
        encoding="utf-8",
    )


def dispatch(box: boxes.Box, task: Path, *extra: str) -> int:
    return main(
        ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task), *extra]
    )


def resume(box: boxes.Box, *extra: str) -> int:
    return main(["--castra", str(box.root.parent), "resume", box.name, "--task", "t-1", *extra])


def calls(tmp_path: Path) -> int:
    log = tmp_path / "executor.log"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


def records(box: boxes.Box) -> list[Path]:
    return sorted((box.root / "tabularium" / "t-1").glob("dispatch-*.json"))


def tree(project: Path) -> dict[str, str]:
    return {
        str(path.relative_to(project)): path.read_text(encoding="utf-8")
        for path in project.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".trajan" not in path.parts
    }


def trajan_runnable() -> bool:
    try:
        root = contour.trajan_root()
    except boxes.HarnessError:
        return False
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    done = subprocess.run(
        [str(contour.trajan_python(root)), "-m", "trajan", "--help"],
        capture_output=True,
        check=False,
        env=env,
    )
    return done.returncode == 0


requires_trajan = pytest.mark.skipif(not trajan_runnable(), reason="trajan cannot run here")


@pytest.fixture(autouse=True)
def executor_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EXECUTOR_LOG", str(tmp_path / "executor.log"))


@requires_trajan
def test_the_task_is_frozen_beside_its_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, with_change(make_patch(tmp_path, project)))
    assert dispatch(box, task) == 0
    capsys.readouterr()
    snapshot = box.root / "tabularium" / "t-1" / SNAPSHOT_FILE
    frozen = augustus.read_task(snapshot)
    assert frozen.id == "t-1" and frozen.goal == "change the app value"
    assert frozen.change is not None and frozen.change.is_file()
    assert frozen.change.parent == snapshot.parent
    # Editing or deleting the original task must not rewrite what already ran.
    task.unlink()
    assert augustus.read_task(snapshot).change == frozen.change


@requires_trajan
def test_a_run_that_died_after_the_gate_is_continued_by_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path))
    task = write_task(tmp_path, TASK)
    assert dispatch(box, task) == 0
    capsys.readouterr()
    # The crash: the tree holds the change, the gate's record exists, the dispatch record does
    # not, and the task file is gone — exactly what a kill between the two stages leaves.
    for record in records(box):
        record.unlink()
    task.unlink()
    assert resume(box) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out
    assert "resumed    from" in out and "verification is the step that did not run" in out
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    assert calls(tmp_path) == 1, "the model must not be asked again for a change already on disk"
    payload = json.loads(
        next((box.root / "tabularium" / "t-1").glob("dispatch-*.json")).read_text(encoding="utf-8")
    )
    assert payload["state"] == "closed" and payload["resumed_from"]


@requires_trajan
def test_a_killed_executor_is_asked_again_only_with_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_killed_executor(tmp_path))
    task = write_task(tmp_path, TASK)
    assert dispatch(box, task) == 1
    capsys.readouterr()
    assert resume(box) == 2
    error = capsys.readouterr().err
    assert "no usable change" in error and "--retry" in error
    good = write_executor(tmp_path)
    assert resume(box, "--retry", "--executor", str(good)) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out and "the change was asked for again" in out
    assert calls(tmp_path) == 1
    assert (project / "src" / "app.py").read_text() == "value = 2\n"


@requires_trajan
def test_a_change_over_budget_is_applied_only_when_the_limit_is_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path, cost="5"), extra="max_cents = 5\n")
    task = write_task(tmp_path, TASK)
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "over-budget" in out, "the task asks for 2 cents; the call said 5"
    assert (project / "src" / "app.py").read_text() == "value = 1\n"
    assert resume(box) == 2
    error = capsys.readouterr().err
    assert "above the 2 allowed" in error and "--max-cents 5" in error
    assert resume(box, "--max-cents", "5") == 0
    out = capsys.readouterr().out
    assert "state      closed" in out
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    assert calls(tmp_path) == 1, "the change was already paid for: raising the limit is free"


@requires_trajan
def test_verification_is_refused_when_the_tree_no_longer_holds_the_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, with_change(make_patch(tmp_path, project)))
    assert dispatch(box, task) == 0
    capsys.readouterr()
    for record in records(box):
        record.unlink()
    # The green change is a commit now, so dropping the working tree is not enough: the tree
    # has to lose the commit too, or the change is still where the gate put it.
    git(project, "reset", "-q", "--hard", "HEAD~1")
    assert resume(box) == 2
    error = capsys.readouterr().err
    assert "no longer in the tree" in error
    assert (project / "src" / "app.py").read_text() == "value = 1\n"


@requires_trajan
def test_a_closed_task_is_not_continued(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, with_change(make_patch(tmp_path, project)))
    assert dispatch(box, task) == 0
    capsys.readouterr()
    before = records(box)
    assert resume(box) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out and "nothing to continue" in out
    assert records(box) == before, "continuing a closed task must not write a new record"


@requires_trajan
def test_a_refused_change_is_not_continued_without_a_new_proposal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    (project / "README.md").write_text("new\n", encoding="utf-8")
    git(project, "add", "-A")
    outside = tmp_path / "outside.diff"
    outside.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    task = write_task(tmp_path, with_change(outside))
    assert dispatch(box, task) == 1
    capsys.readouterr()
    assert resume(box) == 2
    error = capsys.readouterr().err
    assert "the gate refused" in error and "--retry" in error


@requires_trajan
def test_three_tasks_interrupted_in_different_ways_reach_the_same_verdict(
    tmp_path: Path,
) -> None:
    """The stage's closure criterion: an interrupted run and a clean run agree on three tasks."""

    patch = make_patch(tmp_path, make_project(tmp_path, "patchbase"))
    declared = with_change(patch)
    raised = TASK.replace("max_cents = 2", "max_cents = 5")

    def prepare(name: str, text: str, script: Path | None) -> tuple[Path, boxes.Box, Path]:
        project = make_project(tmp_path, f"p-{name}")
        box = boxes.new_box(tmp_path / f"b-{name}", project)
        task = write_task(tmp_path, text, name=f"{name}.toml")
        if script is not None:
            write_profile(box, script)
        return project, box, task

    def verdict(project: Path, box: boxes.Box) -> tuple[str, str, dict[str, str]]:
        payload = json.loads(records(box)[-1].read_text(encoding="utf-8"))
        return str(payload["state"]), str(payload["verify"]["status"]), tree(project)

    def clean(name: str, text: str, script: Path | None) -> tuple[str, str, dict[str, str]]:
        project, box, task = prepare(f"clean-{name}", text, script)
        assert dispatch(box, task) == 0
        return verdict(project, box)

    # 1. The run died after the gate: the change is in the tree, verification did not run.
    project, box, task = prepare("gate", declared, None)
    assert dispatch(box, task) == 0
    for record in records(box):
        record.unlink()
    task.unlink()
    assert resume(box) == 0
    assert verdict(project, box) == clean("gate", declared, None)

    # 2. The call cost more than the budget: continued when the owner raises the limit.
    over = write_executor(tmp_path, cost="5", name="budget-executor.py")
    project, box, task = prepare("budget", TASK, over)
    assert dispatch(box, task) == 1
    assert resume(box, "--max-cents", "5") == 0
    got = verdict(project, box)
    assert got == clean("budget", raised, over)
    assert got[:2] == ("closed", "pass")

    # 3. The executor was killed: asked again, with its own budget unchanged.
    project, box, task = prepare("killed", TASK, write_killed_executor(tmp_path))
    assert dispatch(box, task) == 1
    good = write_executor(tmp_path, name="good-executor.py")
    assert resume(box, "--retry", "--executor", str(good)) == 0
    got = verdict(project, box)
    assert got == clean("killed", TASK, good)
    assert got[:2] == ("closed", "pass")
