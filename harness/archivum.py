"""The change archive: what was changed here, why, and what proved it.

A commit says what changed. Nothing in it says why the change was made, which check proved it,
what the validator noticed, or which attempts were rejected on the way — and that is exactly what
is missing when two fixes start to conflict. The archive keeps it, one entry per closed change.

It is a projection of the dispatch record, written when the change is committed (ADR-045): the
repository stays the truth about the code, the archive is the truth about reasons, and it lives in
the box, never next to the code. Nothing writes here by hand.

Three readers ask by path — refactoring wants a module's map, a conflict wants to know which
changes touched both sides, the reproducer wants to know what was changed here before and why —
so the archive keeps an index by path. The index is rebuilt from the entries every time, because
an index that can disagree with its entries is a second truth about the archive.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
"""Bumped when an entry grows a field that older readers cannot read correctly."""

DIRECTORY = "archivum"
"""The box's folder for it: one sub-folder per task, one per closed change inside."""

ENTRY_FILE = "entry.json"
DIFF_FILE = "change.diff"
INDEX_FILE = "index.json"
SNAPSHOT_FILE = "task.json"
"""The frozen task beside the records: the goal and the two checks are read from it, not guessed."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One closed change with its reason: the contract, the proof, the rejects and the bill."""

    address: str
    task: str
    at: str
    kind: str
    goal: str
    expected: str
    verdict: str
    scope: tuple[str, ...]
    touched: tuple[str, ...]
    commit: str
    subject: str
    contract: dict[str, object] | None
    checks: dict[str, object]
    validator: dict[str, object] | None
    rejected: tuple[dict[str, object], ...]
    cost_cents: float | None
    seconds: float | None
    diff_hash: str | None

    def json(self) -> dict[str, object]:
        return {
            "address": self.address,
            "task": self.task,
            "at": self.at,
            "kind": self.kind,
            "goal": self.goal,
            "expected": self.expected,
            "verdict": self.verdict,
            "scope": list(self.scope),
            "touched": list(self.touched),
            "commit": self.commit,
            "subject": self.subject,
            "contract": self.contract,
            "checks": self.checks,
            "validator": self.validator,
            "rejected": [dict(item) for item in self.rejected],
            "cost_cents": self.cost_cents,
            "seconds": self.seconds,
            "diff_hash": self.diff_hash,
        }


def write(box_root: Path, record: dict[str, object], when: datetime) -> Path | None:
    """Project one dispatch record into the archive; ``None`` when nothing was closed.

    The trigger is the close, not the push (ADR-045): a closed task has its commit, and a change
    that was never committed has no reason to be in the archive. Everything is read from the
    record and from the files beside it; nothing is passed in from the run that produced it.
    """
    if str(record.get("state")) != "closed":
        return None
    commit = record.get("commit")
    if not isinstance(commit, dict) or not str(commit.get("sha") or ""):
        return None
    task = str(record.get("task") or "")
    if not task:
        return None
    stamp = when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    address = f"{task}/{stamp}"
    folder = box_root / DIRECTORY / task / stamp
    folder.mkdir(parents=True, exist_ok=True)
    frozen = _frozen(box_root, task)
    checks = _checks(record, frozen)
    diff = _diff(record, folder)
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "address": address,
        "task": task,
        "kind": str(record.get("kind") or ""),
        "role": str(record.get("role") or "executor"),
        "at": when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "goal": str(frozen.get("goal") or ""),
        "expected": str(frozen.get("expected") or ""),
        "contract": record.get("contract"),
        "scope": [str(item) for item in _list(record.get("paths"))],
        "verdict": str(record.get("state") or ""),
        "commit": {
            "sha": str(commit.get("sha") or ""),
            "subject": str(commit.get("subject") or ""),
        },
        "checks": checks,
        "validator": _validator(record),
        "rejected": _rejected(box_root, task, stamp),
        "cost_cents": _cost(record),
        "seconds": _seconds(record),
        "touched": _touched(record),
        "diff": diff,
    }
    (folder / ENTRY_FILE).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_index(box_root)
    return folder


