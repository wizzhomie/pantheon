"""The verification report: one document, rendered from one object.

Markdown is a rendering of the JSON next to it, never a second source of truth. That rule
was paid for once already: a replay rewrote a report's markdown while its JSON held the live
numbers, and a reader could not tell which was the run. Here the same object produces both, so
they cannot disagree.

The report is written for a human deciding whether to ship, so it leads with the verdict, then
shows the evidence it rests on, then what was *not* checked. The last section is not an
afterthought: a report that hides its gaps is a claim, not a report.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

from pydantic import Field, model_validator

from ..contracts.base import StrictModel
from .contracts import VerificationRun
from .findings import Finding
from .verdict import Verdict

REPORT_VERSION = 1

REPORT_JSON = "report.json"
REPORT_MD = "report.md"

STATUS_MARK = {
    "passed": "pass",
    "failed": "fail",
    "timeout": "timeout",
    "error": "error",
    "skipped": "skipped",
    "not_applicable": "n/a",
}


class VerificationReport(StrictModel):
    """Everything a reader needs to trust or distrust the verdict, in one object."""

    schema_version: int = Field(default=REPORT_VERSION)
    run: VerificationRun
    findings: list[Finding] = Field(default_factory=list)
    verdict: Verdict

    @model_validator(mode="after")
    def _consistent(self) -> VerificationReport:
        """The verdict may only point at findings the report actually contains."""
        ids = [finding.id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding ids must be unique within a report")
        unknown = [fid for fid in self.verdict.blocking_findings if fid not in ids]
        if unknown:
            raise ValueError(f"the verdict references unknown findings: {', '.join(unknown)}")
        return self


def build_report(
    run: VerificationRun,
    findings: list[Finding],
    verdict: Verdict,
) -> VerificationReport:
    return VerificationReport(run=run, findings=findings, verdict=verdict)


def render_markdown(report: VerificationReport) -> str:
    run = report.run
    header = [
        f"# Verification report — {report.verdict.headline}",
        "",
        "| | |",
        "|---|---|",
        f"| Verdict | **{report.verdict.headline}** |",
        f"| Run | `{run.run_id}` |",
        f"| Policy | `{run.policy_name}` |",
        f"| Policy digest | `{run.policy_digest[:12]}` |",
        f"| Profile digest | `{run.profile_digest[:12] if run.profile_digest else 'none'}` |",
        f"| Started | {run.started_at.astimezone(UTC):%Y-%m-%d %H:%M UTC} |",
        f"| Checked | {len(run.checks)} of {run.planned} planned |",
        f"| Spent | {run.spent_seconds:.1f}s |",
    ]
    blocks = [
        "\n".join(header),
        _reasons_section(report.verdict),
        _checks_section(run),
        _findings_section(report.findings),
        _gaps_section(run),
        "## How this verdict was reached\n\n"
        "The verdict is computed from the checks above by code, not by a model: a failed or\n"
        "timed-out required check and a confirmed blocking finding produce `HOLD`; a required\n"
        "check that did not run produces `INCONCLUSIVE`; a non-blocking or unconfirmed finding\n"
        "produces `PASS WITH CONDITIONS`. Nothing in this document overrides that rule.\n\n"
        "Findings here come from the checks themselves; an independent reviewer that proposes\n"
        "findings about a diff is the next phase and does not exist yet.\n\n"
        "Markdown is a rendering of the JSON report next to it; the JSON is the record.",
    ]
    return "\n\n".join(block for block in blocks if block) + "\n"


def write_report(report: VerificationReport, out_dir: Path) -> tuple[Path, Path]:
    """Write the pair; both come from the same object, on purpose."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / REPORT_JSON
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path = target / REPORT_MD
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def read_report(path: Path) -> VerificationReport:
    """Parse a report back; a report that cannot be parsed is not evidence."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return VerificationReport.model_validate(data)


def _reasons_section(verdict: Verdict) -> str:
    if not verdict.reasons:
        return ""
    lines = ["## Why", ""]
    lines.extend(f"- `{reason.code}` — {reason.detail}" for reason in verdict.reasons)
    return "\n".join(lines)


def _checks_section(run: VerificationRun) -> str:
    if not run.checks:
        return ""
    lines = [
        "## Checks",
        "",
        "| Check | Status | Exit | Time | Evidence |",
        "|---|---|---|---|---|",
    ]
    for check in run.checks:
        exit_code = "—" if check.exit_code is None else str(check.exit_code)
        evidence = check.stdout_path or check.stderr_path or "—"
        reason = f" ({check.reason})" if check.reason else ""
        lines.append(
            f"| `{check.check_id}` | {STATUS_MARK[check.status]}{reason} | {exit_code} "
            f"| {check.duration_ms / 1000:.2f}s | `{evidence}` |"
        )
    return "\n".join(lines)


def _findings_section(findings: list[Finding]) -> str:
    if not findings:
        return "## Findings\n\nNone."
    lines = [
        "## Findings",
        "",
        "| Finding | Severity | Status | Kind | Where |",
        "|---|---|---|---|---|",
    ]
    for finding in findings:
        where = f"`{finding.location.file}:{finding.location.line}`" if finding.location else "—"
        lines.append(
            f"| **{finding.id}** — {finding.title} | {finding.severity} "
            f"| {finding.status} | {finding.kind} | {where} |"
        )
    return "\n".join(lines)


def _gaps_section(run: VerificationRun) -> str:
    """What did not run, and why. This is the section a quiet PASS would have hidden."""
    gaps = [check for check in run.checks if check.status in ("error", "skipped", "not_applicable")]
    if not gaps:
        return ""
    lines = ["## Not checked", ""]
    lines.extend(
        f"- `{check.check_id}`: {check.status} — {check.reason or 'no reason recorded'}"
        for check in gaps
    )
    return "\n".join(lines)
