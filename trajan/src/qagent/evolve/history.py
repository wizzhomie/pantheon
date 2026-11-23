"""The recorded runs, read back as a learning set.

A rule is only worth adding if the history says it would have helped, so the history has to
be readable: every stored report is one observation — what the run saw, what the verdict was,
what was found. Nothing here re-runs a check or calls a model; the past is already evidence.

Two rules keep this honest. A run that was never decided (``run.json`` without a
``report.json``) is not an observation about verdicts and is listed as skipped instead of
being given a verdict after the fact. And a file that does not parse is skipped by name, so a
corrupt report cannot quietly shrink the evidence a proposal stands on.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ..contracts.base import StrictModel
from ..verify.contracts import CheckRun, VerificationRun
from ..verify.findings import Finding
from ..verify.report import REPORT_JSON, read_report
from ..verify.verdict import Verdict

RUN_JSON = "run.json"


class Skipped(StrictModel):
    """A path that was in the history directory but is not usable as evidence."""

    path: str
    reason: str


class RunFacts(StrictModel):
    """One decided run: the record, the verdict that was computed from it, the findings."""

    run: VerificationRun
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def started_at(self) -> datetime:
        return _aware(self.run.started_at)

    @property
    def shippable(self) -> bool:
        return self.verdict.shippable

    def check(self, check_id: str) -> CheckRun | None:
        for check in self.run.checks:
            if check.check_id == check_id:
                return check
        return None

    def findings_for(self, check_id: str) -> list[str]:
        """The findings this run produced for a check — the ``check-<id>`` convention."""
        wanted = f"check-{check_id}"
        return [finding.id for finding in self.findings if finding.id == wanted]


class History(StrictModel):
    """Everything the project decided about its own changes, oldest first."""

    runs: list[RunFacts] = Field(default_factory=list)
    skipped: list[Skipped] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.runs

    def digest(self) -> str:
        """The input a proposal was mined from: which runs, and what they concluded."""
        payload = [[facts.run_id, facts.verdict.status] for facts in self.runs]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def span(self) -> tuple[datetime, datetime] | None:
        if not self.runs:
            return None
        return self.runs[0].started_at, self.runs[-1].started_at

    def verdicts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for facts in self.runs:
            counts[facts.verdict.status] = counts.get(facts.verdict.status, 0) + 1
        return dict(sorted(counts.items()))

    def containing(self, check_id: str) -> list[RunFacts]:
        return [facts for facts in self.runs if facts.check(check_id) is not None]


def load_history(paths: Iterable[str | Path]) -> History:
    """Read every report under the given paths; unusable entries are named, not dropped."""
    runs: list[RunFacts] = []
    skipped: list[Skipped] = []
    sources: list[str] = []
    seen: set[str] = set()

    for raw in paths:
        source = Path(raw)
        sources.append(str(source))
        directories = _run_directories(source, skipped)
        for directory in directories:
            report = directory / REPORT_JSON
            if not report.is_file():
                skipped.append(
                    Skipped(
                        path=str(directory),
                        reason=f"no {REPORT_JSON}: this run was never decided",
                    )
                )
                continue
            try:
                loaded = read_report(report)
            except (ValueError, OSError) as exc:
                skipped.append(Skipped(path=str(report), reason=f"unreadable report: {exc}"))
                continue
            if loaded.run.run_id in seen:
                skipped.append(
                    Skipped(path=str(report), reason=f"run {loaded.run.run_id} already loaded")
                )
                continue
            seen.add(loaded.run.run_id)
            runs.append(
                RunFacts(run=loaded.run, verdict=loaded.verdict, findings=list(loaded.findings))
            )

    runs.sort(key=lambda facts: (facts.started_at, facts.run_id))
    return History(runs=runs, skipped=skipped, sources=sources)


def _run_directories(source: Path, skipped: list[Skipped]) -> list[Path]:
    """A run directory, a directory of runs, or a report file — nothing deeper than that."""
    if source.is_file():
        return [source.parent] if source.name == REPORT_JSON else []
    if not source.is_dir():
        skipped.append(Skipped(path=str(source), reason="no such history path"))
        return []
    if (source / REPORT_JSON).is_file() or (source / RUN_JSON).is_file():
        return [source]
    found = [
        child
        for child in sorted(source.iterdir())
        if child.is_dir()
        and ((child / REPORT_JSON).is_file() or (child / RUN_JSON).is_file())
    ]
    if not found:
        skipped.append(Skipped(path=str(source), reason="no runs found under this path"))
    return found


def _aware(value: datetime) -> datetime:
    """Compare datetimes from different hands: a report without a zone is treated as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
