"""The frame and its approval: what the project declares, and the record that someone accepted it.

An approval is a box record carrying the digest of the bytes a person accepted. These tests hold
the three ways that fact can be lost: the record is written but points at a different frame, the
frame moves after the approval and nobody notices, or a dispatch closes against a basis nobody
ever signed off. Each one has to be a line a person reads, not an absence.
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

TASK = """\
schema_version = 1
id = "t-frame"
goal = "check the project against the frame"
kind = "docs"
paths = ["docs/**"]

[budget]
max_seconds = 60
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
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("note\n", encoding="utf-8")
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


def write_task(tmp_path: Path, text: str = TASK) -> Path:
    path = tmp_path / "t-frame.toml"
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


def test_an_approval_records_the_digest_of_what_it_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    current = frame.read_frame(project)
    assert run(box, "approve", box.name, "--note", "the first model", "--json") == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["approved"] == current.digest
    assert answer["by"] == "owner" and answer["at"].endswith("Z")
    record = Path(str(answer["record"]))
    assert record.is_file() and record.parent == box.root / "tabularium" / "forma"
    written = json.loads(record.read_text(encoding="utf-8"))
    assert written["type"] == "frame.approval"
    assert written["digest"] == current.digest
    assert written["note"] == "the first model"
    assert {item["path"] for item in written["files"]} == {
        ".pantheon/architecture.toml",
    }
    assert ".trajan/context.toml" in written["missing"], "a missing file is listed, not ignored"
    ledger = box.root / "fasti" / f"{written['at'][:10]}.jsonl"
    event = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert event["type"] == "frame.approved" and event["actor"] == "owner"
    assert frame.standing(box).state == "approved"


def test_the_same_frame_approved_twice_is_two_records_not_an_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    assert run(box, "approve", box.name) == 0
    capsys.readouterr()
    assert run(box, "approve", box.name, "--note", "same bytes, said again") == 0
    capsys.readouterr()
    records = sorted((box.root / "tabularium" / "forma").glob("approval-*.json"))
    assert len(records) == 2
    assert frame.standing(box).state == "approved"


def test_a_frame_that_moved_after_the_approval_is_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    assert run(box, "approve", box.name) == 0
    capsys.readouterr()
    (project / ".pantheon" / "architecture.toml").write_text(MODEL + "\n", encoding="utf-8")
    standing = frame.standing(box)
    assert standing.state == "changed"
    assert standing.approval is not None and standing.approval.digest != standing.digest
    gap = standing.gap()
    assert gap is not None and "changed after it was approved" in gap
    assert "changed since approval" in standing.line()


@requires_trajan
def test_a_dispatch_says_which_frame_it_rested_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path)
    assert run(box, "approve", box.name) == 0
    capsys.readouterr()
    code = run(box, "dispatch", box.name, "--task", str(task), "--json")
    answer = json.loads(capsys.readouterr().out)
    assert code == 0 and answer["state"] == "closed"
    assert answer["frame"]["state"] == "approved"
    assert not any("frame" in str(item) for item in answer["unknowns"])


@requires_trajan
def test_an_unapproved_frame_is_a_visible_gap_not_a_silent_close(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    task = write_task(tmp_path)
    code = run(box, "dispatch", box.name, "--task", str(task), "--json")
    answer = json.loads(capsys.readouterr().out)
    assert code == 0 and answer["state"] == "closed"
    assert answer["frame"]["state"] == "never"
    assert any("never approved" in str(item) for item in answer["unknowns"])
    outbox = max((box.root / "acta").glob("*.md"), key=lambda item: item.stat().st_mtime)
    assert "never approved" in outbox.read_text(encoding="utf-8")


def test_approving_nothing_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    box = make_box(tmp_path, project)
    assert run(box, "approve", box.name) == 2
    error = capsys.readouterr().err
    assert "no frame to approve" in error
    assert not (box.root / "tabularium" / "forma").exists()


def test_approving_a_package_that_does_not_parse_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    (project / ".pantheon" / "architecture.toml").write_text("[layer\nname = ", encoding="utf-8")
    box = make_box(tmp_path, project)
    assert run(box, "approve", box.name) == 2
    error = capsys.readouterr().err
    assert "does not read as a package" in error
    assert "architecture.toml" in error
    assert not (box.root / "tabularium" / "forma").exists()


def test_status_names_the_frame(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    assert run(box, "status", box.name) == 0
    assert "frame      never approved" in capsys.readouterr().out
    assert run(box, "approve", box.name) == 0
    capsys.readouterr()
    assert run(box, "status", box.name) == 0
    out = capsys.readouterr().out
    short = frame.read_frame(project).digest.removeprefix("sha256:")[:12]
    assert "frame      approved" in out and short in out


def test_a_broken_approval_record_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    directory = box.root / "tabularium" / "forma"
    directory.mkdir(parents=True)
    (directory / "approval-20260101T000000Z.json").write_text("{not json", encoding="utf-8")
    assert run(box, "status", box.name) == 2
    assert "does not parse" in capsys.readouterr().err