def entries(box_root: Path) -> tuple[Entry, ...]:
    """Every archived change, newest first, read from the index the entries build."""
    index = _read_index(box_root)
    found: list[Entry] = []
    for address in index:
        item = _read_entry(box_root / DIRECTORY / Path(*address.split("/")) / ENTRY_FILE)
        if item is not None:
            found.append(item)
    return tuple(reversed(found))


def for_paths(box_root: Path, patterns: tuple[str, ...]) -> tuple[Entry, ...]:
    """The changes that touched any of these paths, newest first.

    A pattern matches a concrete changed path, or the scope a change declared: a module's map is
    asked as ``src/app/**``, and a change whose contract declared that scope belongs in it even
    when the diff landed one file below.
    """
    if not patterns:
        return entries(box_root)
    index = _read_index(box_root)
    matching: list[str] = []
    for address in index:
        item = _read_entry(box_root / DIRECTORY / Path(*address.split("/")) / ENTRY_FILE)
        if item is None:
            continue
        if _matches(item, patterns):
            matching.append(address)
    found = [_entry(box_root, address) for address in matching]
    return tuple(reversed([item for item in found if item is not None]))


def find(box_root: Path, address: str) -> Entry | None:
    """One entry by ``<task>/<stamp>``, or ``None``: the archive does not invent addresses."""
    return _entry(box_root, address)


def diff(box_root: Path, address: str) -> Path | None:
    """The stored change for an entry, read on demand: a map does not carry the diffs."""
    path = box_root / DIRECTORY / Path(*address.split("/")) / DIFF_FILE
    return path if path.is_file() else None


def render(entry: Entry) -> list[str]:
    """One entry as a person reads it: what, where, why, what proved it, what was rejected."""
    lines = [
        f"{entry.address}  {entry.kind or 'task'}  {entry.at}",
        f"goal      {entry.goal or 'not recorded'}",
        f"commit    {entry.commit or 'not recorded'}  {entry.subject}",
        f"scope     {', '.join(entry.scope) or 'nothing declared'}",
        f"touched   {', '.join(entry.touched) or 'nothing recorded'}",
        f"verdict   {entry.verdict}",
    ]
    if entry.contract:
        for key in ("symptom", "expected", "visible", "boundaries", "missing"):
            value = entry.contract.get(key)
            if value:
                lines.append(f"{key:<9} {value}")
    hidden = entry.checks.get("hidden")
    if isinstance(hidden, dict):
        lines.append(
            f"hidden    {hidden.get('status') or 'unknown'}"
            f"  check {hidden.get('check_hash') or 'not hashed'}"
            f"  report {hidden.get('report_hash') or 'not hashed'}"
        )
    visible = entry.checks.get("visible")
    if isinstance(visible, dict):
        lines.append(
            f"visible   {visible.get('status') or 'unknown'}"
            f"  check {visible.get('check_hash') or 'not hashed'}"
        )
    if entry.validator:
        lines.append(
            f"validator {entry.validator.get('model') or '?'}"
            f"  {entry.validator.get('status') or 'not recorded'}"
        )
    for item in entry.rejected:
        lines.append(
            f"rejected  {item.get('address')}  {item.get('class') or 'no class'}"
            f"  {item.get('why') or 'no reason recorded'}"
        )
    money = entry.cost_cents
    lines.append(
        f"cost      {'not reported' if money is None else f'{money:g} cents'}"
        f"  {entry.seconds if entry.seconds is not None else '?'} s"
    )
    if entry.diff_hash:
        lines.append(f"diff      {entry.diff_hash}  reads with `pantheon history --diff`")
    return lines


def payload(entry: Entry) -> dict[str, object]:
    """The same entry for a machine."""
    return entry.json()


def _frozen(box_root: Path, task: str) -> dict[str, object]:
    path = box_root / "tabularium" / task / SNAPSHOT_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {str(key): value for key, value in data.items()} if isinstance(data, dict) else {}


