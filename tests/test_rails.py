"""The rails: what a project says its work serves, and the record that the owner accepted it.

These tests hold the ways a rail can be lost: it is never written, it is written but never
accepted, it moves after acceptance, or it is so empty that accepting it would look like a basis
while saying nothing. Each one has to be a line a person reads, not an absence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import box as boxes
from harness import rails
from harness.cli import main

FRAME = """\
schema_version = 1

[brief]
why = "the harness must not let a task drift"
for = "the owner"
success = "every task names the milestone it serves"

[[goal]]
text = "a task that serves nothing is refused"

[[milestone]]
id = "M1"
title = "rails"
closes_when = "a task names a milestone and the gate checks it"
metric = "drift"

[[metric]]
name = "drift"
direction = "down"
threshold = "zero tasks outside the plan"

[[principle]]
text = "evidence over claims"
why = "a run that cannot be re-read is not evidence"
"""


def make_project(tmp_path: Path, frame_text: str | None = FRAME) -> Path:
    project = tmp_path / "probe"
    project.mkdir()
    (project / ".pantheon").mkdir()
    if frame_text is not None:
        (project / ".pantheon" / "rails.toml").write_text(frame_text, encoding="utf-8")
    return project


def make_box(tmp_path: Path, project: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project)


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


def test_a_frame_is_read_and_its_missing_parts_are_named_one_by_one(tmp_path: Path) -> None:
    project = make_project(
        tmp_path,
        """\
schema_version = 1

[brief]
why = ""

[[milestone]]
id = "M1"
title = "rails"
closes_when = ""
metric = ""
""",
    )
    current = rails.read(project)
    assert current is not None
    assert current.brief.why == ""
    assert current.missing == (
        "brief.why",
        "brief.for",
        "brief.success",
        "goals",
        "metrics",
        "milestone M1.closes_when",
        "milestone M1.metric",
        "principles",
    )


def test_no_file_is_not_an_empty_frame(tmp_path: Path) -> None:
    project = make_project(tmp_path, None)
    box = make_box(tmp_path, project)
    assert rails.read(project) is None
    standing = rails.standing(box)
    assert standing.state == "none"
    gap = standing.gap()
    assert gap is not None and "declares no rails" in gap
    assert standing.line() == "nothing declared"


def test_rails_never_approved_are_a_draft_not_a_basis(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    standing = rails.standing(box)
    assert standing.state == "never"
    gap = standing.gap()
    assert gap is not None and "never approved" in gap


def test_an_approval_records_the_digest_and_what_was_delegated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    current = rails.read(project)
    assert current is not None
    assert run(box, "rails", box.name, "--approve", "--note", "the first rail",
               "--delegate", "how many milestones?=one per stage", "--json") == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["approved"] == current.digest
    assert answer["file"] == ".pantheon/rails.toml"
    assert answer["delegations"] == [
        {"question": "how many milestones?", "default": "one per stage"}
    ]
    record = Path(str(answer["record"]))
    assert record.is_file() and record.parent == box.root / "tabularium" / "forma"
    written = json.loads(record.read_text(encoding="utf-8"))
    assert written["type"] == "rails.approval"
    assert written["digest"] == current.digest
    assert written["missing"] == []
    assert written["base"] is None, "a project outside git has no base, and that is not a zero"
    event = json.loads(
        (box.root / "fasti" / f"{written['at'][:10]}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert event["type"] == "rails.approved" and event["actor"] == "owner"
    assert rails.standing(box).state == "approved"


def test_approving_the_same_rails_twice_is_two_records(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    rails.approve(box, by="owner")
    rails.approve(box, by="owner")
    records = sorted((box.root / "tabularium" / "forma").glob("rails-approval-*.json"))
    assert len(records) == 2
    assert rails.standing(box).state == "approved"


def test_rails_that_moved_after_the_approval_are_stale(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    rails.approve(box, by="owner")
    (project / ".pantheon" / "rails.toml").write_text(FRAME + "\n", encoding="utf-8")
    standing = rails.standing(box)
    assert standing.state == "changed"
    assert standing.approval is not None and standing.approval.digest != standing.digest
    gap = standing.gap()
    assert gap is not None and "changed after they were approved" in gap
    assert "changed since approval" in standing.line()


def test_approving_rails_that_say_nothing_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(
        tmp_path,
        """\
schema_version = 1

[brief]
why = "there must be something"
""",
    )
    box = make_box(tmp_path, project)
    assert run(box, "rails", box.name, "--approve") == 2
    error = capsys.readouterr().err
    assert "not filled enough to be a basis" in error
    assert "brief.for" in error and "milestones" in error
    assert not (box.root / "tabularium" / "forma").exists()


def test_approving_missing_rails_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path, None)
    box = make_box(tmp_path, project)
    with pytest.raises(boxes.HarnessError, match="no rails to approve"):
        rails.approve(box, by="owner")


def test_a_delegation_needs_both_sides(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    assert run(box, "rails", box.name, "--approve", "--delegate", "no default") == 2
    assert "QUESTION=DEFAULT" in capsys.readouterr().err


def test_a_milestone_on_an_undeclared_metric_is_refused(tmp_path: Path) -> None:
    project = make_project(
        tmp_path,
        FRAME.replace('metric = "drift"', 'metric = "speed"'),
    )
    with pytest.raises(boxes.HarnessError, match="no metric table declares"):
        rails.read(project)


def test_a_metric_direction_must_be_one_of_two(tmp_path: Path) -> None:
    project = make_project(tmp_path, FRAME.replace('direction = "down"', 'direction = "sideways"'))
    with pytest.raises(boxes.HarnessError, match="direction 'sideways'"):
        rails.read(project)


def test_a_milestone_can_be_looked_up_by_id(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    current = rails.read(project)
    assert current is not None
    found = current.milestone("M1")
    assert found is not None
    assert found.title == "rails"
    assert current.milestone("M9") is None


def test_status_names_the_rails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    assert run(box, "status", box.name) == 0
    assert "rails      never approved" in capsys.readouterr().out
    rails.approve(box, by="owner")
    assert run(box, "status", box.name) == 0
    out = capsys.readouterr().out
    fresh = rails.read(project)
    assert fresh is not None
    short = fresh.digest.removeprefix("sha256:")[:12]
    assert "rails      approved" in out and short in out


def test_rails_show_renders_the_frame_and_the_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    assert run(box, "rails", box.name) == 0
    out = capsys.readouterr().out
    assert "rails      never approved" in out
    assert "why        the harness must not let a task drift" in out
    assert "milestone  M1  rails  (open)" in out
    assert "closed" not in out

    assert run(box, "rails", box.name, "--approve") == 0
    capsys.readouterr()
    assert run(box, "rails", box.name, "--json") == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["state"] == "approved"
    assert answer["milestones"][0]["id"] == "M1"
    assert answer["metrics"] == [
        {"name": "drift", "direction": "down", "threshold": "zero tasks outside the plan"}
    ]
    assert answer["approved"]["by"] == "owner"
