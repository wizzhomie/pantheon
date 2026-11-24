"""What a dispatch did not check, said out loud before anyone reads the word "closed".

The cases here are the ways a green answer can be narrower than it looks: the verdict came from
a checks-only run, from an earlier run of the same tree, from a task whose architecture was
never checked, or from a run nothing limited. Each one must appear in the outbox, in the record
and in `status` — a closed task that quietly forgets one of them is the failure this file is
about.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import container, contour
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
    box = boxes.new_box(tmp_path / "boxes", project)
    box.compose.write_text(
        container.render_compose(box, "nousresearch/hermes-agent@sha256:" + "3e9f" * 16),
        encoding="utf-8",
    )
    return project, box


def make_patch(tmp_path: Path, project: Path) -> Path:
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
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


def outbox(box: boxes.Box, capsys: pytest.CaptureFixture[str]) -> str:
    capsys.readouterr()
    latest = max((box.root / "outbox").glob("*.md"), key=lambda item: item.stat().st_mtime)
    return latest.read_text(encoding="utf-8")


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
def test_a_checks_only_verdict_says_nobody_read_the_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    code = main(
        ["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["state"] == "closed"
    assert any("no reviewer read the change" in item for item in payload["unknowns"])
    text = outbox(box, capsys)
    assert "## what we did not check" in text
    assert "no reviewer read the change" in text


@requires_trajan
def test_a_reused_verdict_says_the_checks_did_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "docs"').replace(
            'paths = ["src/**"]', 'paths = ["src/**"]'
        ),
    )
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    capsys.readouterr()
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "reused from" in out
    text = outbox(box, capsys)
    assert "the checks did not run in this dispatch" in text


@requires_trajan
def test_an_unchecked_architecture_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    text = with_change(patch, TASK.replace('kind = "bugfix"', 'kind = "feature"'))
    task = write_task(tmp_path, text)
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    body = outbox(box, capsys)
    assert "the architecture was not confirmed (unchecked)" in body


@requires_trajan
def test_a_task_without_a_budget_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(
        tmp_path,
        'schema_version = 1\nid = "t-1"\ngoal = "look"\nkind = "docs"\npaths = ["src/**"]\n'
        'expected = "the checks pass"\n',
    )
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    body = outbox(box, capsys)
    assert "the task declared no budget" in body


@requires_trajan
def test_status_shows_what_the_newest_dispatch_did_not_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    capsys.readouterr()
    assert main(["--boxes", str(box.root.parent), "status", box.name]) == 0
    out = capsys.readouterr().out
    assert "unknown    no reviewer read the change" in out


REVIEW = '{"summary": "adds a constant", "findings": []}'


@requires_trajan
def test_a_reviewed_dispatch_names_the_reader_and_drops_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    code = main(
        ["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--review"]
    )
    capsys.readouterr()
    assert code == 0
    records = (box.root / "artifacts" / "t-1").glob("dispatch-*.json")
    payload = json.loads(max(records, key=lambda item: item.name).read_text(encoding="utf-8"))
    assert payload["verify"]["reviewed"] is True
    assert payload["verify"]["review"]["status"] == "ok"
    assert not any("no reviewer read the change" in item for item in payload["unknowns"])
    body = outbox(box, capsys)
    assert "read by deepseek-flash" in body
    assert "review: deepseek-flash" in body, "the reading has its own line in the cost section"


@requires_trajan
def test_a_review_without_a_bill_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The agent answered but reported no cost: the reading happened, its price is unknown."""
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert (
        main(
            [
                "--boxes",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--review",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert any("the reviewer did not report what it cost" in item for item in payload["unknowns"])


@requires_trajan
def test_a_failed_review_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reader was asked and could not answer: that is not the same as nobody reading."""
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
    assert (
        main(
            [
                "--boxes",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--review",
                "--json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert any("the reviewer was asked and failed" in item for item in payload["unknowns"])


def declare_reader(box: boxes.Box, text: str = "schema_version = 1\nenabled = true\n") -> None:
    path = box.root / "profile" / "reviewer.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@requires_trajan
def test_a_box_that_declares_reading_reads_without_a_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    declare_reader(box)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    body = outbox(box, capsys)
    assert "read by deepseek-flash" in body
    assert "no reviewer read the change" not in body


@requires_trajan
def test_no_review_wins_over_the_declared_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    declare_reader(box)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert (
        main(
            [
                "--boxes",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--no-review",
            ]
        )
        == 0
    )
    body = outbox(box, capsys)
    assert "no reviewer read the change" in body
    assert "read by" not in body


@requires_trajan
def test_a_reading_above_the_declared_ceiling_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The call already happened; pretending the ceiling held would be the quiet pass."""
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    declare_reader(box, 'schema_version = 1\nenabled = true\nmax_cents = 0.01\n')
    bill = box.root / "state" / ".cache" / "review-usage.json"
    bill.parent.mkdir(parents=True, exist_ok=True)
    bill.write_text(
        json.dumps({"input_tokens": 900, "output_tokens": 60, "estimated_cost_usd": 0.004}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert (
        main(
            ["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--json"]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["review_cap"] == 0.01
    assert any("more than the 0.01 the box declared" in item for item in payload["unknowns"])
