"""The context pack: what a task is told about the box before the model is asked anything.

A model that must find its own bearings spends tokens on questions the box can already answer:
which frame this work rests on, which finished tasks touched the same paths and why they were
picked, and where the full records are. The pack answers those in a few hundred characters and
records itself, so the record of a run can say exactly what the model was told.

The pack is assembled by code, never by the model, and it is bounded in two ways: a fixed number
of records and a fixed number of characters. Everything that does not fit is counted and named
in the pack itself — a context that silently lost half of its records would be worse than an
empty one, because the model would not know what it had not been told.

The pack is a *pointer* document. Each selected record is one line ending with the record's own
file; a model opens a record only when it decides the work, instead of re-reading the history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes
from . import caesar, records
from . import frame as frames

SCHEMA_VERSION = 1
MAX_SELECTED = 6
LIMIT_CHARS = 4000
"""The pack's size cap in characters: roughly a thousand tokens, not a second task."""
STAMP = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True, slots=True)
class Selection:
    """One past record and the fact that put it in front of the model."""

    entry: records.Entry
    reason: str


@dataclass(frozen=True, slots=True)
class Pack:
    """The context as it was given: frame, records, pointers, the size spent, what was left out."""

    task: str
    built_at: str
    path: Path
    frame: frames.Standing
    digest: records.Digest
    selected: tuple[Selection, ...]
    lines: tuple[str, ...]
    limit: int
    omitted: dict[str, int]

    @property
    def records_digest(self) -> str:
        return self.digest.digest

    def json(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task": self.task,
            "built_at": self.built_at,
            "path": str(self.path),
            "frame": self.frame.json(),
            "records": {
                "digest": self.digest.digest,
                "total": self.digest.total,
                "selected": [
                    {
                        "task": item.entry.task,
                        "when": item.entry.when,
                        "state": item.entry.state,
                        "verdict": item.entry.verdict,
                        "kind": item.entry.kind,
                        "reason": item.reason,
                        "record": item.entry.record,
                    }
                    for item in self.selected
                ],
                "omitted": dict(self.omitted),
                "skipped": [
                    {"task": task, "reason": reason} for task, reason in self.digest.skipped
                ],
            },
            "budget": {
                "chars": _chars(self.lines),
                "limit": self.limit,
                "selected": len(self.selected),
                "max": MAX_SELECTED,
            },
            "lines": list(self.lines),
        }


def build(
    box: boxes.Box, task: str, paths: tuple[str, ...], when: datetime | None = None
) -> Pack:
    """Assemble the pack for a task: the frame it rests on and the records near its paths.

    The task is named by its id and its declared patterns, not passed as an object: selecting
    records needs nothing else, so the pack cannot drift with the orchestrator's task type.
    """
    identifier = task
    patterns = paths
    moment = (when or datetime.now(UTC)).replace(microsecond=0)
    digest = records.build(box)
    standing = frames.standing(box)
    selected: list[Selection] = []
    omitted = {
        "own": 0,
        "no_overlap": 0,
        "over_limit": 0,
        "over_budget": 0,
        "older": digest.omitted,
        "unreadable": len(digest.skipped),
    }
    for entry in digest.entries:
        if entry.task == identifier:
            omitted["own"] += 1
            continue
        reason = _reason(entry, patterns)
        if reason is None:
            omitted["no_overlap"] += 1
            continue
        selected.append(Selection(entry, reason))
    if len(selected) > MAX_SELECTED:
        omitted["over_limit"] = len(selected) - MAX_SELECTED
        selected = selected[:MAX_SELECTED]
    lines, dropped = _lines(standing, digest, selected)
    omitted["over_budget"] = dropped
    leftovers = _left_out(omitted)
    if leftovers:
        lines.append(f"left out: {leftovers}")
    return Pack(
        task=identifier,
        built_at=moment.isoformat().replace("+00:00", "Z"),
        path=box.root / "artifacts" / identifier / f"pack-{moment.strftime(STAMP)}.json",
        frame=standing,
        digest=digest,
        selected=tuple(selected),
        lines=tuple(lines),
        limit=LIMIT_CHARS,
        omitted=omitted,
    )


