"""The box's records, digested: what happened here, one bounded line per task.

A task's artifacts are the evidence of what it did: the frozen contract, the gate's attempt,
the verification report, the commit. That is the right size for a person asking "what did this
task change" and the wrong size for a model asking "what already happened around these paths":
reading every record to answer it costs more than the work itself. So the code writes the cheap
answer first — which tasks ran, with what verdict, what they touched, and where the full record
is kept.

Three properties make the digest usable instead of decorative:

* **Deterministic.** The same records give the same bytes and the same digest; ``built_at`` is
  outside the hash. A digest that changes without a record changing cannot be compared to the
  one a run recorded.
* **Bounded.** A fixed number of entries, paths and goal characters; a record that does not fit
  is counted, not silently dropped.
* **Honest at the edges.** A record that cannot be read is listed with the reason, and a task
  that never produced a dispatch record is not invented: the digest says how many records were
  found, not how many somebody hoped for.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes

SCHEMA_VERSION = 1
RECORDS = Path("tabularium") / "commentarii"
DIGEST_FILE = "digest.json"
RESERVED = frozenset(
    {"commentarii", "forma", "expeditio", "aurelius", "fluxus", "studia", "mutatio", "spec"}
)
"""Directories under ``tabularium/`` that are records of other things, not tasks."""
MAX_ENTRIES = 200
MAX_PATHS = 5
MAX_GOAL = 160
STAMP = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True, slots=True)
class Entry:
    """One task as the digest keeps it: the verdict, the paths, and where the record is."""

    task: str
    address: str
    when: str
    state: str
    verdict: str
    kind: str
    role: str
    goal: str
    paths: tuple[str, ...]
    total_paths: int
    changed: tuple[str, ...]
    total_changed: int
    commit: str | None
    record: str

    def line(self) -> str:
        """One line for a person reading ``pantheon records``."""
        return " | ".join(
            (
                self.task,
                self.when,
                self.state,
                self.verdict or "not checked",
                self.kind or "no kind",
                self.goal or "no goal",
            )
        )

    def json(self) -> dict[str, object]:
        return {
            "task": self.task,
            "address": self.address,
            "when": self.when,
            "state": self.state,
            "verdict": self.verdict,
            "kind": self.kind,
            "role": self.role,
            "goal": self.goal,
            "paths": list(self.paths),
            "total_paths": self.total_paths,
            "changed": list(self.changed),
            "total_changed": self.total_changed,
            "commit": self.commit,
            "record": self.record,
        }


@dataclass(frozen=True, slots=True)
class Digest:
    """The records as they stand: entries, the hash over them, and what was left out."""

    root: Path
    entries: tuple[Entry, ...]
    digest: str
    built_at: str
    total: int
    omitted: int
    skipped: tuple[tuple[str, str], ...]

    @property
    def count(self) -> int:
        return len(self.entries)

    def json(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "built_at": self.built_at,
            "digest": self.digest,
            "count": self.count,
            "total": self.total,
            "omitted": self.omitted,
            "skipped": [{"task": task, "reason": reason} for task, reason in self.skipped],
            "entries": [entry.json() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class Stored:
    """A digest read back from the box: enough to say whether the file is current."""

    path: Path
    digest: str
    count: int
    built_at: str


def build(box: boxes.Box) -> Digest:
    """Read every finished task's newest dispatch record and hash what came out.

    A record that cannot be read is not a reason to fail the whole digest: it is a line in it.
    A directory with no dispatch record (a refused task, an interrupted one) is not a record
    and is not invented here — its absence is visible in the ledger, not in this file.
    """
    base = box.root / "tabularium"
    entries: list[Entry] = []
    skipped: list[tuple[str, str]] = []
    found = 0
    if base.is_dir():
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or directory.name in RESERVED:
                continue
            newest = _newest(directory)
            if newest is None:
                continue
            found += 1
            try:
                entries.append(_entry(box, directory.name, newest))
            except (OSError, ValueError, KeyError) as error:
                skipped.append((directory.name, f"unreadable record: {error}"))
    entries.sort(key=lambda item: (item.when, item.task), reverse=True)
    omitted = max(0, len(entries) - MAX_ENTRIES)
    kept = tuple(entries[:MAX_ENTRIES])
    return Digest(
        root=box.root,
        entries=kept,
        digest=_digest(kept),
        built_at=_now(),
        total=found,
        omitted=omitted,
        skipped=tuple(skipped),
    )


def write(box: boxes.Box, digest: Digest | None = None) -> Digest:
    """Write the digest where the box keeps derived records, atomically."""
    digest = digest or build(box)
    path = box.root / RECORDS / DIGEST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(digest.json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(path)
    return digest


def read(box: boxes.Box) -> Stored | None:
    """The digest file as written, or ``None`` when the box has none yet.

    A file that does not parse is an error, not a silent "not written": the difference between
    "the code never ran" and "the code wrote something unreadable" is the difference a person
    needs when the memory of the box stops being trustworthy.
    """
    path = box.root / RECORDS / DIGEST_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"the records digest {path} does not parse: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(f"{path} is not a schema {SCHEMA_VERSION} records digest")
    digest = data.get("digest")
    if not isinstance(digest, str) or not digest:
        raise boxes.HarnessError(f"{path} names no digest: a digest of nothing is not one")
    count = data.get("count")
    return Stored(
        path=path,
        digest=digest,
        count=count if isinstance(count, int) else 0,
        built_at=str(data.get("built_at", "")),
    )


def state(box: boxes.Box) -> tuple[Digest, str]:
    """The fresh digest and whether the stored one still matches: ``fresh``, ``stale``, ``none``."""
    fresh = build(box)
    stored = read(box)
    if stored is None:
        return fresh, "none"
    return fresh, "fresh" if stored.digest == fresh.digest else "stale"


def render(digest: Digest) -> list[str]:
    """The digest as a person reads it: what is there, what was left out, where each record is."""
    lines = [
        f"records    {digest.count} tasks, digest {short(digest.digest)}, written {digest.built_at}"
    ]
    if digest.omitted:
        lines.append(f"omitted    {digest.omitted} older than the {MAX_ENTRIES}-entry cap")
    for task, reason in digest.skipped:
        lines.append(f"skipped    {task}: {reason}")
    if not digest.entries and not digest.skipped:
        lines.append("nothing has run in this box yet: there is no record to digest")
    for entry in digest.entries:
        lines.append(f"- {entry.line()}  {entry.address} -> {entry.record}")
    return lines


def short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


ADDRESS_TASK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ADDRESS_STAMP = re.compile(r"^\d{8}T\d{6}Z$")


def parse_address(text: str) -> tuple[str, str] | None:
    """Split ``<task>/<stamp>`` into its parts; anything else is not an address."""
    task, _, stamp = text.rpartition("/")
    if not task or not ADDRESS_TASK.match(task) or not ADDRESS_STAMP.match(stamp):
        return None
    return task, stamp


def lookup(digest: Digest, address: str) -> Entry | None:
    """The entry an address names, or ``None``: the digest is the index, not the filesystem."""
    return next((entry for entry in digest.entries if entry.address == address), None)


def _newest(directory: Path) -> Path | None:
    files = sorted(directory.glob("dispatch-*.json"))
    return files[-1] if files else None


def _entry(box: boxes.Box, task: str, record: Path) -> Entry:
    data = json.loads(record.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the record is not a JSON object")
    stamp = record.name.removeprefix("dispatch-").removesuffix(".json")
    when = _iso(stamp)
    contract = _contract(box, task)
    paths = _strings(contract.get("allowed_paths") if contract else data.get("paths"))
    changed = _strings(_nested(data, "attempt", "paths"))
    commit = _nested(data, "commit", "sha")
    try:
        pointer = record.relative_to(box.root).as_posix()
    except ValueError:
        pointer = str(record)
    return Entry(
        task=task,
        address=f"{task}/{stamp}",
        when=when,
        state=str(data.get("state") or "unknown"),
        verdict=str(_nested(data, "verify", "status") or ""),
        kind=str(data.get("kind") or (contract.get("kind") if contract else "") or ""),
        role=str(data.get("role") or "executor"),
        goal=_clip(str((contract.get("goal") if contract else "") or ""), MAX_GOAL),
        paths=paths[:MAX_PATHS],
        total_paths=len(paths),
        changed=changed[:MAX_PATHS],
        total_changed=len(changed),
        commit=commit if isinstance(commit, str) and commit else None,
        record=pointer,
    )


def _contract(box: boxes.Box, task: str) -> dict[str, object] | None:
    path = box.root / "mandata" / f"{task}.contract.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data


def _nested(data: dict[str, object], *keys: str) -> object | None:
    current: object = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _clip(text: str, limit: int) -> str:
    """Cut at a word, never mid-word: this text is what a model is shown as the subject."""
    if len(text) <= limit:
        return text
    window = text[: limit - 1]
    cut = window.rsplit(" ", 1)[0] if " " in window else window
    return (cut or window).rstrip(" ,;:-—") + "…"


def _iso(stamp: str) -> str:
    moment = datetime.strptime(stamp, STAMP).replace(tzinfo=UTC)
    return moment.isoformat().replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(entries: tuple[Entry, ...]) -> str:
    """One hash over the kept entries: same records, same answer, whatever the clock says."""
    body = "\n".join(
        json.dumps(entry.json(), ensure_ascii=False, sort_keys=True) for entry in entries
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
