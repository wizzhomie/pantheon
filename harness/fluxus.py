"""The stream: a brief, a feed of attempts, and a queue of tasks waiting for a verdict.

A task answers "did this change pass". A stream answers "what are we building, and what has
already been tried": the brief names the milestone the work serves, the feed keeps the attempts
including the ones that were abandoned, and the queue lets a task be put down and read later.
The abandoned approach is a record with a reason, because a chat that remembers the labyrinth is
not evidence — the next agent or the owner a week later would repeat the same attempt.

The feed is append-only. "Read" is an event too, so a verdict cannot be quietly counted twice:
what stands after the newest read event is what nobody has read yet.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import augustus, rails
from . import box as boxes

SCHEMA_VERSION = 1
DIR = Path("tabularium") / "fluxus"
BRIEF_FILE = "brief.toml"
FEED_FILE = "feed.jsonl"
QUEUE_DIR = "queue"
LOCK_FILE = "lock"
KINDS = ("brief", "queued", "verdict", "discarded", "read")
MAX_FEED = 20


@dataclass(frozen=True, slots=True)
class Brief:
    """What is being built now, why, and which milestone of the frame it serves."""

    at: str
    by: str
    milestone: str
    what: str
    why: str


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of the feed: an attempt, a verdict, a brief, or the moment verdicts were read."""

    at: str
    kind: str
    task: str | None = None
    state: str | None = None
    reason: str = ""
    milestone: str | None = None
    record: str | None = None

    def json(self) -> dict[str, object]:
        return {
            "at": self.at,
            "kind": self.kind,
            "task": self.task,
            "state": self.state,
            "reason": self.reason,
            "milestone": self.milestone,
            "record": self.record,
        }


def read_brief(box: boxes.Box) -> Brief | None:
    """The brief on disk, or ``None`` when the stream has not said what it is building."""
    path = box.root / DIR / BRIEF_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise boxes.HarnessError(f"cannot read the brief {path}: {error}") from error
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"the brief {path} does not parse: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(f"the brief {path} is not schema_version {SCHEMA_VERSION}")
    return Brief(
        at=_text(data.get("at"), "at", path),
        by=_text(data.get("by"), "by", path),
        milestone=_text(data.get("milestone"), "milestone", path),
        what=_text(data.get("what"), "what", path),
        why=_text(data.get("why"), "why", path),
    )


def write_brief(
    box: boxes.Box,
    *,
    milestone: str,
    what: str,
    why: str,
    by: str = "owner",
    when: datetime | None = None,
) -> Brief:
    """Say what the stream is building. The brief names a milestone the accepted frame declares."""
    if not what.strip():
        raise boxes.HarnessError("a brief says what is being built: `--brief` is empty")
    if not why.strip():
        raise boxes.HarnessError("a brief says why it is being built: `--why` is empty")
    state = rails.standing(box)
    if state.state != "approved":
        gap = state.gap() or state.line()
        raise boxes.HarnessError(
            f"the flow serves a milestone, and the frame is not accepted: {gap}"
        )
    current = rails.read(box.project)
    if current is None or current.milestone(milestone) is None:
        known = "none" if current is None else ", ".join(item.id for item in current.milestones)
        raise boxes.HarnessError(
            f"the brief serves milestone {milestone!r}, which the frame does not declare; "
            f"known milestones: {known}"
        )
    stamp = _stamp((when or datetime.now(UTC)).replace(microsecond=0))
    path = box.root / DIR / BRIEF_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        f"schema_version = {SCHEMA_VERSION}",
        f"at = {_quote(stamp)}",
        f"by = {_quote(by)}",
        f"milestone = {_quote(milestone)}",
        f"what = {_quote(what.strip())}",
        f"why = {_quote(why.strip())}",
    ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    brief = Brief(at=stamp, by=by, milestone=milestone, what=what.strip(), why=why.strip())
    _append(box, Entry(at=stamp, kind="brief", milestone=milestone, reason=brief.what))
    return brief