def _checks(record: dict[str, object], frozen: dict[str, object]) -> dict[str, object]:
    """Both checks with a hash of the check itself and of what it produced.

    The hashes are what makes the archive useful years later: a report whose check has changed is
    not the same evidence, and an entry that only names the file cannot tell the two apart.
    """
    verify = record.get("verify")
    verify = verify if isinstance(verify, dict) else {}
    report_text = ""
    report = str(verify.get("report") or "")
    if report:
        try:
            report_text = Path(report).read_text(encoding="utf-8")
        except OSError:
            report_text = ""
    hidden: dict[str, object] = {
        "status": str(verify.get("status") or ""),
        "check_hash": _sha(frozen.get("repro")),
        "report": report or None,
        "report_hash": _sha(report_text) if report_text else None,
    }
    own = record.get("self_check")
    visible: dict[str, object] | None = None
    if isinstance(own, dict) or frozen.get("self_check"):
        own = own if isinstance(own, dict) else {}
        visible = {
            "status": str(own.get("status") or "not run"),
            "exit_code": own.get("exit_code"),
            "check_hash": _sha(frozen.get("self_check")),
            "tail": str(own.get("tail") or "") or None,
        }
    return {"hidden": hidden, "visible": visible}


def _validator(record: dict[str, object]) -> dict[str, object] | None:
    verify = record.get("verify")
    review = verify.get("review") if isinstance(verify, dict) else None
    if not isinstance(review, dict):
        return None
    return {
        "model": review.get("model"),
        "status": review.get("status"),
        "proposed": review.get("proposed"),
        "confirmed": review.get("confirmed"),
        "error": review.get("error"),
    }


def _rejected(box_root: Path, task: str, stamp: str) -> tuple[dict[str, object], ...]:
    """The attempts that were paid for and did not become the change, oldest first.

    Read from the records beside this one: each carries its class and the change it proposed.
    The one phrase "why" is the record's own words — the last line that names a failed check —
    never a summary written here.
    """
    directory = box_root / "tabularium" / task
    found: list[dict[str, object]] = []
    for path in sorted(directory.glob("dispatch-*.json")):
        if path.stem.removeprefix("dispatch-") == stamp:
            continue
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or str(data.get("state")) == "closed":
            continue
        found.append(
            {
                "address": f"{task}/{path.stem.removeprefix('dispatch-')}",
                "state": data.get("state"),
                "class": data.get("failure_class"),
                "diff_hash": _attempt_hash(data),
                "why": _reason(data),
            }
        )
    return tuple(found)


def _attempt_hash(record: dict[str, object]) -> str | None:
    for key in ("attempt", "executor"):
        item = record.get(key)
        if not isinstance(item, dict):
            continue
        folder = item.get("directory")
        if not isinstance(folder, str) or not folder:
            continue
        candidate = Path(folder) / DIFF_FILE
        try:
            return _sha(candidate.read_text(encoding="utf-8"))
        except OSError:
            continue
    return None


def _reason(record: dict[str, object]) -> str:
    notes = record.get("notes")
    if not isinstance(notes, list):
        return ""
    for line in reversed([str(item) for item in notes]):
        if "failed" in line or "refused" in line:
            return line
    return ""


def _diff(record: dict[str, object], folder: Path) -> dict[str, object]:
    """Copy the applied change in: the archive must not depend on a folder cleaned later."""
    source = _attempt_diff(record)
    if source is None:
        return {}
    target = folder / DIFF_FILE
    shutil.copyfile(source, target)
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"file": DIFF_FILE, "hash": _sha(text), "bytes": target.stat().st_size}


def _attempt_diff(record: dict[str, object]) -> Path | None:
    attempt = record.get("attempt")
    if isinstance(attempt, dict):
        folder = attempt.get("directory")
        if isinstance(folder, str) and folder:
            candidate = Path(folder) / DIFF_FILE
            if candidate.is_file():
                return candidate
    return None


def _touched(record: dict[str, object]) -> list[str]:
    attempt = record.get("attempt")
    if isinstance(attempt, dict):
        return [str(item) for item in _list(attempt.get("paths"))]
    return []


