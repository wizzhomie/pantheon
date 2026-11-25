"""The orchestrator: one request in, one stored run out.

Everything below this module already works — a policy runs, a diff is reviewed, a verdict is
computed. What was missing was the piece that ties them into *one thing a person asked for*:
a request (which project, which policy, which change, which reviewer, how much money), the
plan built from it, the run that answered it, and a record that can be read back later.

Two rules keep it honest:

* the request is stored **in the report**, not in a side file that can drift from it;
* ``trajan show`` only reads. Explaining a run must never trigger a new one, or the
  explanation is a second experiment wearing the first one's name.
"""

from __future__ import annotations

import importlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .context import ProjectContext, ProjectProfile, detect_profile, read_context, read_profile
from .evolve import AppliedPolicy, apply_proposals, load_proposals
from .llm.base import LLMClient
from .review import Diff, ReviewerConfig, ReviewOutcome, parse_diff, review_diff
from .review.cache import ReviewCache
from .verify import (
    SandboxRecord,
    VerificationReport,
    VerificationRequest,
    build_plan,
    build_report,
    decide,
    describe_limits,
    describe_plan,
    describe_sandbox,
    findings_from_run,
    read_policy,
    read_report,
    run_policy,
    write_report,
)
from .verify.trace import build_trace, summarise

REQUEST_JSON = "request.json"
REPORT_JSON = "report.json"


def read_request(path: str | Path) -> VerificationRequest:
    """Load a request from TOML; an unknown key is a typo, not a silent no-op."""
    import tomllib

    source = Path(path)
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"no request at {source}") from exc
    return VerificationRequest.model_validate(data)


async def execute(
    request: VerificationRequest,
    repo: str | Path,
    *,
    out_dir: str | Path | None = None,
    use_cache: bool = True,
) -> tuple[VerificationReport, Path]:
    """Answer the request: run the checks, review the change when asked, store the verdict."""
    root = Path(repo).resolve()
    applied = effective_policy(root, _path(root, request.policy))
    policy = applied.policy
    profile = _profile(root)

    context = _context(root)
    requirements = _requirements(context)
    review: ReviewOutcome | None = None
    gaps: list[str] = []
    changed: list[str] | None = None
    if request.paths is not None:
        changed = list(request.paths)
    if request.needs_review:
        diff = load_diff(request, root)
        changed = changed_paths(diff)
        review = await review_diff(
            diff,
            root,
            _client(request),
            _reviewer_config(request, root),
            requirements=requirements,
            cache=ReviewCache(root) if use_cache else None,
        )
        if review.status == "failed":
            gaps.append(f"the reviewer did not run: {review.error}")

    target_dir = Path(out_dir) if out_dir is not None else None
    run, target = await run_policy(
        policy,
        root,
        out_dir=target_dir,
        profile=profile,
        adopted=applied.adopted,
        changed=changed,
    )
    findings = findings_from_run(run)
    if review is not None:
        findings.extend(review.findings)
    verdict = decide(run, findings, gaps=gaps)
    report = build_report(run, findings, verdict, review, request, context)
    (target / REQUEST_JSON).write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
    write_report(report, target)
    return report, target


def effective_policy(root: Path, path: Path) -> AppliedPolicy:
    """The declared policy plus the rules the project has accepted from its own history."""
    return apply_proposals(read_policy(path), load_proposals(root))


def trace_line(report: VerificationReport) -> str | None:
    """One line about approved promises, when the project has any."""
    if report.context is None:
        return None
    rows = build_trace(report.context, report.run, report.findings)
    if not rows:
        return None
    return f"traceability: {summarise(rows)}"


def plan_lines(request: VerificationRequest, repo: str | Path) -> list[str]:
    """What the run would do, without doing any of it."""
    root = Path(repo).resolve()
    applied = effective_policy(root, _path(root, request.policy))
    policy = applied.policy
    profile = _profile(root)
    lines = [f"{policy.name}: {request.describe()}"]
    if applied.adopted:
        lines.append(f"  accepted rules: {', '.join(applied.adopted)}")
    sandbox = describe_sandbox(policy.sandbox)
    if sandbox is not None:
        lines.append(f"  sandbox: {sandbox}")
    diff = load_diff(request, root) if request.needs_review else None
    changed = changed_paths(diff) if diff is not None else request.paths
    plan = build_plan(policy, profile, changed=changed)
    lines.extend(f"  {line}" for line in describe_plan(plan))
    if diff is not None:
        lines.append(
            f"  review: {len(diff.files)} file(s), {diff.added_lines} added line(s), "
            f"reviewer {request.reviewer}"
        )
        if request.second_look:
            lines.append("  second look: on — another call checks every claim before the anchor")
    return lines


def changed_paths(diff: Diff | None) -> list[str] | None:
    """The paths a plan may reason about, or ``None`` when the change is not fully known.

    A diff cut by a size or file cap no longer describes the whole change: the files after the
    cut were never seen, so a gate scoped to one of them would be skipped for the wrong reason.
    An incomplete change is treated like an unknown one — plan everything.
    """
    if diff is None or diff.truncated:
        return None
    return diff.paths


def load_diff(request: VerificationRequest, root: Path) -> Diff:
    """The change the request names: a diff file, or the project's own `git diff`."""
    if request.diff is not None:
        text = _path(root, request.diff).read_text(encoding="utf-8")
    else:
        assert request.since is not None
        text = _git_diff(root, request.since)
    return parse_diff(text)


