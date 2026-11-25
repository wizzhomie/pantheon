"""The stream: a brief, a feed of attempts, and a queue of tasks waiting for a verdict.

What these tests hold: the brief serves a milestone the accepted frame declares, a queued task
is a frozen copy rather than a reference, an abandoned attempt keeps its reason, and "read" is
an event so a verdict cannot be counted twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import augustus, fluxus, rails
from harness import box as boxes
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

TASK = """\
schema_version = 1
id = "t-flow"
kind = "docs"
paths = ["docs/**"]
milestone = "M1"
"""


def make_box(tmp_path: Path, *, approved: bool = True) -> boxes.Box:
    project = tmp_path / "probe"
    (project / ".pantheon").mkdir(parents=True)
    (project / ".pantheon" / "rails.toml").write_text(FRAME, encoding="utf-8")
    box = boxes.new_box(tmp_path / "castra", project)
    if approved:
        rails.approve(box, by="owner")
    return box


def write_task(tmp_path: Path, body: str = TASK) -> Path:
    path = tmp_path / "t-flow.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_brief_serves_a_milestone_the_frame_declares(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    brief = fluxus.write_brief(
        box, milestone="M1", what="the stream", why="tasks need a home", by="owner"
    )
    assert brief.milestone == "M1"
    again = fluxus.read_brief(box)
    assert again is not None and again.what == "the stream"
    entries = fluxus.feed(box)
    assert entries[-1].kind == "brief" and entries[-1].milestone == "M1"


def test_a_brief_is_refused_until_the_frame_is_accepted(tmp_path: Path) -> None:
    box = make_box(tmp_path, approved=False)
    with pytest.raises(boxes.HarnessError, match="not accepted"):
        fluxus.write_brief(box, milestone="M1", what="x", why="y")


def test_a_brief_for_an_unknown_milestone_is_refused(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError, match="M9"):
        fluxus.write_brief(box, milestone="M9", what="x", why="y")


def test_a_queued_task_is_a_frozen_copy(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    source = write_task(tmp_path)
    task_id, path = fluxus.enqueue(box, source, reason="first attempt")
    assert task_id == "t-flow" and path.is_file()
    assert augustus.read_task(path).id == "t-flow"
    source.write_text(TASK.replace('"docs/**"', '"src/**"'), encoding="utf-8")
    queued = augustus.read_task(path)
    assert queued.paths == ("docs/**",), "editing the original must not change the queue"
    assert [entry.kind for entry in fluxus.feed(box)] == ["queued"]


def test_a_task_without_a_milestone_is_not_queued(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    source = write_task(tmp_path, TASK.replace('milestone = "M1"\n', ""))
    with pytest.raises(boxes.HarnessError, match="not admitted"):
        fluxus.enqueue(box, source)


def test_the_queue_is_oldest_first(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    first = write_task(tmp_path)
    fluxus.enqueue(box, first)
    second = tmp_path / "t-two.toml"
    second.write_text(TASK.replace('"t-flow"', '"t-two"'), encoding="utf-8")
    fluxus.enqueue(box, second)
    assert [augustus.read_task(path).id for path in fluxus.queued(box)] == ["t-flow", "t-two"]


def test_an_abandoned_attempt_needs_a_reason(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError, match="says why"):
        fluxus.discard(box, "t-flow", None)


def test_discarding_a_queued_task_drops_it(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    fluxus.enqueue(box, write_task(tmp_path))
    entry = fluxus.discard(box, "t-flow", "the approach did not converge")
    assert entry.state == "dropped"
    assert fluxus.queued(box) == ()
    assert fluxus.feed(box)[-1].reason == "the approach did not converge"


def test_discarding_a_task_that_never_existed_is_refused(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    with pytest.raises(boxes.HarnessError, match="no task"):
        fluxus.discard(box, "t-ghost", "because")


def test_a_read_event_splits_the_verdicts(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    task = augustus.read_task(write_task(tmp_path))
    fluxus.record_verdict(box, augustus.Dispatch(task=task, state="closed", stages=()))
    fluxus.record_verdict(box, augustus.Dispatch(task=task, state="returned", stages=()))
    assert len(fluxus.unread(box)) == 2
    fluxus.mark_read(box)
    assert fluxus.unread(box) == ()


def test_a_broken_feed_line_stops_the_reading(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    directory = box.root / "tabularium" / "fluxus"
    directory.mkdir(parents=True)
    (directory / "feed.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="line 1"):
        fluxus.feed(box)


def test_the_lock_keeps_two_drains_apart(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    held = fluxus.lock(box)
    with pytest.raises(boxes.HarnessError, match="holds the lock"):
        fluxus.lock(box)
    fluxus.unlock(held)
    fluxus.unlock(fluxus.lock(box))


def test_the_cli_shows_the_stream(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box = make_box(tmp_path)
    assert main(["--castra", str(box.root.parent), "flow", box.name]) == 0
    assert "no brief yet" in capsys.readouterr().out
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "flow",
                box.name,
                "--brief",
                "the stream",
                "--why",
                "tasks need a home",
                "--milestone",
                "M1",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "M1 — the stream" in out and "why        tasks need a home" in out
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "flow",
                box.name,
                "--enqueue",
                str(write_task(tmp_path)),
                "--json",
            ]
        )
        == 0
    )
    answer = json.loads(capsys.readouterr().out)
    assert answer["task"] == "t-flow"
