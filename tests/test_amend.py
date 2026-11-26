"""Amend and park: a widened task is a successor with a parent, a reason and a recorded diff.

The pilot's gap: "I added a path and raised the budget" existed only as a new task with no
link, so the record could not say what changed or why. These tests hold the other contract —
the old task keeps its record, the new one names it, and an empty successor is refused.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import augustus, contour
from harness import box as boxes
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

[contract]
symptom = "the value is one"
visible = "src/app.py says value = 2"
boundaries = "src/**"
missing = ""
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_patch(tmp_path: Path, project: Path, content: str) -> Path:
    (project / "src" / "app.py").write_text(content, encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    return project, boxes.new_box(tmp_path / "castra", project)


def write_task(tmp_path: Path, text: str = TASK) -> Path:
    path = tmp_path / "t-1.toml"
    path.write_text(text, encoding="utf-8")
    return path


def freeze(box: boxes.Box, text: str = TASK) -> augustus.Task:
    """Freeze a task without dispatching it: amend reads the snapshot, not the file."""
    task = augustus.read_task(write_task_from(box, text))
    directory = box.root / "tabularium" / task.id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / augustus.SNAPSHOT_FILE).write_text(augustus.task_text(task), encoding="utf-8")
    return task


def write_task_from(box: boxes.Box, text: str) -> Path:
    path = box.root / "mandata" / "t-1.toml"
    path.write_text(text, encoding="utf-8")
    return path


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


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


def test_amend_writes_a_successor_with_parent_reason_and_diff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    freeze(box)
    assert (
        run(
            box,
            "amend",
            "probe",
            "--task",
            "t-1",
            "--why",
            "the fix also needs the helper module",
            "--add-path",
            "src/lib/**",
            "--budget",
            "max_cents=8",
            "--max-steps",
            "3",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "successor  t-1-b" in out and "the fix also needs the helper module" in out
    child = augustus.read_task(box.root / "tabularium" / "t-1-b" / augustus.SNAPSHOT_FILE)
    assert child.parent == "t-1" and child.why == "the fix also needs the helper module"
    assert child.paths == ("src/**", "src/lib/**")
    assert child.budget["max_cents"] == 8 and child.max_steps == 3
    assert (box.root / "mandata" / "t-1-b.toml").is_file()
    amendment = json.loads(
        (box.root / "tabularium" / "t-1-b" / augustus.AMENDMENT_FILE).read_text()
    )
    fields = [change["field"] for change in amendment["changes"]]
    assert fields == ["paths", "budget.max_cents", "max_steps"]
    assert amendment["changes"][0] == {
        "field": "paths",
        "before": ["src/**"],
        "after": ["src/**", "src/lib/**"],
    }
    chain = json.loads(
        (box.root / "tabularium" / "t-1" / augustus.AMENDMENTS_FILE).read_text()
    )
    assert chain["children"][0]["id"] == "t-1-b"
    events = (box.root / "fasti" / sorted((box.root / "fasti").iterdir())[0].name).read_text()
    assert "task.amended" in events and "replaced by t-1-b" in events
    # The status shows both tasks and the link, before either has run.
    capsys.readouterr()
    assert run(box, "status", "probe") == 0
    status = capsys.readouterr().out
    assert "t-1  not run" in status and "replaced by t-1-b" in status
    assert "t-1-b  not run" in status and "amends t-1" in status


def test_amend_refuses_an_empty_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    freeze(box)
    assert (
        run(box, "amend", "probe", "--task", "t-1", "--why", "no real change") == 2
    )
    assert "nothing changes" in capsys.readouterr().err
    assert not (box.root / "tabularium" / "t-1-b").exists()


def test_a_parent_chain_names_the_immediate_parent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    freeze(box)
    assert (
        run(box, "amend", "probe", "--task", "t-1", "--why", "first", "--budget", "max_cents=8")
        == 0
    )
    assert (
        run(
            box,
            "amend",
            "probe",
            "--task",
            "t-1-b",
            "--why",
            "second",
            "--max-steps",
            "2",
        )
        == 0
    )
    child = augustus.read_task(box.root / "tabularium" / "t-1-b" / augustus.SNAPSHOT_FILE)
    grandchild = augustus.read_task(
        box.root / "tabularium" / "t-1-b-b" / augustus.SNAPSHOT_FILE
    )
    assert child.parent == "t-1" and grandchild.parent == "t-1-b"
    assert grandchild.why == "second" and grandchild.max_steps == 2
    capsys.readouterr()
    assert run(box, "status", "probe") == 0
    status = capsys.readouterr().out
    assert "t-1-b  not run" in status and "amends t-1" in status
    assert "t-1-b-b  not run" in status and "amends t-1-b" in status


def test_park_without_a_task_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    assert run(box, "park", "probe", "--task", "t-404", "--why", "later") == 2
    assert "no task to park" in capsys.readouterr().err


def test_a_parked_task_does_not_run_and_status_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    freeze(box)
    assert (
        run(box, "park", "probe", "--task", "t-1", "--why", "the owner moved it out of the sprint")
        == 0
    )
    capsys.readouterr()
    # A resume reads the frozen task; the park record refuses before anything is paid for.
    assert run(box, "resume", "probe", "--task", "t-1") == 1
    out = capsys.readouterr().out
    assert "the task is parked" in out and "the owner moved it out of the sprint" in out
    assert run(box, "status", "probe") == 0
    status = capsys.readouterr().out
    assert "parked" in status and "the owner moved it out of the sprint" in status


@requires_trajan
def test_amend_a_closed_task_and_the_new_acta_names_the_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project, "value = 2\n")
    parent_task = write_task_from(
        box, TASK.replace("[budget]", f'change = "{patch}"\n\n[budget]')
    )
    assert run(box, "dispatch", "probe", "--task", str(parent_task)) == 0
    parent_acta = next((box.root / "acta").glob("*-t-1.md"))
    capsys.readouterr()
    assert (
        run(
            box,
            "amend",
            "probe",
            "--task",
            "t-1",
            "--why",
            "the helper is in scope too",
            "--add-path",
            "src/lib/**",
        )
        == 0
    )
    assert "replaced by t-1-b" in parent_acta.read_text()
    # The successor carries the parent's patch, so it runs and writes its own acta.
    assert run(box, "dispatch", "probe", "--task", str(box.root / "mandata" / "t-1-b.toml")) in (
        0,
        1,
    )
    child_acta = next((box.root / "acta").glob("*-t-1-b.md"))
    text = child_acta.read_text()
    assert "## what changed from the parent" in text
    assert "paths: src/** -> src/**, src/lib/**" in text
    assert "parent: t-1 — the helper is in scope too" in text


def test_status_keeps_the_newest_tasks_not_the_last_alphabetically(tmp_path: Path) -> None:
    root = tmp_path / "box"
    for number in range(12):
        directory = root / "tabularium" / f"t-{number:02d}"
        directory.mkdir(parents=True)
        stamp = f"20261101T0000{11 - number:02d}Z"
        (directory / f"dispatch-{stamp}.json").write_text(
            json.dumps({"task": directory.name, "state": "closed"}), encoding="utf-8"
        )
    # t-00 is the newest and t-11 the oldest: taking the last names would keep t-11.
    newest = augustus.recent_tasks(root, limit=1)
    assert [task.id for task in newest] == ["t-00"]
