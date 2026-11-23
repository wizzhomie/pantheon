"""A unified diff, parsed into the lines a reviewer is allowed to talk about.

The reviewer sees a change, not a repository: only the changed files, only their changed
lines and a little context around them. That is a cost decision and an honesty decision at
the same time — a model asked to review "the repository" will invent findings about code it
never read.

The parser is deliberately small and strict. It takes the output of ``git diff`` (or any
unified diff), keeps the new-file line numbers, refuses paths that climb out of the project,
and stops at a size ceiling instead of silently growing the prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import ConfigDict, Field

from ..contracts.base import StrictModel

MAX_DIFF_BYTES = 200_000
"""Above this the diff is truncated; an unbounded prompt is an unbounded bill."""

MAX_FILES = 50
"""How many files may appear in one review; the rest are listed as not reviewed."""

FileStatus = Literal["added", "modified", "deleted", "renamed"]


class DiffLine(StrictModel):
    """One line of the new file: added by the change, or context around it.

    Whitespace is *not* stripped here, unlike every other contract: indentation is the line.
    A parser that trims it turns Python into a language where the quote never matches.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=False, validate_default=True
    )

    number: int = Field(ge=1, description="line number in the new file")
    text: str
    added: bool = Field(description="True when the change introduced the line")


class FileChange(StrictModel):
    """One changed file and the lines of it the change touched or surrounded."""

    path: str = Field(min_length=1)
    status: FileStatus
    old_path: str | None = None
    lines: list[DiffLine] = Field(default_factory=list)
    removed: int = Field(default=0, ge=0, description="how many lines the change removed")


class Diff(StrictModel):
    """The parts of a change a reviewer can be shown, and nothing else."""

    files: list[FileChange] = Field(default_factory=list)
    truncated: bool = Field(default=False, description="a cap cut the diff short")

    @property
    def paths(self) -> list[str]:
        return [change.path for change in self.files]

    @property
    def added_lines(self) -> int:
        return sum(1 for change in self.files for line in change.lines if line.added)

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_diff(text: str, *, max_bytes: int = MAX_DIFF_BYTES, max_files: int = MAX_FILES) -> Diff:
    """Read a unified diff into files and numbered lines.

    Anything the reviewer cannot cite is dropped rather than guessed: binary files, mode
    changes without hunks, and paths outside the project.
    """
    truncated = len(text) > max_bytes
    if truncated:
        text = text[:max_bytes]
    files: list[FileChange] = []
    current: _Builder | None = None

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            _finish(files, current)
            if len(files) >= max_files:
                return Diff(files=files, truncated=True)
            old, new = _paths_from_header(raw)
            current = _Builder(path=new, old_path=old)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.status = "added"
            continue
        if raw.startswith("deleted file mode"):
            current.status = "deleted"
            continue
        if raw.startswith("rename from "):
            current.old_path = raw.removeprefix("rename from ").strip()
            current.status = "renamed"
            continue
        if raw.startswith("rename to "):
            current.path = raw.removeprefix("rename to ").strip()
            current.status = "renamed"
            continue
        if raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.binary = True
            continue
        if raw.startswith("@@"):
            parsed = _hunk_start(raw)
            if parsed is not None:
                current.new_line, current.old_line = parsed
            continue
        if raw.startswith(("--- ", "+++ ", "index ", "similarity index", "dissimilarity index")):
            continue
        _consume_line(current, raw)

    _finish(files, current)
    return Diff(files=files, truncated=truncated)


class _Builder:
    """Mutable during parsing; converted to a frozen :class:`FileChange` at the end."""

    def __init__(self, path: str, old_path: str | None) -> None:
        self.path = _safe_path(path) or ""
        self.old_path = _safe_path(old_path) if old_path else None
        self.status: FileStatus = "modified"
        self.binary = False
        self.lines: list[DiffLine] = []
        self.removed = 0
        self.new_line = 1
        self.old_line = 1


def _consume_line(current: _Builder, raw: str) -> None:
    if not raw:
        return
    marker, text = raw[0], raw[1:]
    if marker == "+":
        current.lines.append(DiffLine(number=current.new_line, text=text, added=True))
        current.new_line += 1
    elif marker == "-":
        current.removed += 1
        current.old_line += 1
    elif marker == " ":
        current.lines.append(DiffLine(number=current.new_line, text=text, added=False))
        current.new_line += 1
        current.old_line += 1
    elif marker == "\\":
        return  # "\ No newline at end of file"
    # Anything else is not part of a hunk and is ignored.


def _finish(files: list[FileChange], current: _Builder | None) -> None:
    if current is not None and current.path:
        files.append(_clean(current))


def _clean(builder: _Builder) -> FileChange:
    return FileChange(
        path=builder.path,
        status=builder.status,
        old_path=builder.old_path,
        lines=[] if builder.binary else builder.lines,
        removed=builder.removed,
    )


def _paths_from_header(line: str) -> tuple[str | None, str]:
    """``diff --git a/x b/x`` → (old, new), with the ``a/`` and ``b/`` prefixes removed."""
    parts = line.split(" ")
    if len(parts) < 4:
        return None, ""
    old = parts[2][2:] if parts[2].startswith(("a/", "i/", "w/", "o/", "1/", "2/")) else parts[2]
    new = parts[3][2:] if parts[3].startswith(("b/", "i/", "w/", "o/", "1/", "2/")) else parts[3]
    return old, new


HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", re.MULTILINE)


def _hunk_start(line: str) -> tuple[int, int] | None:
    """``@@ -12,3 +14,4 @@`` → (14, 12); the new file's first line is what a citation uses."""
    match = HUNK.match(line)
    if match is None:
        return None
    return int(match.group(2)), int(match.group(1))


def _safe_path(value: str | None) -> str | None:
    """Paths in a diff come from the outside; a climbing path is refused, not resolved."""
    if value is None:
        return None
    path = value.strip()
    if not path or path == "/dev/null":
        return None
    if path.startswith("/") or ".." in path.split("/"):
        return None
    return path
