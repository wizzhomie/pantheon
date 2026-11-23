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

from ..context.knowledge import ProjectContext
from ..contracts.base import StrictModel
from ..review.contracts import ReviewOutcome
from .contracts import SandboxRecord, VerificationRequest, VerificationRun
from .engine import describe_limits
from .findings import Finding
from .trace import build_trace, summarise, unlinked_findings
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
    request: VerificationRequest | None = Field(
        default=None, description="what was asked for, so the run can be explained later"
    )
    context: ProjectContext | None = Field(
        default=None, description="the approved promises this run was judged against"
    )
    review: ReviewOutcome | None = Field(
        default=None, description="the reviewer's proposals and their anchors, when one ran"
    )

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
    review: ReviewOutcome | None = None,
    request: VerificationRequest | None = None,
    context: ProjectContext | None = None,
) -> VerificationReport:
    return VerificationReport(
        run=run,
        findings=findings,
        verdict=verdict,
        review=review,
        request=request,
        context=context,
    )


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
    if run.adopted:
        header.append(f"| Accepted rules | {_rule_list(run.adopted)} |")
    if run.sandbox is not None:
        header.append(f"| Sandbox | {describe_limits(run.sandbox)} |")
    blocks = [
        "\n".join(header),
        _rules_note(run),
        _request_section(report.request),
        _sandbox_section(run.sandbox),
        _plan_section(run),
        _reasons_section(report.verdict),
        _checks_section(run),
        _review_section(report.review),
        _findings_section(report.findings),
        _quotes_section(report.findings),
        _trace_section(report),
        _gaps_section(run, report.review),
        "## How this verdict was reached\n\n"
        "The verdict is computed from the evidence above by code, not by a model: a failed or\n"
        "timed-out required check and a confirmed blocking finding produce `HOLD`; a required\n"
        "check that did not run, or a review that did not happen, produces `INCONCLUSIVE`; a\n"
        "non-blocking or unconfirmed finding produces `PASS WITH CONDITIONS`.\n\n"
        "A reviewer finding is `confirmed` only when the file is part of the change, the line\n"
        "exists, and the quote is literally that line. Anything else stays `unverified`: it is\n"
        "reported, and it cannot hold a release.\n\n"
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


def _rules_note(run: VerificationRun) -> str:
    """The policy of this run is the declared policy plus what the project accepted."""
    if not run.adopted:
        return ""
    return (
        "This run also used rules the project accepted from its own history: "
        + _rule_list(run.adopted)
        + ". Each one is a file in `.qagent/proposals/`, with the recorded runs\n"
        "behind it; the declared policy was not edited."
    )


def _rule_list(rules: list[str]) -> str:
    return ", ".join("`" + rule + "`" for rule in rules)


def _request_section(request: VerificationRequest | None) -> str:
    if request is None:
        return ""
    lines = [
        "## Request",
        "",
        "| | |",
        "|---|---|",
        f"| Name | {request.name} |",
        f"| Policy | `{request.policy}` |",
    ]
    if request.diff is not None:
        lines.append(f"| Change | diff `{request.diff}` |")
    elif request.since is not None:
        lines.append(f"| Change | `git diff {request.since}` |")
    if request.reviewer is not None:
        lines.append(f"| Reviewer | `{request.reviewer}` |")
    if request.second_look:
        lines.append("| Second look | on: one more call checks every claim |")
    if request.notes:
        lines.append(f"| Notes | {request.notes} |")
    return "\n".join(lines)


def _sandbox_section(record: SandboxRecord | None) -> str:
    """What was limited, and — in the same breath — what was not.

    A report that says "sandbox" and stops is exactly the kind of claim this project refuses:
    the reader would assume isolation the run does not have. The limits are resource ceilings,
    the boundary is missing, and the section says both.
    """
    if record is None:
        return ""
    limits = describe_limits(record)
    if record.backend == "unsupported":
        return (
            "## Sandbox\n\n"
            f"The policy declares {limits}, but this platform cannot apply them. The checks "
            "were recorded as errors instead of running without the ceiling they declared, so "
            "no check in this report ran unguarded while claiming otherwise."
        )
    return (
        "## Sandbox\n\n"
        f"Every check ran with {limits}, and core dumps were disabled.\n\n"
        "These are resource ceilings, not isolation: a check can still reach the network and "
        "read or write any path the user running the agent can. qagent limits what a check may "
        "consume; it does not yet confine where it may go, and this report does not claim "
        "otherwise."
    )


def _plan_section(run: VerificationRun) -> str:
    """Why a check the policy declares did not run: the change did not need it.

    This section exists so that "not selected" can never be read as "not checked and nobody
    knows". A path-scoped check is a decision the policy made in advance, and the report says
    which paths it guards.
    """
    skipped = [check for check in run.checks if not check.selected]
    if not skipped:
        return ""
    lines = [
        "## Plan",
        "",
        "Path-scoped checks run only when the change touches the paths they guard; a run without "
        "a change plans all of them.",
        "",
        "| Check | Why it was not selected |",
        "|---|---|",
    ]
    lines.extend(f"| `{check.check_id}` | {check.reason or 'not selected'} |" for check in skipped)
    return "\n".join(lines)


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
        mark = "not selected" if not check.selected else STATUS_MARK[check.status]
        reason = f" ({check.reason})" if check.reason else ""
        lines.append(
            f"| `{check.check_id}` | {mark}{reason} | {exit_code} "
            f"| {check.duration_ms / 1000:.2f}s | `{evidence}` |"
        )
    return "\n".join(lines)


def _review_section(review: ReviewOutcome | None) -> str:
    if review is None:
        return ""
    if review.status == "failed":
        return (
            "## Review\n\n"
            f"The reviewer did not run: {review.error or 'no reason recorded'}.\n\n"
            "No finding in this report comes from a model."
        )
    model = f"`{review.model}`" + (" (cached)" if review.cached else "")
    tokens = f"{review.prompt_tokens} in / {review.completion_tokens} out"
    if review.cached:
        tokens += " (paid once, on the original review)"
    lines = [
        "## Review",
        "",
        "| | |",
        "|---|---|",
        f"| Model | {model} |",
        f"| Diff | `{review.diff_digest[:12]}` |",
        f"| Files reviewed | {review.files_reviewed} |",
        f"| Proposed | {review.proposed} |",
        f"| Confirmed by the anchor | {review.confirmed} |",
        f"| Unverified | {review.unverified} |",
        f"| Tokens | {tokens} |",
        f"| Time | {review.spent_seconds:.1f}s |",
    ]
    if review.second_look != "off":
        fate = (
            f"{review.refuted} claim(s) dropped"
            if review.second_look == "ok"
            else "the reader did not answer, the claims stand"
        )
        lines.extend(
            [
                f"| Second look | {fate} |",
                "",
                "A claim the second reader drops becomes `false-positive`: it stays in this",
                "report, with the line it quoted, and the verdict does not count it.",
            ]
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
        status: str = finding.status
        if status == "false-positive":
            status = "false-positive (second look)"
        lines.append(
            f"| **{finding.id}** — {finding.title} | {finding.severity} "
            f"| {status} | {finding.kind} | {where} |"
        )
    return "\n".join(lines)


def _quotes_section(findings: list[Finding]) -> str:
    """The evidence of a reviewer finding is the line it quoted; show it, don't describe it."""
    quoted = [finding for finding in findings if finding.source == "reviewer" and finding.location]
    if not quoted:
        return ""
    lines = ["## Reviewer evidence", ""]
    for finding in quoted:
        assert finding.location is not None
        if finding.status == "confirmed":
            mark = ""
        elif finding.status == "false-positive":
            mark = " (dropped by the second reader)"
        else:
            mark = " (unverified: the anchor refused it)"
        lines.append(f"**{finding.id}** — `{finding.location.file}:{finding.location.line}`{mark}")
        lines.append("")
        lines.append(f"> {finding.location.quote}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _trace_section(report: VerificationReport) -> str:
    """Every approved promise and what this run knows about it — the "why" behind the checks."""
    if report.context is None:
        return ""
    rows = build_trace(report.context, report.run, report.findings)
    if not rows:
        return ""
    lines = [
        "## Traceability",
        "",
        f"{summarise(rows)}.",
        "",
        "| Requirement | Status | Checks | Findings |",
        "|---|---|---|---|",
    ]
    for row in rows:
        checks = ", ".join(f"`{check}`" for check in row.checks) or "—"
        findings = ", ".join(row.findings) or "—"
        lines.append(f"| **{row.id}** — {row.title} | {row.status} | {checks} | {findings} |")
    unlinked = unlinked_findings(report.findings)
    if unlinked:
        lines.append("")
        lines.append(
            "Confirmed findings with no approved requirement: "
            + ", ".join(f"**{finding.id}**" for finding in unlinked)
            + "."
        )
    return "\n".join(lines)


def _gaps_section(run: VerificationRun, review: ReviewOutcome | None) -> str:
    """What did not run, and why. This is the section a quiet PASS would have hidden."""
    gaps = [
        check
        for check in run.checks
        if check.selected and check.status in ("error", "skipped", "not_applicable")
    ]
    if not gaps and (review is None or review.status != "failed"):
        return ""
    lines = ["## Not checked", ""]
    lines.extend(
        f"- `{check.check_id}`: {check.status} — {check.reason or 'no reason recorded'}"
        for check in gaps
    )
    if review is not None and review.status == "failed":
        lines.append(f"- reviewer: failed — {review.error or 'no reason recorded'}")
    return "\n".join(lines)