def enqueue(
    box: boxes.Box,
    task_path: Path,
    *,
    reason: str = "",
    when: datetime | None = None,
) -> tuple[str, Path]:
    """Copy a task into the queue, frozen, after the rails admit it.

    The task is copied rather than referenced: editing the original afterwards must not change
    what is waiting. A declared change is copied beside it and the copy names that file.
    """
    task = augustus.read_task(task_path)
    check = rails.check(box, milestone=task.milestone, outside=task.outside_plan)
    if check.refusal is not None:
        raise boxes.HarnessError(f"the task is not admitted by the rails: {check.refusal}")
    stamp = _stamp((when or datetime.now(UTC)).replace(microsecond=0))
    directory = box.root / DIR / QUEUE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    change: Path | None = None
    if task.change is not None:
        try:
            data = task.change.read_bytes()
        except OSError as error:
            raise boxes.HarnessError(f"cannot read the patch {task.change}: {error}") from error
        change = directory / f"{stamp}-{task.id}.diff"
        change.write_bytes(data)
    header = [
        "# Queued by `pantheon flow --enqueue`: a copy, frozen until `--next` takes it.",
    ]
    text = "\n".join(header) + "\n" + augustus.task_text(task, change=change)
    path = directory / f"{stamp}-{task.id}.toml"
    path.write_text(text, encoding="utf-8")
    _append(
        box,
        Entry(
            at=stamp,
            kind="queued",
            task=task.id,
            state="queued",
            reason=reason,
            milestone=task.milestone,
            record=str(path),
        ),
    )
    return task.id, path


def queued(box: boxes.Box) -> tuple[Path, ...]:
    """The queue, oldest first: the name starts with the stamp, so the order is the write order."""
    directory = box.root / DIR / QUEUE_DIR
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("*.toml")))


def next_queued(box: boxes.Box) -> Path | None:
    """The task `--next` would run, or ``None``."""
    found = queued(box)
    return found[0] if found else None


def discard(
    box: boxes.Box,
    task_id: str,
    reason: str | None,
    *,
    by: str = "owner",
    when: datetime | None = None,
) -> Entry:
    """Record that an attempt was abandoned, and why: the labyrinth is part of the evidence."""
    if not (reason and reason.strip()):
        raise boxes.HarnessError(
            "a discarded attempt says why: without the reason the next attempt repeats it"
        )
    stamp = _stamp((when or datetime.now(UTC)).replace(microsecond=0))
    dropped = _drop_from_queue(box, task_id)
    if not dropped and not (box.root / "tabularium" / task_id).is_dir():
        raise boxes.HarnessError(
            f"no task {task_id!r} in this box: only an attempt that exists can be abandoned"
        )
    entry = Entry(
        at=stamp,
        kind="discarded",
        task=task_id,
        state="discarded" if not dropped else "dropped",
        reason=reason.strip(),
        record=None,
    )
    _append(box, entry)
    return entry


def record_verdict(
    box: boxes.Box, result: augustus.Dispatch, *, when: datetime | None = None
) -> Entry:
    """Put a finished run in the feed: the verdict is read later, not necessarily now."""
    stamp = _stamp((when or datetime.now(UTC)).replace(microsecond=0))
    entry = Entry(
        at=stamp,
        kind="verdict",
        task=result.task.id,
        state=result.state,
        reason="; ".join(result.notes),
        milestone=result.task.milestone,
        record=None if result.outbox is None else str(result.outbox),
    )
    _append(box, entry)
    return entry


def mark_read(box: boxes.Box, *, by: str = "owner", when: datetime | None = None) -> Entry:
    """A read event: every verdict before it is read, every verdict after it is not."""
    entry = Entry(
        at=_stamp((when or datetime.now(UTC)).replace(microsecond=0)),
        kind="read",
        reason=by,
    )
    _append(box, entry)
    return entry


def feed(box: boxes.Box) -> tuple[Entry, ...]:
    """Every line of the feed, oldest first. A line that does not parse stops the reading."""
    path = box.root / DIR / FEED_FILE
    if not path.is_file():
        return ()
    entries: list[Entry] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as error:
            raise boxes.HarnessError(
                f"the flow feed {path} line {number} does not parse: {error}"
            ) from error
        if not isinstance(data, dict) or data.get("kind") not in KINDS:
            raise boxes.HarnessError(f"the flow feed {path} line {number} is not an entry")
        entries.append(
            Entry(
                at=str(data.get("at", "")),
                kind=str(data.get("kind")),
                task=_optional(data.get("task")),
                state=_optional(data.get("state")),
                reason=str(data.get("reason", "")),
                milestone=_optional(data.get("milestone")),
                record=_optional(data.get("record")),
            )
        )
    return tuple(entries)


