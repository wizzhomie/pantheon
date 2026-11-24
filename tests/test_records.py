"""The records digest: one bounded line per task, the same bytes for the same records.

These tests hold what makes the digest usable as memory instead of decoration: a task appears
with its verdict and a pointer to its full record, the same records always give the same bytes,
a record that cannot be read is named rather than dropped, and a digest file that is stale or
unreadable is a fact about the box, not a quiet "nothing to see".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import records
from harness.cli import main


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "probe"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "note.md").write_text("note\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return boxes.new_box(tmp_path / "boxes", project)


def write_record(
    box: boxes.Box,
    task: str,
    stamp: str = "20261124T120000Z",
    *,
    state: str = "closed",
    verdict: str | None = "pass",
    kind: str = "docs",
    goal: str = "write the note",
    paths: tuple[str, ...] = ("docs/**",),
    changed: tuple[str, ...] | None = ("docs/note.md",),
    commit: str | None = "abc123",
) -> Path:
    directory = box.root / "artifacts" / task
    directory.mkdir(parents=True, exist_ok=True)
    contract = box.root / "tasks" / f"{task}.contract.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "schema_version = 1\n"
        f'task = "{task}"\n'
        f'kind = "{kind}"\n'
        f'goal = "{goal}"\n'
        f"allowed_paths = {json.dumps(list(paths))}\n",
        encoding="utf-8",
    )
    record = directory / f"dispatch-{stamp}.json"
    record.write_text(
        json.dumps(
            {
                "task": task,
                "kind": kind,
                "paths": list(paths),
                "state": state,
                "role": "executor",
                "verify": None if verdict is None else {"status": verdict},
                "attempt": None if changed is None else {"paths": list(changed)},
                "commit": None if commit is None else {"status": "made", "sha": commit},
            }
        ),
        encoding="utf-8",
    )
    return record


def run(box: boxes.Box, *args: str) -> int:
    return main(["--boxes", str(box.root.parent), *args])


def test_a_finished_task_appears_with_its_verdict_and_a_pointer_to_its_record(
    tmp_path: Path,
) -> None:
    box = make_box(tmp_path)
    record = write_record(box, "t-1")
    digest = records.build(box)
    assert digest.count == 1 and digest.total == 1
    entry = digest.entries[0]
    assert entry.task == "t-1"
    assert (entry.state, entry.verdict, entry.kind) == ("closed", "pass", "docs")
    assert entry.goal == "write the note"
    assert entry.changed == ("docs/note.md",)
    assert entry.commit == "abc123"
    assert box.root / entry.record == record, "the line points at the file, not at a promise"
    assert "t-1" in entry.line() and "pass" in entry.line()


def test_newest_first_and_the_same_records_give_the_same_digest(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "older", "20261124T100000Z")
    write_record(box, "newer", "20261124T130000Z")
    first = records.build(box)
    second = records.build(box)
    assert [entry.task for entry in first.entries] == ["newer", "older"]
    assert first.digest == second.digest, "built_at is outside the hash"
    write_record(box, "third", "20261124T140000Z")
    assert records.build(box).digest != first.digest


def test_a_record_that_cannot_be_read_is_listed_not_swallowed(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "good", "20261124T100000Z")
    broken = box.root / "artifacts" / "broken" / "dispatch-20261124T110000Z.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json", encoding="utf-8")
    digest = records.build(box)
    assert [entry.task for entry in digest.entries] == ["good"]
    assert digest.total == 2
    assert digest.skipped and digest.skipped[0][0] == "broken"
    assert "unreadable record" in digest.skipped[0][1]
    assert "skipped" in "\n".join(records.render(digest))


def test_entries_beyond_the_cap_are_counted_not_dropped_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records, "MAX_ENTRIES", 2)
    box = make_box(tmp_path)
    for index in range(3):
        write_record(box, f"t-{index}", f"20261124T1{index}0000Z")
    digest = records.build(box)
    assert digest.count == 2 and digest.total == 3
    assert digest.omitted == 1
    assert "omitted" in "\n".join(records.render(digest))


def test_a_long_goal_and_many_paths_are_clipped_and_counted(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(
        box,
        "t-long",
        goal="word " * 60,
        paths=tuple(f"docs/{index}.md" for index in range(9)),
        changed=tuple(f"docs/{index}.md" for index in range(9)),
    )
    entry = records.build(box).entries[0]
    assert len(entry.goal) <= records.MAX_GOAL and entry.goal.endswith("…")
    assert len(entry.paths) == records.MAX_PATHS
    assert entry.total_paths == 9
    assert len(entry.changed) == records.MAX_PATHS
    assert entry.total_changed == 9


def test_the_digest_is_written_read_back_and_a_new_record_makes_it_stale(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-1", "20261124T100000Z")
    written = records.write(box)
    stored = records.read(box)
    assert stored is not None
    assert stored.digest == written.digest and stored.count == 1
    assert stored.path == box.root / records.RECORDS / records.DIGEST_FILE
    assert records.state(box)[1] == "fresh"
    write_record(box, "t-2", "20261124T110000Z")
    _fresh, state = records.state(box)
    assert state == "stale"
    records.write(box)
    _fresh, state = records.state(box)
    assert state == "fresh"


def test_an_unreadable_digest_file_is_an_error_not_an_empty_memory(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    path = box.root / records.RECORDS / records.DIGEST_FILE
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="does not parse"):
        records.read(box)


def test_the_command_writes_the_digest_and_an_empty_box_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    assert run(box, "records", "probe") == 0
    out = capsys.readouterr().out
    assert "nothing has run in this box yet" in out
    assert (box.root / records.RECORDS / records.DIGEST_FILE).is_file()
    write_record(box, "t-1")
    assert run(box, "records", "probe", "--json") == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["count"] == 1 and answer["entries"][0]["task"] == "t-1"
    assert answer["digest"].startswith("sha256:")


def test_status_names_the_digest_state_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-1")
    assert run(box, "status", "probe") == 0
    out = capsys.readouterr().out
    assert "records    1 task, digest not written yet" in out
    assert not (box.root / records.RECORDS / records.DIGEST_FILE).exists()
    records.write(box)
    assert run(box, "status", "probe") == 0
    assert "records    1 task, digest fresh" in capsys.readouterr().out
