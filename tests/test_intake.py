"""The intake: a task is handed on only when its contract is in the owner's words.

The gap the contract closes: a task can be executed perfectly and still solve the wrong problem.
The intake reads the five points before any work and refuses the task that cannot say what is
wrong, where it shows, what not to touch and what will be seen after the fix — or one that still
has a question for the owner.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import augustus
from harness import box as boxes
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

CONTRACT = """\
[contract]
symptom = "the row says 042"
visible = "docs/README.md, the 05-adr row"
boundaries = "do not edit 05-adr.md"
"""


def task_text(*, contract: str = CONTRACT, expected: bool = True) -> str:
    text = """\
schema_version = 1
id = "t-1"
goal = "the table names every decision"
kind = "bugfix"
paths = ["docs/**"]
"""
    if expected:
        text += 'expected = "the row names 043"\n'
    return text + contract + "\n[budget]\nmax_cents = 5\n"


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "README.md").write_text("ADR-001…042\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    return project, boxes.new_box(tmp_path / "castra", project)


def write_task(tmp_path: Path, text: str, name: str = "t-1.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_complete_contract_is_ready(tmp_path: Path) -> None:
    task = augustus.read_task(write_task(tmp_path, task_text()))
    result = augustus.intake(task)
    assert result.ready
    assert result.refusals == ()
    assert any("no reproduction" in gap for gap in result.gaps)


def test_a_task_without_a_contract_is_not_handed_on(tmp_path: Path) -> None:
    task = augustus.read_task(write_task(tmp_path, task_text(contract="")))
    result = augustus.intake(task)
    assert not result.ready
    assert "no contract" in result.refusals[0]


def test_a_contract_that_omits_a_point_is_refused(tmp_path: Path) -> None:
    partial = """\
[contract]
symptom = "the row says 042"
boundaries = "do not edit 05-adr.md"
"""
    task = augustus.read_task(write_task(tmp_path, task_text(contract=partial)))
    result = augustus.intake(task)
    assert not result.ready
    assert any("visible" in refusal for refusal in result.refusals)


def test_an_open_question_is_an_escalation(tmp_path: Path) -> None:
    open_contract = CONTRACT + 'missing = "which run was the old one"\n'
    task = augustus.read_task(write_task(tmp_path, task_text(contract=open_contract)))
    result = augustus.intake(task)
    assert not result.ready
    assert any("escalate" in refusal for refusal in result.refusals)
    assert any("which run was the old one" in refusal for refusal in result.refusals)


def test_the_expected_behaviour_is_part_of_the_contract(tmp_path: Path) -> None:
    task = augustus.read_task(write_task(tmp_path, task_text(expected=False)))
    result = augustus.intake(task)
    assert not result.ready
    assert any("no expected behaviour" in refusal for refusal in result.refusals)


def test_the_cli_answers_ready_and_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, box = make_box(tmp_path)
    ready = write_task(tmp_path, task_text())
    assert main(["--castra", str(box.root.parent), "intake", "probe", "--task", str(ready)]) == 0
    assert "ready" in capsys.readouterr().out

    refused = write_task(tmp_path, task_text(contract=""), name="t-2.toml")
    assert main(["--castra", str(box.root.parent), "intake", "probe", "--task", str(refused)]) == 1
    assert "refused" in capsys.readouterr().out

    code = main(
        ["--castra", str(box.root.parent), "intake", "probe", "--task", str(refused), "--json"]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["contract"] is None
    assert payload["refusals"]
