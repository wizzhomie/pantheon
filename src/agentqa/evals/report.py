"""Building and rendering the evaluation report.

The report is a committed artefact, so it carries the facts needed to distrust it: the
preset hash, the dataset hash, the commit, the models, and both families of numbers — what
the loop did, and what the golden values say actually happened. Markdown is for humans,
JSON is for ``compare``; both are generated from the same object, so they cannot disagree.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contracts.evals import EvalReport, MetaEval
from ..runtime.facade import GuardedAgent
from .dataset import Dataset
from .metrics import CaseRecord, build_metrics

GIT_TIMEOUT_S = 2.0


def git_revision() -> str:
    """Short commit hash, from the environment or from git; empty when unavailable.

    Never raises: an evaluation run in a tarball without ``.git`` is still a valid run.
    """
    from_env = os.environ.get("AGENTQA_GIT_SHA") or os.environ.get("GITHUB_SHA")
    if from_env:
        return from_env.strip()[:40]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()[:40]


def make_run_id(mode: str, created_at: datetime, revision: str) -> str:
    """``20261122T170000-guarded-4c924ff``: sortable, unique per mode, short enough."""
    stamp = created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{mode}-{revision[:7] or 'nogit'}"


def build_report(
    records: Sequence[CaseRecord],
    *,
    guarded: GuardedAgent[Any],
    dataset: Dataset,
    created_at: datetime | None = None,
    revision: str | None = None,
    meta_eval: MetaEval | None = None,
) -> EvalReport:
    """Assemble a report from scored cases and the setup that produced them."""
    at = created_at or datetime.now(UTC)
    sha = revision if revision is not None else git_revision()
    mode = guarded.mode
    return EvalReport(
        run_id=make_run_id(mode, at, sha),
        created_at=at,
        git_sha=sha,
        mode=mode,
        preset_name=guarded.preset.name,
        preset_hash=guarded.preset.fingerprint(),
        dataset_hash=dataset.digest(),
        llm=_llm_info(guarded),
        metrics=build_metrics(records),
        meta_eval=meta_eval,
        cases=[record.to_case_result() for record in records],
    )


def write_json(report: EvalReport, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target


def write_markdown(report: EvalReport, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(report), encoding="utf-8")
    return target


def render_markdown(report: EvalReport) -> str:
    metrics = report.metrics
    lines = [
        f"# Evaluation report — `{report.mode}` · `{report.run_id}`",
        "",
        "| | |",
        "|---|---|",
        f"| Preset | `{report.preset_name}` |",
        f"| Preset hash | `{report.preset_hash}` |",
        f"| Dataset hash | `{report.dataset_hash}` |",
        f"| Cases | {metrics.cases} |",
        f"| Commit | `{report.git_sha or 'unknown'}` |",
        f"| Created | {report.created_at.astimezone(UTC):%Y-%m-%d %H:%M UTC} |",
        f"| Agent model | `{report.llm.get('agent', 'unknown')}` |",
        f"| Judge | `{report.llm.get('judge', 'unknown')}` |",
        "",
        "## Quality (ground truth from the golden values)",
        "",
        "| Metric | Value | Meaning |",
        "|---|---|---|",
        f"| Pass, first attempt | {_pct(metrics.pass_rate_first_attempt)} | "
        "accepted on attempt 1 and correct |",
        f"| Pass, final | {_pct(metrics.pass_rate_final)} | correct after the loop |",
        f"| Field accuracy (macro) | {_pct(metrics.field_accuracy_macro)} | "
        "weighted match of the expected fields |",
        f"| Hallucination rate | {_pct(metrics.hallucination_rate)} | "
        "a forbidden value appeared |",
        f"| False pass rate | {_pct(metrics.false_pass_rate)} | "
        "the loop accepted a wrong answer |",
        "",
        "## Loop",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Escalation rate | {_pct(metrics.escalation_rate)} |",
        f"| Retry rate | {_pct(metrics.retry_rate)} |",
        f"| Retry recovery rate | {_pct(metrics.retry_recovery_rate)} |",
        f"| Latency p50 / p95 | {_ms(metrics.latency_p50_ms)} / {_ms(metrics.latency_p95_ms)} |",
        f"| Cost, total | {_money(metrics.cost_usd_total)} |",
        f"| Cost per case | {_money(metrics.cost_usd_per_case)} |",
        f"| Cost per passed case | {_money(metrics.cost_usd_per_passed_case)} |",
        f"| Tokens (prompt / completion) | {metrics.prompt_tokens:,} / "
        f"{metrics.completion_tokens:,} |",
        "",
    ]
    if metrics.by_category:
        lines += [
            "## By category",
            "",
            "| Category | Cases | Pass, first | Pass, final | Escalation | Field accuracy |",
            "|---|---|---|---|---|---|",
        ]
        for category, block in sorted(metrics.by_category.items()):
            lines.append(
                f"| {category} | {block.cases} | {_pct(block.pass_rate_first_attempt)} | "
                f"{_pct(block.pass_rate_final)} | {_pct(block.escalation_rate)} | "
                f"{_pct(block.field_accuracy)} |"
            )
        lines.append("")
    if report.meta_eval is not None:
        lines += _meta_eval_section(report.meta_eval)
    lines += _cases_section(report)
    lines += _notes(report)
    return "\n".join(lines).rstrip() + "\n"


def _llm_info(guarded: GuardedAgent[Any]) -> dict[str, str]:
    judge = guarded.judge
    judge_model = getattr(judge, "model", None)
    judge_name = getattr(judge, "name", "custom")
    label = "off" if judge is None else str(judge_model) if judge_model else str(judge_name)
    return {
        "agent": guarded.preset.agent_model or "unknown",
        "judge": label,
    }


def _meta_eval_section(meta: MetaEval) -> list[str]:
    lines = [
        "## Judge calibration (hand labels)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Labelled cases | {meta.n} |",
        f"| Agreement | {_pct(meta.agreement)} |",
        f"| Cohen's kappa | {meta.cohens_kappa:.3f} |",
        f"| Judge precision | {_pct(meta.judge_precision)} |",
        f"| Judge recall | {_pct(meta.judge_recall)} |",
        "",
    ]
    if meta.false_pass_ids:
        lines.append(
            "**False passes (judge approved, human rejected):** "
            + ", ".join(f"`{case_id}`" for case_id in meta.false_pass_ids)
        )
        lines.append("")
    if meta.false_fail_ids:
        lines.append(
            "**False fails (judge rejected, human approved):** "
            + ", ".join(f"`{case_id}`" for case_id in meta.false_fail_ids)
        )
        lines.append("")
    return lines


def _cases_section(report: EvalReport) -> list[str]:
    lines = [
        "## Cases",
        "",
        "| Case | Category | Status | Correct | Accuracy | Codes | Retries | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in report.cases:
        correct = "yes" if case.final_passed else "no"
        codes = ", ".join(case.failure_codes).replace("|", "/") or "—"
        lines.append(
            f"| `{case.case_id}` | {case.category} | {case.status} | {correct} | "
            f"{_pct(case.field_accuracy)} | {codes} | {case.retries} | "
            f"{_money(case.cost_usd)} |"
        )
    lines.append("")
    return lines


def _notes(report: EvalReport) -> list[str]:
    metrics = report.metrics
    notes: list[str] = []
    passed_cases = sum(1 for case in report.cases if case.final_passed)
    if passed_cases == 0:
        notes.append(
            "No case passed: cost per passed case is undefined and shown as $0. "
            "Do not read it as a cheap run."
        )
    if metrics.false_pass_rate > 0:
        notes.append(
            f"{metrics.false_pass_rate:.1%} of cases were accepted by the loop but are wrong "
            "against the golden values."
        )
    if not report.git_sha:
        notes.append("Commit is unknown: the run was produced outside a git checkout.")
    if not notes:
        return []
    return ["## Notes", ""] + [f"- {note}" for note in notes] + [""]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _money(value: float) -> str:
    return f"${value:,.4f}"


def _ms(value: float) -> str:
    return f"{value:,.0f} ms"
