"""The check debt: how many narrowed verdicts stand since the last full run.

A narrowed verdict is a decision, not a free lunch: it ran the checks the change could touch and
left the rest for later. This module counts those decisions in the box's records, newest first,
until a verdict that ran every check the policy declared — the repayment. A reused verdict is
skipped, because it repeats a run that is already counted; a record whose report is gone is
counted as unreadable, not as repaid.

The count is what the box reads: ``status`` shows it, and a dispatch with debt at the limit
verifies the full policy once instead of narrowing again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import box as boxes

LIMIT = 5
"""Narrowed verdicts that may stand before the next run has to be full."""
RECORD_FILE = "box-verify.json"


@dataclass(frozen=True, slots=True)
class Debt:
    """The narrowed verdicts since the last full run, newest first, and what could not be read."""

    narrowed: tuple[str, ...] = ()
    last_full: str | None = None
    unknown: int = 0

    @property
    def count(self) -> int:
        return len(self.narrowed)

    def due(self) -> bool:
        """Whether the next verification has to run the full policy instead of a narrowed set."""
        return self.count >= LIMIT

    def line(self) -> str:
        """The debt as one line for a person."""
        if self.count and self.last_full is not None:
            text = f"{self.count} narrowed verdict(s) since {self.last_full}"
        elif self.count:
            text = f"{self.count} narrowed verdict(s), no full run yet"
        elif self.last_full is not None:
            text = f"0 — the last verdict ran every check ({self.last_full})"
        else:
            text = "nothing verified yet"
        if self.due():
            text += " — the next dispatch runs the full set"
        if self.unknown:
            text += f"; {self.unknown} verdict(s) unreadable"
        return text

    def json(self) -> dict[str, object]:
        return {
            "count": self.count,
            "limit": LIMIT,
            "due": self.due(),
            "last_full": self.last_full,
            "narrowed": list(self.narrowed),
            "unknown": self.unknown,
        }


def tally(box: boxes.Box) -> Debt:
    """Read the verdicts the box recorded and count the narrowed ones since the last full run."""
    base = box.root / "tabularium"
    if not base.is_dir():
        return Debt()
    found: list[tuple[str, str, bool | None]] = []
    for record in base.rglob(RECORD_FILE):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("reused_from"):
            continue
        at = str(data.get("at", ""))
        run = record.parent.name
        found.append((at, run, _narrowed(data)))
    return _walk(found)


def _walk(found: list[tuple[str, str, bool | None]]) -> Debt:
    narrowed: list[str] = []
    unknown = 0
    last_full: str | None = None
    for _, run, state in sorted(found, key=lambda item: (item[0], item[1]), reverse=True):
        if state is None:
            unknown += 1
            continue
        if state:
            narrowed.append(run)
            continue
        last_full = run
        break
    return Debt(narrowed=tuple(narrowed), last_full=last_full, unknown=unknown)


def _narrowed(data: dict[str, object]) -> bool | None:
    """Whether a verdict skipped checks; ``None`` when its record and report cannot say."""
    skipped = data.get("skipped")
    if isinstance(skipped, list):
        return bool(skipped)
    report = data.get("report")
    if not isinstance(report, str):
        return None
    try:
        body = json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    run = body.get("run")
    if not isinstance(run, dict):
        return None
    checks = run.get("checks")
    if not isinstance(checks, list):
        return None
    return any(
        isinstance(check, dict) and check.get("selected") is False for check in checks
    )
