"""The archive: a closed change keeps its reason, and the reasons are read by path.

ADR-045: the unit is a change with its reason, the trigger is the close, the archive is a
projection of the record and lives in the box. These tests hold the projection (what an entry
keeps), the index (what a path query finds) and the one thing the archive must never do — lose a
closed change without saying so.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness import archivum, contour
from harness import box as boxes
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "value"
argv = ["grep", "-qx", "value = 2", "src/app.py"]
purpose = "the value is two"
"""

TASK = """\
schema_version = 1
id = "t-1"
goal = "bump the value in src/app.py"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[contract]
symptom = "the value is one"
visible = "src/app.py holds value = 2"
boundaries = "nothing outside src/"
missing = ""

[budget]
max_cents = 5
max_seconds = 60
"""

PATCH = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

SNAPSHOT = """\
schema_version = 1
id = "t-1"
goal = "bump the value in src/app.py"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"
repro = ["grep", "-qx", "value = 2", "src/app.py"]

[contract]
symptom = "the value is one"
visible = "the value is two"
boundaries = "nothing outside src/"
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


def make_box(tmp_path: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", make_project(tmp_path))


def prepared(tmp_path: Path, box: boxes.Box) -> tuple[Path, dict[str, object]]:
    """The files a closed record points at, and the record itself: the archive is a projection."""
    attempt = box.root / "tabularium" / "t-1" / "1"
    attempt.mkdir(parents=True)
    change = attempt / "change.diff"
    change.write_text(PATCH, encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"run": {"checks": []}}), encoding="utf-8")
    (box.root / "tabularium" / "t-1" / "task.json").write_text(SNAPSHOT, encoding="utf-8")
    record: dict[str, object] = {
        "task": "t-1",
        "kind": "bugfix",
        "role": "executor",
        "paths": ["src/**"],
        "state": "closed",
        "contract": {
            "symptom": "the value is one",
            "visible": "the value is two",
            "boundaries": "nothing outside src/",
            "missing": None,
        },
        "self_check": {"status": "passed", "exit_code": 0, "tail": "ok"},
        "verify": {"status": "pass", "report": str(report), "exit_code": 0},
        "attempt": {"directory": str(attempt), "paths": ["src/app.py"]},
        "commit": {"status": "made", "sha": "abc123", "subject": "bugfix: bump the value"},
        "executor": {"usage": {"cost_cents": 0.4}},
        "timings": {"total": 12.5},
        "notes": ["the check the task shows the model: passed (ok)"],
    }
    return report, record


def stamp() -> datetime:
    """One fixed moment, so the address in every assertion is the same address."""
    return datetime(2026, 11, 26, 9, 30, tzinfo=UTC)


def test_a_closed_change_keeps_its_reason(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    folder = archivum.write(box.root, record, stamp())
    assert folder is not None
    entry = json.loads((folder / "entry.json").read_text(encoding="utf-8"))
    assert entry["address"] == "t-1/20261126T093000Z"
    assert entry["goal"] == "bump the value in src/app.py"
    assert entry["commit"] == {"sha": "abc123", "subject": "bugfix: bump the value"}
    assert entry["contract"]["boundaries"] == "nothing outside src/"
    assert entry["checks"]["hidden"]["status"] == "pass"
    assert entry["checks"]["hidden"]["check_hash"], "the check itself is hashed"
    assert entry["checks"]["hidden"]["report_hash"], "the evidence is hashed"
    assert entry["checks"]["visible"]["status"] == "passed"
    assert entry["touched"] == ["src/app.py"]
    assert entry["cost_cents"] == pytest.approx(0.4)
    assert entry["seconds"] == pytest.approx(12.5)
    assert (folder / "change.diff").read_text(encoding="utf-8") == PATCH


def test_the_index_finds_a_change_by_the_path_it_touched(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    index = json.loads((box.root / "archivum" / "index.json").read_text(encoding="utf-8"))
    assert index["paths"]["src/app.py"] == ["t-1/20261126T093000Z"]
    assert [item.address for item in archivum.for_paths(box.root, ("src/**",))] == [
        "t-1/20261126T093000Z"
    ]
    assert archivum.for_paths(box.root, ("docs/**",)) == ()


def test_the_index_is_rebuilt_from_the_entries(tmp_path: Path) -> None:
    """A deleted index is not a lost archive: the entries are the source, the index is a view."""
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    (box.root / "archivum" / "index.json").unlink()
    assert [item.address for item in archivum.for_paths(box.root, ("src/**",))] == [
        "t-1/20261126T093000Z"
    ]


def test_a_change_that_was_not_closed_is_not_archived(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    returned = {**record, "state": "returned"}
    skipped = {**record, "commit": {"status": "skipped"}}
    assert archivum.write(box.root, returned, stamp()) is None
    assert archivum.write(box.root, skipped, stamp()) is None
    assert archivum.entries(box.root) == ()


def test_rejected_attempts_are_kept_with_their_class(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    directory = box.root / "tabularium" / "t-1"
    earlier = {
        "state": "returned",
        "failure_class": "incomplete",
        "verify": {"status": "fail"},
        "attempt": {"directory": str(directory / "1")},
        "notes": ["the write gate refused the change: outside the contract"],
    }
    (directory / "dispatch-20261126T092000Z.json").write_text(
        json.dumps(earlier), encoding="utf-8"
    )
    folder = archivum.write(box.root, record, stamp())
    assert folder is not None
    entry = json.loads((folder / "entry.json").read_text(encoding="utf-8"))
    assert entry["rejected"] == [
        {
            "address": "t-1/20261126T092000Z",
            "state": "returned",
            "class": "incomplete",
            "diff_hash": entry["rejected"][0]["diff_hash"],
            "why": "the write gate refused the change: outside the contract",
        }
    ]
    assert entry["rejected"][0]["diff_hash"], "the rejected change is hashed too"


def test_an_entry_without_evidence_says_which_part_is_missing(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    (box.root / "tabularium" / "t-1" / "task.json").unlink()
    folder = archivum.write(box.root, {**record, "verify": None}, stamp())
    assert folder is not None
    entry = json.loads((folder / "entry.json").read_text(encoding="utf-8"))
    assert entry["goal"] == ""
    assert entry["checks"]["hidden"]["check_hash"] is None
    assert entry["checks"]["hidden"]["report_hash"] is None


def test_an_archive_failure_is_an_event_not_a_silent_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict is already written; a lost archive must be visible in the box's journal."""
    from harness import augustus

    def broken(*args: object, **kwargs: object) -> Path:
        raise OSError("the disk is full")

    monkeypatch.setattr(archivum, "write", broken)
    box = boxes.Box(root=tmp_path / "box", name="probe", project=tmp_path, created="")
    (box.root / "fasti").mkdir(parents=True)
    record: dict[str, object] = {"task": "t-1", "state": "closed"}
    augustus._archive(box, record, stamp())
    events = [
        json.loads(line)
        for path in (box.root / "fasti").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(item["type"] == "archive.failed" for item in events)
    assert "the disk is full" in " ".join(str(item["reason"]) for item in events)


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
def test_a_real_dispatch_writes_the_archive_and_asks_it_by_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    patch = tmp_path / "change.diff"
    patch.write_text(PATCH, encoding="utf-8")
    task = tmp_path / "t-1.toml"
    text = TASK.replace("[contract]", f'change = "{patch}"\n\n[contract]')
    task.write_text(text, encoding="utf-8")
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    capsys.readouterr()
    entries = archivum.entries(box.root)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.goal == "bump the value in src/app.py"
    assert entry.contract is not None and entry.contract["boundaries"] == "nothing outside src/"
    assert entry.commit == git(project, "rev-parse", "HEAD").strip()
    assert entry.touched == ("src/app.py",)
    assert entry.diff_hash
    assert main(
        ["--castra", str(box.root.parent), "history", box.name, "--path", "src/**"]
    ) == 0
    out = capsys.readouterr().out
    assert "bump the value in src/app.py" in out and "src/app.py" in out
    assert (
        main(["--castra", str(box.root.parent), "history", box.name, "--diff", entry.address]) == 0
    )
    assert capsys.readouterr().out.strip() == PATCH.strip()
    assert main(
        ["--castra", str(box.root.parent), "history", box.name, "--path", "docs/**"]
    ) == 0
    assert "nothing archived touches docs/**" in capsys.readouterr().out
    record = json.loads(
        next((box.root / "tabularium" / "t-1").glob("dispatch-*.json")).read_text(encoding="utf-8")
    )
    assert record["sensitivity"] is None, "a task with no check of its own has nothing to reverse"
    assert any("no check of its own" in note for note in record["notes"])


@requires_trajan
def test_a_dispatch_records_whether_the_task_s_check_notices_the_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-047 in the record: the rehearsal is shown, and it does not move the verdict.

    The change is a text block the task's own check greps for: reversing it turns the check red,
    so the piece is noticed, the verdict is still the checks', and the record carries the count.
    """
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / "docs" / "guide.md").write_text("# Guide\n\nOld line.\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(
        'name = "probe"\n\n[[checks]]\nid = "base"\nargv = ["grep", "-qx", "value = 1", '
        '"src/app.py"]\npurpose = "the base is untouched"\n',
        encoding="utf-8",
    )
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    box = boxes.new_box(tmp_path / "castra", project)
    patch = tmp_path / "change.diff"
    patch.write_text(
        "--- a/docs/guide.md\n"
        "+++ b/docs/guide.md\n"
        "@@ -1,3 +1,4 @@\n"
        " # Guide\n"
        " \n"
        " Old line.\n"
        "+Rule: the check notices.\n",
        encoding="utf-8",
    )
    task = tmp_path / "t-2.toml"
    task.write_text(
        f"""\
schema_version = 1
id = "t-2"
goal = "write the rule down"
kind = "docs"
paths = ["docs/**"]
expected = "the guide carries the rule"
change = "{patch}"
repro = ["grep", "-qF", "Rule: the check notices.", "docs/guide.md"]

[contract]
symptom = "the rule is not written"
visible = "docs/guide.md carries the rule"
boundaries = "docs/guide.md only"
missing = ""

[budget]
max_cents = 2
max_seconds = 60
""",
        encoding="utf-8",
    )
    code = main(
        ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    rehearsal = payload["sensitivity"]
    assert rehearsal is not None, "the record must say whether the check notices the change"
    assert rehearsal["noticed"] == 1 and rehearsal["blind"] == 0
    assert payload["state"] == "closed", "the rehearsal is information, not a verdict"


def test_the_owner_s_mark_is_written_beside_the_change(tmp_path: Path) -> None:
    """The third acceptance mark: a person's yes, about one diff, kept with the entry."""
    from harness import augustus

    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    entry = archivum.find(box.root, "t-1/20261126T093000Z")
    assert entry is not None
    made = augustus.accept(box, entry.address, note="the owner read it and wants it")
    assert made.decision == archivum.ACCEPTED
    assert made.commit == entry.commit and made.diff_hash == entry.diff_hash
    marks = archivum.decisions(box.root, entry.address)
    assert [item.decision for item in marks] == [archivum.ACCEPTED]
    stored = json.loads(
        (box.root / "archivum" / "t-1" / "20261126T093000Z" / "acceptance.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored["decisions"][0]["note"] == "the owner read it and wants it"
    assert stored["decisions"][0]["at"].endswith("Z")


def test_a_mark_about_a_change_that_never_closed_is_refused(tmp_path: Path) -> None:
    from harness import augustus

    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError, match="no archived change"):
        augustus.accept(box, "t-1/20260101T000000Z")


def test_a_second_mark_follows_the_first(tmp_path: Path) -> None:
    """A later "no" does not erase the earlier "yes": the record keeps the order."""
    from harness import augustus

    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    address = "t-1/20261126T093000Z"
    augustus.accept(box, address, note="yes")
    augustus.accept(box, address, decision=archivum.REJECTED, note="on second look, no")
    marks = archivum.decisions(box.root, address)
    assert [item.decision for item in marks] == [archivum.ACCEPTED, archivum.REJECTED]
    entry = archivum.find(box.root, address)
    assert entry is not None
    assert "rejected" in archivum.acceptance_line(entry, marks)
    assert "(2 marks in all)" in archivum.acceptance_line(entry, marks)


def test_the_same_mark_twice_is_not_a_second_decision(tmp_path: Path) -> None:
    from harness import augustus

    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    address = "t-1/20261126T093000Z"
    augustus.accept(box, address, note="yes")
    augustus.accept(box, address, note="yes")
    assert len(archivum.decisions(box.root, address)) == 1
    events = [
        json.loads(line)
        for path in (box.root / "fasti").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    accepted = [item for item in events if item["type"] == "task.accepted"]
    assert len(accepted) == 1 and accepted[0]["actor"] == "owner"


def test_a_mark_about_another_diff_is_named(tmp_path: Path) -> None:
    """The mark names its diff: an address reused for another change cannot inherit it."""
    from harness import augustus

    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    address = "t-1/20261126T093000Z"
    augustus.accept(box, address)
    entry_path = box.root / "archivum" / "t-1" / "20261126T093000Z" / "entry.json"
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    entry["diff"]["hash"] = "ffff"
    entry_path.write_text(json.dumps(entry), encoding="utf-8")
    changed = archivum.find(box.root, address)
    assert changed is not None
    line = archivum.acceptance_line(changed, archivum.decisions(box.root, address))
    assert "the mark is about another diff" in line


def test_history_shows_the_owner_s_mark_or_names_its_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    _, record = prepared(tmp_path, box)
    archivum.write(box.root, record, stamp())
    base = ["--castra", str(box.root.parent), "history", box.name]
    assert main(base) == 0
    out = capsys.readouterr().out
    assert "not accepted — the owner has not decided yet" in out
    assert main([*base, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"][0]["acceptance"] == []
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "accept",
                box.name,
                "t-1/20261126T093000Z",
                "--note",
                "yes, this one",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(base) == 0
    out = capsys.readouterr().out
    assert "accepted" in out and "yes, this one" in out
    events = (box.root / "fasti" / f"{datetime.now(UTC):%Y-%m-%d}.jsonl").read_text()
    assert "task.accepted" in events