def show(path: str | Path) -> tuple[VerificationReport, Path]:
    """Read a stored report back. Never runs anything: this is the explanation, not a rerun."""
    target = Path(path)
    if target.is_dir():
        target = target / REPORT_JSON
    if not target.is_file():
        raise FileNotFoundError(f"no report at {target}")
    return read_report(target), target


def _look_summary(review: ReviewOutcome) -> str:
    """What the second reader did, on the same line as the review it checked."""
    if review.second_look == "off":
        return ""
    if review.second_look == "failed":
        return ", second look failed (claims stand)"
    return f", {review.refuted} dropped by the second look"


def render_show(report: VerificationReport) -> str:
    """One screen that answers: what was asked, what happened, what does it mean."""
    run = report.run
    verdict = report.verdict
    lines = [
        f"{verdict.headline}: {run.run_id}",
        f"  request: {report.request.describe() if report.request else 'ad-hoc run'}",
        f"  policy: {run.policy_name} · checks {len(run.checks)} of {run.planned} · "
        f"{run.spent_seconds:.1f}s",
    ]
    if run.adopted:
        lines.append(f"  rules: {', '.join(run.adopted)}")
    if run.sandbox is not None:
        lines.append(f"  sandbox: {_sandbox_line(run.sandbox)}")
    counts = run.by_status()
    if counts:
        lines.append("  checks: " + ", ".join(f"{name} {count}" for name, count in counts.items()))
    for reason in verdict.reasons:
        lines.append(f"  {reason.code}: {reason.detail}")
    if report.findings:
        blocking = sum(1 for finding in report.findings if finding.blocking)
        lines.append(f"  findings: {len(report.findings)} ({blocking} blocking)")
        for finding in report.findings:
            where = (
                f" at {finding.location.file}:{finding.location.line}"
                if finding.location is not None
                else ""
            )
            lines.append(f"    {finding.id} [{finding.status}] {finding.title}{where}")
    if report.review is not None:
        if report.review.status == "failed":
            lines.append(f"  review: failed ({report.review.error})")
        else:
            cached = " (cached)" if report.review.cached else ""
            look = _look_summary(report.review)
            lines.append(
                f"  review: {report.review.model}{cached}, {report.review.proposed} proposed, "
                f"{report.review.confirmed} confirmed{look}"
            )
    traced = trace_line(report)
    if traced is not None:
        lines.append(f"  {traced}")
    for check in [item for item in run.checks if not item.selected]:
        lines.append(f"  not selected: {check.check_id} ({check.reason})")
    gaps = [
        check
        for check in run.checks
        if check.selected and check.status in ("error", "skipped", "not_applicable")
    ]
    for check in gaps:
        why = f" {check.reason}" if check.reason else ""
        lines.append(f"  not checked: {check.check_id} ({check.status}){why}")
    return "\n".join(lines)


def _sandbox_line(record: SandboxRecord) -> str:
    """One screen of text about limits: what applied, and what it does not cover."""
    if record.backend == "unsupported":
        return f"{describe_limits(record)} (declared, not applied on this platform)"
    return f"{describe_limits(record)} (network and file system not isolated)"


def exit_code_for(report: VerificationReport) -> int:
    """0 shippable, 1 HOLD, 2 INCONCLUSIVE — the same mapping as the command itself."""
    return {"pass": 0, "pass_with_conditions": 0, "hold": 1, "inconclusive": 2}[
        report.verdict.status
    ]


def _reviewer_config(request: VerificationRequest, root: Path) -> ReviewerConfig:
    instructions = ""
    if request.instructions is not None:
        instructions = _path(root, request.instructions).read_text(encoding="utf-8")
    return ReviewerConfig(
        model=request.model or "reviewer",
        instructions=instructions,
        max_tokens=request.max_tokens,
        second_look=request.second_look,
    )


def _client(request: VerificationRequest) -> LLMClient:
    assert request.reviewer is not None
    module_name, separator, attribute = request.reviewer.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(
            f"reviewer must look like package.module:factory, got {request.reviewer!r}"
        )
    module = importlib.import_module(module_name)
    factory: Callable[[], object] = getattr(module, attribute)
    if not callable(factory):
        raise ValueError(f"{request.reviewer} is not callable")
    client = factory()
    if not callable(getattr(client, "complete", None)):
        raise ValueError(f"{request.reviewer} must return an LLMClient with a complete() method")
    return cast(LLMClient, client)


def _context(root: Path) -> ProjectContext | None:
    try:
        return read_context(root)
    except FileNotFoundError:
        return None


def _requirements(context: ProjectContext | None) -> tuple[tuple[str, str], ...]:
    """The approved promises the reviewer may cite, as (id, title) pairs."""
    if context is None:
        return ()
    return tuple(
        (item.id, item.title)
        for item in (*context.requirements, *context.invariants)
        if item.status == "approved"
    )


def _profile(root: Path) -> ProjectProfile | None:
    try:
        return read_profile(root)
    except FileNotFoundError:
        try:
            return detect_profile(root)
        except NotADirectoryError:
            return None


def _path(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _git_diff(root: Path, revision: str) -> str:
    completed = subprocess.run(
        ["git", "diff", "--no-color", "--no-ext-diff", revision],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60.0,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"git diff {revision} failed: {completed.stderr.strip()[:300]}")
    return completed.stdout
