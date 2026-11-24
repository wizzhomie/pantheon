"""The frame: the basis a project declares, and the record that says it was accepted.

The frame is a short, named set of files: the architecture model (which packages exist and
which may depend on which), the path policy (what each kind of task may write), and the
requirements the checks judge against. A named list, not a glob: a plan or a brief added to
``.pantheon/`` later must not silently become part of what the owner accepted.

Approving is not a field in a file. It is a box record carrying the digest of the exact bytes
that were accepted, written once and never edited — so "approved" cannot drift away from the
disk, and a frame that changed after the approval is a fact with two digest to compare, not a
memory. A dispatch still closes; it says which frame it rested on and whether that frame was
the one that was accepted.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import aurelius, caesar
from . import box as boxes

SCHEMA_VERSION = 1
FRAME_FILES = (
    ".pantheon/architecture.toml",
    ".pantheon/kinds.toml",
    ".trajan/context.toml",
)
"""The frame, by name. Missing entries are listed, not ignored: an absent file is a fact."""
RECORD_TYPE = "frame.approval"
RECORDS = Path("artifacts") / "frame"
"""Where approvals live in the box: evidence with a date, like every other box record."""


@dataclass(frozen=True, slots=True)
class FileDigest:
    """One frame file and the bytes that were read from it."""

    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class Frame:
    """The frame as it is on disk right now, and what is missing from it."""

    project: Path
    files: tuple[FileDigest, ...]
    digest: str
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Approval:
    """A record a person wrote: this digest was the frame they accepted."""

    path: Path
    at: str
    digest: str
    by: str
    note: str
    files: tuple[FileDigest, ...]


@dataclass(frozen=True, slots=True)
class Standing:
    """How the frame on disk stands against the newest approval: approved, changed, never, empty."""

    state: str
    digest: str
    approval: Approval | None = None
    missing: tuple[str, ...] = ()

    def gap(self) -> str | None:
        """What a verdict about this project does not cover, or ``None`` when it is covered."""
        if self.state == "empty":
            return (
                "the project declares no frame: no model, no path policy and no requirements "
                "file, so there was nothing to approve and nothing to check against"
            )
        if self.state == "never":
            return (
                "the frame was never approved: this change rests on a model nobody signed off "
                "(`pantheon approve` makes that record)"
            )
        if self.state == "changed" and self.approval is not None:
            return (
                "the frame changed after it was approved (approved "
                f"{_short(self.approval.digest)}, now {_short(self.digest)}): the basis of this "
                "change is not the one that was accepted"
            )
        return None

    def line(self) -> str:
        """The standing as one line for a person."""
        if self.state == "approved" and self.approval is not None:
            return f"approved {self.approval.at} by {self.approval.by} ({_short(self.digest)})"
        if self.state == "changed" and self.approval is not None:
            return (
                f"changed since approval ({_short(self.approval.digest)} -> {_short(self.digest)})"
            )
        if self.state == "never":
            return "never approved"
        return "nothing declared"

    def json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "digest": self.digest,
            "missing": list(self.missing),
            "approved": None
            if self.approval is None
            else {
                "at": self.approval.at,
                "by": self.approval.by,
                "digest": self.approval.digest,
                "note": self.approval.note,
                "record": str(self.approval.path),
            },
        }


def read_frame(project: Path) -> Frame:
    """Read the named frame files; a file that cannot be read stops the run, not the digest."""
    files: list[FileDigest] = []
    missing: list[str] = []
    for name in FRAME_FILES:
        try:
            raw = (project / name).read_bytes()
        except FileNotFoundError:
            missing.append(name)
            continue
        except OSError as error:
            raise boxes.HarnessError(
                f"cannot read the frame file {project / name}: {error}"
            ) from error
        files.append(FileDigest(path=name, digest=_sha(raw)))
    return Frame(project=project, files=tuple(files), digest=_digest(files), missing=tuple(missing))


def package_issues(project: Path) -> tuple[str, ...]:
    """What stops the frame from being a package: an existing file has to parse as its format.

    Approving a model nothing can read would record a basis that cannot be checked, so the
    approval is refused instead. A file that is simply absent is not an issue — ``missing``
    says which ones are, and the owner can approve a frame without every part of it.
    """
    issues: list[str] = []
    model = project / FRAME_FILES[0]
    if model.is_file():
        try:
            aurelius.read_model(model)
        except boxes.HarnessError as error:
            issues.append(str(error))
    kinds = project / FRAME_FILES[1]
    if kinds.is_file():
        try:
            caesar.read_kinds(project)
        except boxes.HarnessError as error:
            issues.append(str(error))
    context = project / FRAME_FILES[2]
    if context.is_file():
        try:
            tomllib.loads(context.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            issues.append(f"{context} does not parse: {error}")
    return tuple(issues)


def approve(
    box: boxes.Box,
    *,
    by: str,
    note: str = "",
    when: datetime | None = None,
) -> Approval:
    """Write the record that says this state of the frame is the basis a person accepts.

    The record is written once and is not edited afterwards: a new acceptance is a new record,
    so the history of what was approved stays readable instead of being overwritten. Approving
    an empty frame is refused — an approval of nothing would look like a cleared basis.
    """
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    current = read_frame(box.project)
    if not current.files:
        raise boxes.HarnessError(
            f"there is no frame to approve in {box.project}: none of "
            f"{', '.join(FRAME_FILES)} exists, so an approval would name nothing"
        )
    issues = package_issues(box.project)
    if issues:
        raise boxes.HarnessError(
            "the frame does not read as a package, so there is nothing to approve yet: "
            + "; ".join(issues)
        )
    directory = box.root / RECORDS
    directory.mkdir(parents=True, exist_ok=True)
    path = _free_path(directory, stamp)
    record = {
        "schema_version": SCHEMA_VERSION,
        "type": RECORD_TYPE,
        "at": _stamp(stamp),
        "digest": current.digest,
        "files": [{"path": item.path, "digest": item.digest} for item in current.files],
        "missing": list(current.missing),
        "by": by,
        "note": note,
        "base": caesar.head(box.project),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_event(box, by, note, path, stamp)
    return Approval(
        path=path,
        at=_stamp(stamp),
        digest=current.digest,
        by=by,
        note=note,
        files=current.files,
    )


def _free_path(directory: Path, when: datetime) -> Path:
    """Two approvals in one second are two records, not one silently overwriting the other."""
    path = directory / f"approval-{when:%Y%m%dT%H%M%SZ}.json"
    counter = 2
    while path.exists():
        path = directory / f"approval-{when:%Y%m%dT%H%M%SZ}-{counter}.json"
        counter += 1
    return path


def latest(box: boxes.Box) -> Approval | None:
    """The newest approval record, or ``None``. An unreadable record is an error, not a skip."""
    directory = box.root / RECORDS
    if not directory.is_dir():
        return None
    records = sorted(directory.glob("approval-*.json"))
    if not records:
        return None
    path = records[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"the frame approval {path} does not parse: {error}") from error
    if not isinstance(data, dict) or data.get("type") != RECORD_TYPE:
        raise boxes.HarnessError(f"{path} is not a frame approval record")
    digest = data.get("digest")
    if not isinstance(digest, str) or not digest:
        raise boxes.HarnessError(f"{path} names no digest: an approval of nothing is not one")
    return Approval(
        path=path,
        at=str(data.get("at", "")),
        digest=digest,
        by=str(data.get("by", "")),
        note=str(data.get("note", "")),
        files=tuple(
            FileDigest(path=str(item.get("path", "")), digest=str(item.get("digest", "")))
            for item in data.get("files", [])
            if isinstance(item, dict)
        ),
    )


def standing(box: boxes.Box) -> Standing:
    """The frame on disk against the newest approval: the answer a dispatch records."""
    current = read_frame(box.project)
    approval = latest(box)
    if not current.files:
        return Standing("empty", current.digest, approval, current.missing)
    if approval is None:
        return Standing("never", current.digest, None, current.missing)
    if approval.digest != current.digest:
        return Standing("changed", current.digest, approval, current.missing)
    return Standing("approved", current.digest, approval, current.missing)


def render_approval(approval: Approval, frame: Frame | None = None) -> list[str]:
    """An approval as a person reads it: what was accepted, who said so, where it is kept."""
    lines = [
        f"approved   {approval.at} by {approval.by}",
        f"digest     {approval.digest}",
        f"record     {approval.path}",
    ]
    for item in approval.files:
        lines.append(f"file       {item.path}  {item.digest}")
    if frame is not None:
        for name in frame.missing:
            lines.append(f"missing    {name}")
    if approval.note:
        lines.append(f"note       {approval.note}")
    return lines


def _digest(files: list[FileDigest] | tuple[FileDigest, ...]) -> str:
    """One digest over the whole frame: the same bytes give the same answer, in any order."""
    body = "\n".join(f"{item.path} {item.digest}" for item in sorted(files, key=lambda i: i.path))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_event(box: boxes.Box, by: str, note: str, record: Path, when: datetime) -> None:
    """One ledger event per approval: a record the timeline cannot find is half a record."""
    ledger = box.root / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    try:
        link = str(record.relative_to(box.root))
    except ValueError:
        link = str(record)
    event = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": "frame.approved",
        "at": _stamp(when),
        "task": None,
        "actor": by,
        "reason": note,
        "links": [link],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