def unread(box: boxes.Box) -> tuple[Entry, ...]:
    """The verdicts nobody has read yet: those after the newest read event."""
    entries = feed(box)
    start = 0
    for index, entry in enumerate(entries):
        if entry.kind == "read":
            start = index + 1
    return tuple(entry for entry in entries[start:] if entry.kind == "verdict")


def lock(box: boxes.Box) -> Path:
    """Take the stream's lock for one run: two drains must not write the same tree at once."""
    directory = box.root / DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOCK_FILE
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        held = path.read_text(encoding="utf-8").strip() or "no owner written"
        raise boxes.HarnessError(
            f"another flow run holds the lock at {path} ({held}); remove the file if it is stale"
        ) from error
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(f"pid {os.getpid()} at {_stamp(datetime.now(UTC).replace(microsecond=0))}\n")
    return path


def unlock(path: Path) -> None:
    path.unlink(missing_ok=True)


def render(box: boxes.Box, *, limit: int = MAX_FEED) -> list[str]:
    """The stream as a person reads it: the brief, the queue, the unread verdicts, the feed."""
    brief = read_brief(box)
    waiting = queued(box)
    pending = unread(box)
    lines: list[str] = []
    if brief is None:
        lines.append(
            'flow       no brief yet — `pantheon flow --brief "what" --why "why" --milestone M5`'
        )
    else:
        lines.append(f"flow       {brief.milestone} — {brief.what}")
        lines.append(f"why        {brief.why}")
        lines.append(f"briefed    {brief.at} by {brief.by}")
    if waiting:
        names = ", ".join(_read_id(path) for path in waiting)
        lines.append(f"queue      {len(waiting)} waiting: {names}")
    else:
        lines.append("queue      empty")
    if pending:
        lines.append(f"unread     {len(pending)} verdict(s) nobody has read")
    else:
        lines.append("unread     nothing")
    entries = feed(box)
    if entries:
        lines.append("feed       newest first")
        for entry in reversed(entries[-limit:]):
            lines.append(f"  {entry.kind:<9} {entry.at}  {_entry_brief(entry)}")
    return lines


def payload(box: boxes.Box, *, limit: int = MAX_FEED) -> dict[str, object]:
    brief = read_brief(box)
    waiting = queued(box)
    return {
        "brief": None
        if brief is None
        else {
            "at": brief.at,
            "by": brief.by,
            "milestone": brief.milestone,
            "what": brief.what,
            "why": brief.why,
        },
        "queue": [{"task": _read_id(path), "path": str(path)} for path in waiting],
        "unread": [entry.json() for entry in unread(box)],
        "feed": [entry.json() for entry in feed(box)[-limit:]],
    }


def _append(box: boxes.Box, entry: Entry) -> None:
    path = box.root / DIR / FEED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry.json(), ensure_ascii=False) + "\n")


def _drop_from_queue(box: boxes.Box, task_id: str) -> bool:
    """Remove a queued task by id, and the patch queued beside it; say whether it was there."""
    for path in queued(box):
        try:
            found = augustus.read_task(path)
        except boxes.HarnessError:
            continue
        if found.id != task_id:
            continue
        patch = path.with_suffix(".diff")
        patch.unlink(missing_ok=True)
        path.unlink()
        return True
    return False


def _read_id(path: Path) -> str:
    return augustus.read_task(path).id


def _entry_brief(entry: Entry) -> str:
    parts = [part for part in (entry.task, entry.state) if part]
    if entry.milestone and entry.kind != "queued":
        parts.append(entry.milestone)
    text = "  ".join(parts) if parts else "—"
    if entry.reason:
        text += f"  {entry.reason}"
    if entry.record:
        text += f"  {entry.record}"
    return text


def _text(value: object, key: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise boxes.HarnessError(f"the brief {path} has no {key}")
    return value.strip()


def _optional(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")
