"""Run a holdout suite: one project per case, one verdict per case, no shared state.

Every case is built the same way: the suite's base project is copied to a temporary
directory, the case's files are laid over it, the case's diff is placed at the project root,
and the ordinary `trajan verify` path decides the verdict. That is deliberate — the bench
must not have a verification path of its own, or it would be measuring that path instead of
the one users run.

A case never sees the last case's directory, and nothing is written back into the suite:
copying is what makes the answer repeatable and what keeps a bench run from editing the
project it is grading.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from ..orchestrator import execute
from ..verify.contracts import VerificationRequest
from .contracts import (
    BASE_DIR,
    BENCH_JSON,
    BENCH_MD,
    CASE_FILE,
    CASES_DIR,
    CHANGE_FILE,
    BenchCase,
    BenchLayer,
    BenchReport,
    CaseResult,
    matches,
)


def read_case(path: Path, case_id: str | None = None) -> BenchCase:
    """Load one `case.toml`; an unknown key is a typo, not a silent default.

    The id defaults to the folder name: a case that has to repeat its own directory is a
    chance to disagree with it.
    """
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    if case_id is not None:
        data.setdefault("id", case_id)
    return BenchCase.model_validate(data)


def discover(suite: Path) -> list[BenchCase]:
    """Every case of the suite, sorted by id so two runs are comparable."""
    cases_dir = Path(suite) / CASES_DIR
    if not cases_dir.is_dir():
        raise FileNotFoundError(f"no cases directory in {suite}")
    cases = [
        read_case(path / CASE_FILE, path.name)
        for path in sorted(cases_dir.iterdir())
        if path.is_dir()
    ]
    if not cases:
        raise ValueError(f"the suite at {suite} has no cases")
    seen = {case.id for case in cases}
    if len(seen) != len(cases):
        raise ValueError("case ids must be unique within a suite")
    return cases


async def run_suite(
    suite: Path,
    *,
    reviewer: str,
    model: str | None = None,
    out_dir: Path | None = None,
    layer: BenchLayer = "checks",
    second_look: bool = False,
) -> tuple[BenchReport, Path | None]:
    """Grade the contour on the suite and, when asked, write the report.

    ``layer`` selects which half is being measured: ``checks`` runs the cases whose defect
    the project's own tools can see, ``reviewer`` the ones only a model can catch, ``all``
    both. Mixing them in one number would hide which half found what. ``second_look`` turns on
    the second reader for every review, exactly as a user would; it doubles the paid calls.
    """
    suite = Path(suite).resolve()
    base = suite / BASE_DIR
    if not base.is_dir():
        raise FileNotFoundError(f"no base project in {suite}")
    cases = [case for case in discover(suite) if _in_layer(case, layer)]
    if not cases:
        raise ValueError(f"the suite at {suite} has no cases for layer {layer!r}")
    started_at = datetime.now(UTC)
    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="trajan-bench-") as tmp:
        work = Path(tmp)
        for case in cases:
            results.append(
                await _run_case(case, suite, base, work, reviewer, model, second_look)
            )
    report = BenchReport(
        suite=suite.name,
        reviewer=model or reviewer,
        started_at=started_at,
        cases=results,
    )
    target = None
    if out_dir is not None:
        target = Path(out_dir)
        write_bench(report, target)
    return report, target


def _in_layer(case: BenchCase, layer: BenchLayer) -> bool:
    if layer == "all":
        return True
    return case.needs_reviewer if layer == "reviewer" else not case.needs_reviewer


async def _run_case(
    case: BenchCase,
    suite: Path,
    base: Path,
    work: Path,
    reviewer: str,
    model: str | None,
    second_look: bool,
) -> CaseResult:
    project = work / case.id
    shutil.copytree(base, project)
    overlay = suite / CASES_DIR / case.id / "files"
    if overlay.is_dir():
        shutil.copytree(overlay, project, dirs_exist_ok=True)
    shutil.copy2(suite / CASES_DIR / case.id / CHANGE_FILE, project / CHANGE_FILE)

    request = VerificationRequest(
        name=case.id,
        policy=case.policy,
        diff=CHANGE_FILE,
        reviewer=reviewer,
        model=model,
        second_look=second_look,
    )
    started = time.monotonic()
    report, _ = await execute(request, project, out_dir=work / "runs" / case.id)
    confirmed = sum(
        1 for finding in report.findings if finding.source == "reviewer" and finding.status
        == "confirmed"
    )
    return CaseResult(
        id=case.id,
        expected=case.expected,
        actual=report.verdict.status,
        matched=matches(case.expected, report.verdict.status, confirmed),
        confirmed=confirmed,
        prompt_tokens=report.review.prompt_tokens if report.review is not None else 0,
        completion_tokens=report.review.completion_tokens if report.review is not None else 0,
        cached=report.review.cached if report.review is not None else False,
        reasons=[f"{reason.code}: {reason.detail}" for reason in report.verdict.reasons],
        spent_seconds=round(time.monotonic() - started, 3),
    )


def write_bench(report: BenchReport, out_dir: Path) -> tuple[Path, Path]:
    """Write the pair; the markdown is a rendering of the JSON next to it."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / BENCH_JSON
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path = target / BENCH_MD
    md_path.write_text(render_bench(report), encoding="utf-8")
    return json_path, md_path


def render_bench(report: BenchReport) -> str:
    cases = report.cases
    total = sum(case.spent_seconds for case in cases)
    lines = [
        f"# Bench — {report.summary()}",
        "",
        "| | |",
        "|---|---|",
        f"| Suite | `{report.suite}` |",
        f"| Reviewer | `{report.reviewer}` |",
        f"| Cases | {report.summary()} |",
        f"| Started | {report.started_at.astimezone(UTC):%Y-%m-%d %H:%M UTC} |",
        f"| Spent | {total:.1f}s |",
    ]
    if report.prompt_tokens or report.completion_tokens:
        paid = " (cached)" if any(case.cached for case in cases) else ""
        lines.append(
            f"| Tokens | {report.prompt_tokens} in / {report.completion_tokens} out{paid} |"
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Expected | Actual | Result | Why |",
            "|---|---|---|---|---|",
        ]
    )
    for case in cases:
        if case.matched:
            mark = "ok"
        elif case.caught:
            mark = "caught"
        elif case.missed:
            mark = "**missed**"
        elif case.false_alarm:
            mark = "**false alarm**"
        else:
            mark = "**off the answer**"
        reason = case.reasons[0] if case.reasons else "—"
        if case.expected == "finding":
            reason = f"{case.confirmed} confirmed finding(s); {reason}"
        lines.append(
            f"| `{case.id}` | {case.expected} | {case.actual} | {mark} | {reason} |"
        )
    unmatched = [case for case in cases if not case.matched]
    if unmatched:
        names = ", ".join(f"`{case.id}`" for case in unmatched)
        lines.extend(
            [
                "",
                f"Result: {len(cases) - len(unmatched)} of {len(cases)} cases matched "
                f"the suite; {names} did not.",
            ]
        )
    lines.extend(
        [
            "",
            "## What this measures",
            "",
            "Each case is a real project copied from the suite's base, with one change applied;",
            "the verdict is decided by the same code path a user runs, not by a bench-only path.",
            "A `hold` case is a planted defect: `PASS WITH CONDITIONS` does not count as catching",
            "it, because that verdict ships. A clean case that is held is the cost of catching",
            "defects too eagerly, and it is reported with the same weight.",
        ]
    )
    return "\n".join(lines) + "\n"
