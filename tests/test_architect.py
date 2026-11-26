"""The architect: the same agent in a different job — the frame, not the code.

The role is not a title in a prompt. It is a declared pair: ``role = "architect"`` and
``kind = "architecture"``, and the box refuses either half without the other. The prompt then
says what the architect may not do, because the gate would refuse it anyway, and a refusal in
the gate that the prompt already avoided is a wasted paid call.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour
from harness.cli import main
from harness.executors import container as box_agent

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

TASK = """\
schema_version = 1
id = "t-arch"
goal = "declare the app layer in the model"
kind = "architecture"
role = "architect"
paths = [".pantheon/**"]
expected = "the model names the layer that exists"

[budget]
max_seconds = 60

[contract]
symptom = "the model does not name the layer that exists"
visible = ".pantheon/architecture.toml names the layer"
boundaries = ".pantheon/**"
missing = ""
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src" / "probe").mkdir(parents=True)
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


def make_patch(tmp_path: Path, project: Path) -> Path:
    model = project / ".pantheon" / "architecture.toml"
    extra = (
        '\n[[forbidden]]\nname = "no ui from data"\n'
        'source = ["probe.ui"]\ntarget = ["probe.data"]\nreason = "layers"\n'
    )
    model.write_text(MODEL + extra, encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def with_change(patch: Path, text: str = TASK) -> str:
    body = text.split("\n[budget]")[0]
    rest = "\n[budget]" + text.split("\n[budget]")[1]
    return body + f'change = "{patch}"\n' + rest


def write_task(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "t-arch.toml"
    path.write_text(text, encoding="utf-8")
    return path


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


def dispatch(box: boxes.Box, task: Path) -> int:
    return run(box, "dispatch", box.name, "--task", str(task), "--json")


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


def test_the_architect_prompt_says_what_it_may_not_do() -> None:
    prompt = box_agent._prompt(
        {
            "role": "architect",
            "goal": "declare the layers",
            "kind": "architecture",
            "paths": [".pantheon/**"],
            "expected": "a model",
        },
        card="# Среда\n- проект probe\n",
    )
    assert "архитектор" in prompt
    assert "не пишешь код и тесты" in prompt
    assert "declare the layers" in prompt and ".pantheon/**" in prompt
    assert "ERROR:" in prompt
    assert "# Среда" in prompt
    builder = box_agent._prompt({"role": "executor", "goal": "fix", "paths": ["src/**"]})
    assert "архитектор" not in builder and "ERROR:" in builder


def test_an_architecture_task_without_the_role_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, TASK.replace('role = "architect"\n', ""))
    code = dispatch(box, task)
    answer = json.loads(capsys.readouterr().out)
    assert code == 1 and answer["state"] == "refused"
    assert any("role" in str(note) and "architect" in str(note) for note in answer["notes"])


def test_the_architect_cannot_be_asked_for_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, TASK.replace('kind = "architecture"', 'kind = "bugfix"'))
    code = dispatch(box, task)
    answer = json.loads(capsys.readouterr().out)
    assert code == 1 and answer["state"] == "refused"
    assert any("writes the frame" in str(note) for note in answer["notes"])


def test_an_unknown_role_is_an_error_not_a_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, TASK.replace('role = "architect"', 'role = "reviewer"'))
    code = dispatch(box, task)
    error = capsys.readouterr().err
    assert code == 2 and "reviewer" in error and "architect" in error


@requires_trajan
def test_an_architecture_dispatch_is_the_architect_and_commits_the_frame(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    code = dispatch(box, task)
    answer = json.loads(capsys.readouterr().out)
    assert code == 0 and answer["state"] == "closed"
    assert answer["role"] == "architect"
    assert answer["commit"]["status"] == "made"
    record = json.loads(
        next((box.root / "tabularium" / "t-arch").glob("dispatch-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert record["role"] == "architect"
    assert git(project, "log", "-1", "--format=%s").startswith("architecture:")
    assert "forbidden" in (project / ".pantheon" / "architecture.toml").read_text(
        encoding="utf-8"
    )