def write(pack: Pack) -> Pack:
    """Write the pack next to the task's other artifacts, atomically."""
    pack.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pack.path.with_name(pack.path.name + ".tmp")
    tmp.write_text(json.dumps(pack.json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(pack.path)
    return pack


def payload(pack: Pack) -> dict[str, object]:
    """The pack as the executor request carries it: the lines the model sees, and the pointer."""
    return {
        "schema_version": SCHEMA_VERSION,
        "task": pack.task,
        "path": str(pack.path),
        "records_digest": pack.records_digest,
        "selected": [item.entry.task for item in pack.selected],
        "omitted": dict(pack.omitted),
        "lines": list(pack.lines),
    }


def line(pack: Pack) -> str:
    """One line for the dispatch record and the outbox."""
    left = sum(pack.omitted.values())
    if not pack.selected:
        return (
            "no past record touches the declared paths "
            f"({pack.digest.total} in all, digest {records.short(pack.records_digest)})"
        )
    return (
        f"{len(pack.selected)} past records by paths "
        f"(digest {records.short(pack.records_digest)}), {left} left out"
    )


def render(pack: Pack) -> list[str]:
    """The pack as a person reads it in a dispatch record."""
    lines = [
        f"context    {line(pack)}",
        f"pack       {pack.path}",
    ]
    for selection in pack.selected:
        lines.append(f"- {selection.entry.task}  {selection.reason}")
    return lines


def _lines(
    standing: frames.Standing, digest: records.Digest, selected: list[Selection]
) -> tuple[list[str], int]:
    head = [
        "CONTEXT (assembled by code from the box's records, not by a search):",
        f"frame: {standing.line()}",
    ]
    if selected:
        head.append(
            f"records: {len(selected)} of {digest.total} selected by the declared paths "
            f"(digest {records.short(digest.digest)}); each line ends with its record file — "
            "open one only if it decides the work"
        )
    else:
        head.append(
            "records: no record touches the declared paths "
            f"({digest.total} in all, digest {records.short(digest.digest)})"
        )
    used = _chars(head)
    dropped = 0
    for index, selection in enumerate(selected):
        raw = selection.entry.pack_line(selection.reason)
        if used + len(raw) + 1 > LIMIT_CHARS:
            dropped = len(selected) - index
            break
        head.append(raw)
        used += len(raw) + 1
    return head, dropped


def _left_out(omitted: dict[str, int]) -> str:
    parts: list[str] = []
    if omitted["no_overlap"] == 1:
        parts.append("1 record does not touch the declared paths")
    elif omitted["no_overlap"]:
        parts.append(f"{omitted['no_overlap']} records do not touch the declared paths")
    if omitted["over_limit"]:
        parts.append(f"{omitted['over_limit']} more than the selection cap")
    if omitted["over_budget"]:
        parts.append(f"{omitted['over_budget']} more than the size cap")
    if omitted["older"]:
        parts.append(f"{omitted['older']} older than the digest cap")
    if omitted["unreadable"] == 1:
        parts.append("1 record could not be read")
    elif omitted["unreadable"]:
        parts.append(f"{omitted['unreadable']} records could not be read")
    if omitted["own"]:
        parts.append("this task's own earlier record")
    return "; ".join(parts)


def _reason(entry: records.Entry, patterns: tuple[str, ...]) -> str | None:
    """Why this record belongs next to this task: a touched path first, a scope second."""
    for path in entry.changed:
        for pattern in patterns:
            if caesar.matches(pattern, path):
                return f"touches {path}"
    for declared in entry.paths:
        for pattern in patterns:
            if _overlap(declared, pattern):
                return f"declared scope overlaps {declared}"
    return None


def _overlap(left: str, right: str) -> bool:
    """Two scope patterns are near each other when their fixed roots are prefixes."""
    first, second = _root(left), _root(right)
    if not first or not second:
        return False
    return first == second or first.startswith(second + "/") or second.startswith(first + "/")


def _root(pattern: str) -> str:
    kept: list[str] = []
    for part in pattern.split("/"):
        if "*" in part:
            break
        kept.append(part)
    return "/".join(kept)


def _chars(lines: list[str] | tuple[str, ...]) -> int:
    return sum(len(line) + 1 for line in lines)