def _cost(record: dict[str, object]) -> float | None:
    cents = 0.0
    known = False
    executor = record.get("executor")
    if isinstance(executor, dict):
        usage = executor.get("usage")
        value = usage.get("cost_cents") if isinstance(usage, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cents += float(value)
            known = True
    verify = record.get("verify")
    review = verify.get("review") if isinstance(verify, dict) else None
    value = review.get("cost_cents") if isinstance(review, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        cents += float(value)
        known = True
    return round(cents, 4) if known else None


def _seconds(record: dict[str, object]) -> float | None:
    timings = record.get("timings")
    total = timings.get("total") if isinstance(timings, dict) else None
    return float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None


def _write_index(box_root: Path) -> None:
    """Rebuild the path index from the entries: the index is a projection, never a source."""
    changes: list[dict[str, object]] = []
    paths: dict[str, list[str]] = {}
    for entry_path in sorted((box_root / DIRECTORY).glob(f"*/*/{ENTRY_FILE}")):
        try:
            data: Any = json.loads(entry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        address = str(data.get("address") or "")
        if not address:
            continue
        changes.append(
            {
                "address": address,
                "task": data.get("task"),
                "at": data.get("at"),
                "kind": data.get("kind"),
                "verdict": data.get("verdict"),
                "goal": data.get("goal"),
                "commit": (data.get("commit") or {}).get("sha")
                if isinstance(data.get("commit"), dict)
                else None,
                "scope": _list(data.get("scope")),
                "touched": _list(data.get("touched")),
            }
        )
        for name in {*_list(data.get("touched")), *_list(data.get("scope"))}:
            paths.setdefault(str(name), []).append(address)
    changes.sort(key=lambda item: str(item.get("at") or ""))
    text = json.dumps(
        {"schema_version": SCHEMA_VERSION, "changes": changes, "paths": paths},
        indent=2,
        ensure_ascii=False,
    )
    (box_root / DIRECTORY / INDEX_FILE).write_text(text + "\n", encoding="utf-8")


def _read_index(box_root: Path) -> list[str]:
    """The addresses in the index, oldest first; a missing index is rebuilt from the entries."""
    path = box_root / DIRECTORY / INDEX_FILE
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if not (box_root / DIRECTORY).is_dir():
            return []
        _write_index(box_root)
        return _read_index(box_root)
    if not isinstance(data, dict):
        return []
    changes = data.get("changes")
    if not isinstance(changes, list):
        return []
    return [str(item.get("address")) for item in changes if isinstance(item, dict)]


def _read_entry(path: Path) -> Entry | None:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("address"):
        return None
    commit_data = data.get("commit")
    commit = commit_data if isinstance(commit_data, dict) else {}
    checks_data = data.get("checks")
    checks = checks_data if isinstance(checks_data, dict) else {}
    rejected_data = data.get("rejected")
    rejected = rejected_data if isinstance(rejected_data, list) else []
    diff_data = data.get("diff")
    diff = diff_data if isinstance(diff_data, dict) else {}
    return Entry(
        address=str(data.get("address")),
        task=str(data.get("task") or ""),
        at=str(data.get("at") or ""),
        kind=str(data.get("kind") or ""),
        goal=str(data.get("goal") or ""),
        expected=str(data.get("expected") or ""),
        verdict=str(data.get("verdict") or ""),
        scope=tuple(str(item) for item in _list(data.get("scope"))),
        touched=tuple(str(item) for item in _list(data.get("touched"))),
        commit=str(commit.get("sha") or ""),
        subject=str(commit.get("subject") or ""),
        contract=data.get("contract") if isinstance(data.get("contract"), dict) else None,
        checks={str(key): value for key, value in checks.items()},
        validator=data.get("validator") if isinstance(data.get("validator"), dict) else None,
        rejected=tuple(item for item in rejected if isinstance(item, dict)),
        cost_cents=_number(data.get("cost_cents")),
        seconds=_number(data.get("seconds")),
        diff_hash=str(diff.get("hash") or "") or None,
    )


def _entry(box_root: Path, address: str) -> Entry | None:
    parts = address.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return _read_entry(box_root / DIRECTORY / parts[0] / parts[1] / ENTRY_FILE)


def _matches(entry: Entry, patterns: tuple[str, ...]) -> bool:
    names = [*entry.touched, *entry.scope]
    for name in names:
        for pattern in patterns:
            if fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(pattern, name):
                return True
    return False


def _sha(value: object) -> str | None:
    """A hash of the check or the evidence; ``None`` when there is nothing to hash."""
    if value is None:
        return None
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, ensure_ascii=False)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []
