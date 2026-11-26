"""Explain reads the same rules a dispatch would use, and writes nothing.

The gap: the envelope, the route, the limits, the checks and the gaps were visible only after
the run. The test that keeps the resolver honest is "the explanation equals the record".
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import augustus, contour, executor
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
self_check = ["python", "-c", "print('ok')"]

[budget]
max_cents = 3
max_seconds = 60
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


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    return project, boxes.new_box(tmp_path / "castra", project)


def write_task(tmp_path: Path, text: str = TASK) -> Path:
    path = tmp_path / "t-1.toml"
    path.write_text(text, encoding="utf-8")
    return path


def tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


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


def test_explain_shows_the_frame_route_limits_checks_and_gaps_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    task = write_task(tmp_path)
    before = tree(box.root)
    assert main(["--castra", str(box.root.parent), "explain", "probe", "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "frame      " in out
    assert "rails      " in out
    assert "route      run -> verify" in out
    assert "limits     " in out and "3 cents" in out
    assert "room       " in out
    assert "checks     probe: smoke" in out
    assert "repro      not declared" in out
    assert "self check python" in out
    assert "gaps" in out
    assert "no reader is declared" in out
    assert tree(box.root) == before, "explain must not create a record, contract or event"


def test_explain_refuses_a_task_that_would_be_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    task = write_task(tmp_path, TASK.replace('paths = ["src/**"]\n', ""))
    assert main(["--castra", str(box.root.parent), "explain", "probe", "--task", str(task)]) == 1
    out = capsys.readouterr().out
    assert "refused    the task declares no paths" in out
    assert not (box.root / "tabularium" / "t-1").exists()


@requires_trajan
def test_the_explanation_matches_what_the_dispatch_record_says(tmp_path: Path) -> None:
    project, box = make_box(tmp_path)
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    task = write_task(
        tmp_path, TASK.replace("[budget]", f'change = "{patch}"\n\n[budget]')
    )
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    resolution = augustus.explain(
        box,
        augustus.read_task(task),
        routes=augustus.read_routes(None),
        config=config,
    )
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    record = json.loads(
        sorted((box.root / "tabularium" / "t-1").glob("dispatch-*.json"))[-1].read_text()
    )
    assert list(resolution.stages) == record["stages"]
    assert resolution.engine == record["engine"]
    assert resolution.frame.state == record["frame"]["state"]
    assert resolution.rails.milestone == (record["rails"] or {}).get("milestone")
    assert resolution.task.budget == record["budget"]


def test_explain_names_the_box_ceiling_that_binds(tmp_path: Path) -> None:
    _, box = make_box(tmp_path)
    task = augustus.read_task(write_task(tmp_path))
    config = executor.ExecutorConfig(
        path=tmp_path / "executor.toml", max_seconds=60, max_cents=1.0
    )
    result = augustus.explain(
        box, task, routes=augustus.read_routes(None), config=config
    )
    assert result.cents == 1.0
    assert any("the box's ceiling is what binds" in gap for gap in result.gaps)
