"""The check debt: narrowed verdicts stand until a full run repays them.

A narrowed verdict is a decision with a price: it ran the checks the change could touch and left
the rest for later. These tests hold the three ways the counter can lie: a reused verdict counted
twice, a full run that never resets the debt, and a record whose report is gone read as repaid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import augustus, debt
from harness import box as boxes
from harness.cli import main


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    project.mkdir()
    return project


def make_box(tmp_path: Path, project: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project)


def record(
    box: boxes.Box,
    *,
    run: str,
    at: str,
    skipped: list[str] | None,
    task: str = "t-1",
    reused_from: str | None = None,
) -> Path:
    directory = box.root / "tabularium" / task / run
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "pass",
        "report": str(directory / "report.json"),
        "at": at,
        "reused_from": reused_from,
    }
    if skipped is not None:
        payload["skipped"] = skipped
    path = directory / "box-verify.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_narrowed_verdicts_are_counted_until_a_full_run(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    record(box, run="verify-1", at="2026-11-25T01:00:00Z", skipped=[])
    record(box, run="verify-2", at="2026-11-25T02:00:00Z", skipped=["trajan"])
    record(box, run="verify-3", at="2026-11-25T03:00:00Z", skipped=["trajan"])
    owed = debt.tally(box)
    assert owed.count == 2
    assert owed.narrowed == ("verify-3", "verify-2")
    assert owed.last_full == "verify-1"
    assert "2 narrowed verdict(s) since verify-1" in owed.line()


def test_a_full_run_repays_the_debt(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    record(box, run="verify-2", at="2026-11-25T02:00:00Z", skipped=["trajan"])
    record(box, run="verify-4", at="2026-11-25T04:00:00Z", skipped=[])
    owed = debt.tally(box)
    assert owed.count == 0 and owed.last_full == "verify-4"
    assert "the last verdict ran every check" in owed.line()


def test_a_reused_verdict_is_not_counted_twice(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    record(box, run="verify-1", at="2026-11-25T01:00:00Z", skipped=["trajan"])
    record(
        box,
        run="verify-2",
        at="2026-11-25T02:00:00Z",
        skipped=["trajan"],
        reused_from="t-1/verify-1",
    )
    assert debt.tally(box).count == 1


def test_a_record_whose_report_is_gone_is_unreadable_not_repaid(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    record(box, run="verify-1", at="2026-11-25T01:00:00Z", skipped=None)
    owed = debt.tally(box)
    assert owed.count == 0 and owed.unknown == 1
    assert "unreadable" in owed.line()


def test_an_older_record_without_skipped_is_read_from_its_report(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    path = record(box, run="verify-1", at="2026-11-25T01:00:00Z", skipped=None)
    report = path.parent / "report.json"
    report.write_text(
        json.dumps({"run": {"checks": [{"check_id": "lint", "selected": True},
                                       {"check_id": "trajan", "selected": False}]}}),
        encoding="utf-8",
    )
    owed = debt.tally(box)
    assert owed.count == 1 and owed.unknown == 0


def test_debt_at_the_limit_is_due(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    for hour in range(1, debt.LIMIT + 1):
        record(box, run=f"verify-{hour}", at=f"2026-11-25T0{hour}:00:00Z", skipped=["trajan"])
    owed = debt.tally(box)
    assert owed.count == debt.LIMIT
    assert owed.due()
    assert "the next dispatch runs the full set" in owed.line()


def test_status_shows_the_debt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    record(box, run="verify-1", at="2026-11-25T01:00:00Z", skipped=["trajan"])
    assert main(["--castra", str(box.root.parent), "status", box.name]) == 0
    out = capsys.readouterr().out
    assert "debt       1 narrowed" in out


def test_a_due_debt_turns_the_next_verification_into_a_full_run(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    for hour in range(1, debt.LIMIT + 1):
        record(box, run=f"verify-{hour}", at=f"2026-11-25T0{hour}:00:00Z", skipped=["trajan"])
    notes: list[str] = []
    assert augustus._verification_paths(box, ("harness/cli.py",), notes) is None
    assert any("check debt reached" in note for note in notes)


def test_a_debt_below_the_limit_keeps_the_narrowed_set(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    record(box, run="verify-1", at="2026-11-25T01:00:00Z", skipped=["trajan"])
    notes: list[str] = []
    paths = augustus._verification_paths(box, ("harness/cli.py",), notes)
    assert paths == ("harness/cli.py",)
    assert notes == []
