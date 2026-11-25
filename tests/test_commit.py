"""The commit after the verdict: the reviewed change, and only it, becomes the project.

The verdict is about a change; the commit is the sentence "this is the project now". These
tests hold the line between the two: a green verdict commits, a refused one does not, a tree
carrying unrelated edits cannot smuggle them into a commit that says "checks passed", and a
run that was told not to commit says so instead of leaving a silent gap.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import caesar, contour
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

FAILING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "raise SystemExit(1)"]
purpose = "a check that never passes"
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


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path, policy: str = PASSING) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\nother = 10\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(policy, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path, project: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project)


def make_patch(tmp_path: Path, project: Path) -> Path:
    (project / "src" / "app.py").write_text("value = 2\nother = 10\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def with_change(patch: Path, text: str = TASK) -> str:
    """A task that carries its own patch: `change` belongs before the `[budget]` table."""
    body = text.split("\n[budget]")[0]
    rest = "\n[budget]" + text.split("\n[budget]")[1]
    return body + f'change = "{patch}"\n' + rest


def write_task(tmp_path: Path, text: str, name: str = "t-1.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


def dispatch(box: boxes.Box, task: Path, *extra: str) -> int:
    return run(box, "dispatch", box.name, "--task", str(task), "--json", *extra)


def payload(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    data: object = json.loads(capsys.readouterr().out)
    assert isinstance(data, dict)
    return {str(key): value for key, value in data.items()}


def strings(value: object) -> list[str]:
    assert isinstance(value, list), "the field is a list in the record"
    return [str(item) for item in value]


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


@requires_trajan
def test_a_green_verdict_becomes_a_commit_that_quotes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    code = dispatch(box, task)
    answer = payload(capsys)
    assert code == 0 and answer["state"] == "closed"
    commit = answer["commit"]
    assert isinstance(commit, dict) and commit["status"] == "made"
    sha = str(commit["sha"])
    assert len(sha) == 40 and git(project, "rev-parse", "HEAD").strip() == sha
    message = git(project, "log", "-1", "--format=%B")
    assert "Verdict: pass" in message
    assert "Pantheon-Task: t-1" in message
    assert "tabularium/t-1/" in message, "the message names the report the verdict came from"
    assert "Pantheon <pantheon@harness.invalid>" in git(project, "log", "-1", "--format=%an <%ae>")
    assert git(project, "status", "--porcelain") == "", "a committed change is a clean tree"
    record = json.loads((box.root / "tabularium/t-1/1/commit.json").read_text(encoding="utf-8"))
    assert record["status"] == "made" and record["sha"] == sha
    assert record["verdict"] == "pass"


@requires_trajan
def test_a_hand_edit_cannot_ride_into_the_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    hand_edit = "value = 1\nother = 10\n# written by hand, never reviewed\n"
    (project / "src" / "app.py").write_text(hand_edit, encoding="utf-8")
    patch = make_patch(tmp_path, project)
    (project / "src" / "app.py").write_text(hand_edit, encoding="utf-8")
    box = make_box(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    assert dispatch(box, task) == 0
    payload(capsys)
    committed = git(project, "show", "--format=", "HEAD")
    assert "+value = 2" in committed and "written by hand" not in committed
    assert "# written by hand, never reviewed" in (project / "src" / "app.py").read_text(
        encoding="utf-8"
    )
    assert " M src/app.py" in git(project, "status", "--porcelain"), "the edit is still an edit"


@requires_trajan
def test_no_commit_is_recorded_instead_of_being_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    head = git(project, "rev-parse", "HEAD").strip()
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    code = dispatch(box, task, "--no-commit")
    answer = payload(capsys)
    assert code == 0 and answer["state"] == "closed"
    commit = answer["commit"]
    assert isinstance(commit, dict) and commit["status"] == "skipped"
    assert "--no-commit" in str(commit["reason"])
    assert git(project, "rev-parse", "HEAD").strip() == head, "nothing was committed"
    assert (project / "src" / "app.py").read_text(encoding="utf-8").startswith("value = 2")
    assert any("not in git" in item for item in strings(answer["unknowns"]))
    outbox = max((box.root / "acta").glob("*.md"), key=lambda item: item.stat().st_mtime)
    assert "not in git" in outbox.read_text(encoding="utf-8")


@requires_trajan
def test_a_refused_verdict_leaves_a_draft_not_a_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path, policy=FAILING)
    box = make_box(tmp_path, project)
    head = git(project, "rev-parse", "HEAD").strip()
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    code = dispatch(box, task)
    answer = payload(capsys)
    assert code == 1 and answer["state"] == "returned"
    assert answer["commit"] is None
    assert git(project, "rev-parse", "HEAD").strip() == head
    assert "value = 2" in (project / "src" / "app.py").read_text(encoding="utf-8")


@requires_trajan
def test_a_commit_that_cannot_be_made_is_a_failure_with_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project)
    write_task(tmp_path, with_change(patch))
    contract = caesar.Contract(
        path=tmp_path / "contract.toml",
        task="t-1",
        kind="bugfix",
        goal="change the app value",
        expected="two",
        allowed_paths=("src/**",),
        forbidden=(),
        budget={},
    )
    attempt = caesar.run_attempt(box.root, project, contract, patch)
    assert attempt.applied
    # The change is already in HEAD: there is nothing left to commit, and saying so is the
    # answer — an empty commit would claim a verdict was recorded where nothing changed.
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "same change")
    commit = caesar.commit_change(
        project, contract, attempt, verdict="pass", report="report.json", box_root=box.root
    )
    assert commit.status == "skipped" and "already in" in commit.reason


@requires_trajan
def test_a_task_in_automatic_mode_is_refused_because_nothing_runs_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project)
    text = with_change(patch).replace('kind = "bugfix"', 'kind = "bugfix"\nmode = "auto"')
    task = write_task(tmp_path, text)
    code = dispatch(box, task)
    answer = payload(capsys)
    assert code == 1 and answer["state"] == "refused"
    assert any("automatic mode" in note for note in strings(answer["notes"]))
    assert "value = 1" in (project / "src" / "app.py").read_text(encoding="utf-8")


def test_an_unknown_mode_is_an_error_not_a_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(
        tmp_path, TASK.replace('kind = "bugfix"', 'kind = "bugfix"\nmode = "sometimes"')
    )
    code = dispatch(box, task)
    error = capsys.readouterr().err
    assert code == 2 and "sometimes" in error and "manual" in error


def test_a_project_outside_git_has_nothing_to_commit(tmp_path: Path) -> None:
    project = tmp_path / "plain"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    contract = caesar.Contract(
        path=tmp_path / "contract.toml",
        task="t-1",
        kind="bugfix",
        goal="change",
        expected="two",
        allowed_paths=("src/**",),
        forbidden=(),
        budget={},
    )
    attempt = caesar.Attempt(
        task="t-1", number=1, status="applied", reason="", paths=("src/app.py",)
    )
    commit = caesar.commit_change(
        project, contract, attempt, verdict="pass", report="x", box_root=tmp_path / "box"
    )
    assert commit.status == "skipped" and "not a git repository" in commit.reason
