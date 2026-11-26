"""The context pack: what a task is told about the box before the model is asked anything.

A model that must find its own bearings spends tokens on questions the box can already answer:
which frame this work rests on, which finished tasks touched the same paths and why they were
picked, and where the full records are. The pack answers those in a few hundred characters and
records itself, so the record of a run can say exactly what the model was told.

The pack is assembled by code, never by the model, and it is bounded in two ways: a fixed number
of records and a fixed number of characters. Everything that does not fit is counted and named
in the pack itself — a context that silently lost half of its records would be worse than an
empty one, because the model would not know what it had not been told.

The pack is a *consequence* document, not an inventory. What the model is told is one line: how
many recorded changes touch these paths, which one came last, and that undoing a recorded decision
is the owner's call. The full selection, its reasons and the addresses stay in the pack's JSON for
a person, because a list of pointers is what the model never opened (measured 2026-11-26,
``pilot/context-block/``: the answer with that list was byte for byte the answer without it).
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
LIMIT_CHARS = 600
"""The block's size cap in characters: one line of consequence, not a second task."""
STAMP = "%Y%m%dT%H%M%SZ"
NAMED_REASON = "named by the task"
"""The reason a record the task named itself carries, as opposed to one its paths found."""


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
    named: tuple[str, ...] = ()
    """The addresses the task named itself, in the order it named them."""

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
                "named": list(self.named),
                "selected": [
                    {
                        "task": item.entry.task,
                        "address": item.entry.address,
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
    box: boxes.Box,
    task: str,
    paths: tuple[str, ...],
    when: datetime | None = None,
    named: tuple[str, ...] = (),
) -> Pack:
    """Assemble the pack for a task: the frame it rests on and the records near its paths.

    The task is named by its id and its declared patterns, not passed as an object: selecting
    records needs nothing else, so the pack cannot drift with the orchestrator's task type.

    ``named`` is the task speaking for itself: addresses from ``pantheon records`` that the
    task wants in front of the model even when its paths do not point at them. An address the
    digest does not have is a refusal, not a quiet omission — a task that asked for history
    and did not get it would be exactly the quiet pass the box exists to prevent.
    """
    identifier = task
    patterns = paths
    moment = (when or datetime.now(UTC)).replace(microsecond=0)
    digest = records.build(box)
    standing = frames.standing(box)
    named_selected: list[Selection] = []
    for address in dict.fromkeys(named):
        entry = records.lookup(digest, address)
        if entry is None:
            raise boxes.HarnessError(
                f"{task}: the task names a record the digest does not have: {address!r}; "
                "`pantheon records` lists the addresses it has (an address older than the "
                "digest cap is not addressable)"
            )
        named_selected.append(Selection(entry, NAMED_REASON))
    if len(named_selected) > MAX_SELECTED:
        raise boxes.HarnessError(
            f"{task}: {len(named_selected)} records are named, and the pack carries at most "
            f"{MAX_SELECTED}: name the ones that decide the work"
        )
    taken = {selection.entry.address for selection in named_selected}
    picked: list[Selection] = []
    omitted = {
        "own": 0,
        "no_overlap": 0,
        "over_limit": 0,
        "over_budget": 0,
        "older": digest.omitted,
        "unreadable": len(digest.skipped),
    }
    for entry in digest.entries:
        if entry.address in taken:
            continue
        if entry.task == identifier:
            omitted["own"] += 1
            continue
        reason = _reason(entry, patterns)
        if reason is None:
            omitted["no_overlap"] += 1
            continue
        picked.append(Selection(entry, reason))
    matched = len(named_selected) + len(picked)
    room = MAX_SELECTED - len(named_selected)
    if len(picked) > room:
        omitted["over_limit"] = len(picked) - room
        picked = picked[:room]
    selected = named_selected + picked
    lines = _lines(standing, digest, selected, matched, len(named_selected))
    return Pack(
        task=identifier,
        built_at=moment.isoformat().replace("+00:00", "Z"),
        path=box.root / "tabularium" / identifier / f"fascis-{moment.strftime(STAMP)}.json",
        frame=standing,
        digest=digest,
        selected=tuple(selected),
        lines=tuple(lines),
        limit=LIMIT_CHARS,
        omitted=omitted,
        named=tuple(selection.entry.address for selection in named_selected),
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
        "named": list(pack.named),
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
    named = len(pack.named)
    by_paths = len(pack.selected) - named
    if named and by_paths:
        head = f"{named} named record(s) + {by_paths} past records by paths"
    elif named:
        head = f"{named} named record(s)"
    else:
        head = f"{by_paths} past records by paths"
    return f"{head} (digest {records.short(pack.records_digest)}), {left} left out"


def render(pack: Pack) -> list[str]:
    """The pack as a person reads it in a dispatch record."""
    lines = [
        f"context    {line(pack)}",
        f"pack       {pack.path}",
    ]
    for selection in pack.selected:
        lines.append(f"- {selection.entry.task}  {selection.reason}  {selection.entry.address}")
    return lines


def _lines(
    standing: frames.Standing,
    digest: records.Digest,
    selected: list[Selection],
    matched: int,
    named: int,
) -> list[str]:
    """What the model is told: the frame it rests on and one line of consequence."""
    head = [
        "CONTEXT (assembled by code from the box's records, not by a search):",
        f"frame: {standing.line()}",
    ]
    budget = LIMIT_CHARS - _chars(head) - 1
    return [*head, _history(digest, selected, matched, named, budget)]


def _history(
    digest: records.Digest,
    selected: list[Selection],
    matched: int,
    named: int,
    budget: int,
) -> str:
    """One line: how much history there is, which decision came last, and whose call undoing it is.

    The line names the newest record and says how many touch these paths; an inventory of six
    pointers is what the model never opened. The address and the full record stay in the pack's
    JSON, where a person reads them.
    """
    if not selected:
        return (
            "history: no recorded change touches these paths "
            f"({digest.total} records in the box) — nothing recorded to keep in mind"
        )
    chosen = [item for item in selected if item.reason == NAMED_REASON] or list(selected)
    newest = max(chosen, key=lambda item: item.entry.when)
    changes = "1 recorded change" if matched == 1 else f"{matched} recorded changes"
    verdict = newest.entry.verdict or "not checked"
    if named:
        lead = f"history: {changes}; the task named {newest.entry.task} ({verdict}) "
    else:
        lead = f"history: {changes} on these paths; the latest is {newest.entry.task} ({verdict}) "
    head = lead + '"'
    tail = (
        '". Reversing a recorded decision is the owner\'s call: if the task does not ask for it, '
        "do not undo it"
    )
    room = max(budget - len(head) - len(tail) - 1, 20)
    return head + _short(newest.entry.goal, room) + tail


def _short(text: str, room: int) -> str:
    """The subject cut at a sentence end, else at a word, so a long goal cannot grow the block."""
    flat = " ".join((text or "").split())
    if not flat:
        return "no goal"
    if len(flat) <= room:
        return flat
    window = flat[: room - 1]
    end = window.rfind(". ")
    if end >= 20:
        return window[: end + 1]
    cut = window.rsplit(" ", 1)[0] if " " in window else window
    return (cut or window).rstrip(" ,;:-—") + "…"


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
