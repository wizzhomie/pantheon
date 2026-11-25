"""Running one step of a plan: the same gates, the same checks, and a record in the campaign.

A step is not a second kind of work. It becomes a task, goes through the write gate, the checks,
the reader and the commit exactly like a dispatched task, and the campaign records what happened.
These tests hold the three things that make the campaign worth having: a step waits for its
dependencies, the plan cannot change between two steps without a reason, and the state is
written from the verdict rather than from optimism.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour, frame
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

MODEL = """\
schema_version = 1

[project]
root_packages = ["probe"]

[[layer]]
name = "app"
modules = ["probe"]
"""

PATCH = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

CAMPAIGN = """\
schema_version = 1
id = "c-1"
goal = "bump the value"
frame = "{frame}"

[[step]]
id = "s1"
goal = "bump the value in src/app.py"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[[step]]
id = "s2"
goal = "bump it a second time"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is still two, and checked"
depends_on = ["s1"]

[[acceptance]]
id = "a1"
statement = "the value is two"
step = "s1"

[budget]
max_cents = 10
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
    (project / "src" / "probe").mkdir()
    (project / "src" / "probe" / "__init__.py").write_text("", encoding="utf-8")
    (project / ".pantheon").mkdir()
    (project / ".pantheon" / "architecture.toml").write_text(MODEL, encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path, project: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project)


def write_executor(tmp_path: Path) -> Path:
    script = tmp_path / "fake-executor.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        'with open(os.environ["FAKE_EXECUTOR_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write("called\\n")\n'
        "print(json.dumps({\n"
        f'    "patch": {json.dumps(PATCH)},\n'
        '    "model": "fake-model",\n'
        '    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost_cents": 0.5},\n'
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def with_executor(box: boxes.Box, command: Path) -> None:
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 60\n', encoding="utf-8"
    )


def approved(project: Path, box: boxes.Box) -> str:
    assert run(box, "approve", box.name) == 0
    return frame.read_frame(project).digest


def write_campaign(project: Path, text: str) -> Path:
    directory = project / ".pantheon" / "expeditiones"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "c-1.toml"
    path.write_text(text, encoding="utf-8")
    return path


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


def step(box: boxes.Box, identifier: str, *extra: str) -> int:
    return run(box, "step", box.name, "--campaign", "c-1", "--step", identifier, *extra)


def state(box: boxes.Box) -> dict[str, object]:
    path = box.root / "tabularium" / "expeditio" / "c-1" / "state.json"
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


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


def prepared(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    os.environ["FAKE_EXECUTOR_LOG"] = str(tmp_path / "executor.log")
    with_executor(box, write_executor(tmp_path))
    digest = approved(project, box)
    write_campaign(project, CAMPAIGN.format(frame=digest))
    # A plan is a repository artifact: it is committed like the documents it promises.
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "the plan")
    return project, box


@requires_trajan
def test_a_step_runs_through_the_gates_and_the_campaign_records_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = prepared(tmp_path)
    capsys.readouterr()
    assert step(box, "s1") == 0
    out = capsys.readouterr().out
    assert "state      closed" in out
    assert "commit" in out and "commit     not made" not in out
    assert "verdict    in progress — 1 of 2 steps closed" in out
    written = state(box)
    recorded_steps = written["steps"]
    assert isinstance(recorded_steps, dict)
    recorded = recorded_steps["s1"]
    assert isinstance(recorded, dict)
    assert recorded["state"] == "closed" and recorded["verdict"] == "pass"
    assert recorded["cents"] == 0.5
    spend = written["spend"]
    assert isinstance(spend, dict) and spend["cents"] == 0.5
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert git(project, "status", "--porcelain") == "", "the step's change is committed"


@requires_trajan
def test_two_green_steps_close_the_campaign(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(tmp_path)
    capsys.readouterr()
    assert step(box, "s1") == 0
    capsys.readouterr()
    assert step(box, "s2") == 0
    out = capsys.readouterr().out
    assert "verdict    complete — 2 of 2 steps closed" in out
    assert "accept     a1: the value is two — satisfied by s1" in out


@requires_trajan
def test_a_step_waits_for_the_steps_it_depends_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(tmp_path)
    capsys.readouterr()
    assert step(box, "s2") == 2
    error = capsys.readouterr().err
    assert "waits for s1" in error
    assert not (tmp_path / "executor.log").exists(), "nothing was asked and nothing paid"


@requires_trajan
def test_a_changed_plan_needs_a_reason_before_the_next_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = prepared(tmp_path)
    capsys.readouterr()
    assert step(box, "s1") == 0
    path = project / ".pantheon" / "expeditiones" / "c-1.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("bump it a second time", "bump again"),
                    encoding="utf-8")
    capsys.readouterr()
    assert step(box, "s2") == 2
    assert "the plan changed since the last step" in capsys.readouterr().err
    assert step(box, "s2", "--reason", "split the second step") == 0
    capsys.readouterr()
    revisions = state(box)["revisions"]
    assert isinstance(revisions, list) and len(revisions) == 1
    first = revisions[0]
    assert isinstance(first, dict) and first["reason"] == "split the second step"


@requires_trajan
def test_a_step_that_wrote_nothing_is_recorded_but_not_done(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(tmp_path)
    (box.root / "persona" / "executor.toml").unlink()
    capsys.readouterr()
    assert step(box, "s1") == 1, "a closed verification about no work is not a done step"
    out = capsys.readouterr().out
    assert "verdict    in progress — 0 of 2 steps closed" in out
    assert "steps that wrote nothing: s1" in out
    written = state(box)
    steps = written["steps"]
    assert isinstance(steps, dict)
    recorded = steps["s1"]
    assert isinstance(recorded, dict) and recorded["state"] == "noop"


@requires_trajan
def test_a_plan_that_cannot_start_runs_no_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = prepared(tmp_path)
    path = project / ".pantheon" / "expeditiones" / "c-1.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('depends_on = ["s1"]', 'depends_on = ["s2"]'),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert step(box, "s1") == 2
    assert "the plan cannot start" in capsys.readouterr().err
    assert not (box.root / "tabularium" / "expeditio" / "c-1" / "state.json").exists()
