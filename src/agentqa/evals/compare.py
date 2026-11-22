"""Comparing two reports.

A diff is only meaningful when both runs measured the same cases with the same preset, so
the first thing it reports is whether that was true. Metrics carry their own direction:
more pass rate is better, more cost is not, and an escalation rate is neutral — sending a
case to a human is a business decision, not a defect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ..contracts.evals import CaseChange, EvalReport, MetricDelta, ReportDiff

Direction = Literal["up", "down", "neutral"]

DIRECTIONS: dict[str, Direction] = {
    "pass_rate_first_attempt": "up",
    "pass_rate_final": "up",
    "field_accuracy_macro": "up",
    "retry_recovery_rate": "up",
    "hallucination_rate": "down",
    "false_pass_rate": "down",
    "cost_usd_total": "down",
    "cost_usd_per_case": "down",
    "cost_usd_per_passed_case": "down",
    "latency_p50_ms": "down",
    "latency_p95_ms": "down",
    "retry_rate": "neutral",
    "escalation_rate": "neutral",
}

TOLERANCE = 1e-9

LABELS: dict[str, str] = {
    "pass_rate_first_attempt": "Pass, first attempt",
    "pass_rate_final": "Pass, final",
    "field_accuracy_macro": "Field accuracy (macro)",
    "retry_recovery_rate": "Retry recovery",
    "hallucination_rate": "Hallucination rate",
    "false_pass_rate": "False pass rate",
    "cost_usd_total": "Cost, total",
    "cost_usd_per_case": "Cost per case",
    "cost_usd_per_passed_case": "Cost per passed case",
    "latency_p50_ms": "Latency p50",
    "latency_p95_ms": "Latency p95",
    "retry_rate": "Retry rate",
    "escalation_rate": "Escalation rate",
}

RATES = {
    "pass_rate_first_attempt",
    "pass_rate_final",
    "field_accuracy_macro",
    "retry_recovery_rate",
    "hallucination_rate",
    "false_pass_rate",
    "retry_rate",
    "escalation_rate",
}
MONEY = {"cost_usd_total", "cost_usd_per_case", "cost_usd_per_passed_case"}
DURATIONS = {"latency_p50_ms", "latency_p95_ms"}


def load_report(source: EvalReport | str | Path) -> EvalReport:
    """Accept a report object, a path, or the JSON text itself."""
    if isinstance(source, EvalReport):
        return source
    if isinstance(source, Path):
        return EvalReport.model_validate_json(source.read_text(encoding="utf-8"))
    text = source.strip()
    if text.startswith("{"):
        return EvalReport.model_validate_json(text)
    return EvalReport.model_validate_json(Path(text).read_text(encoding="utf-8"))


def compare(a: EvalReport | str | Path, b: EvalReport | str | Path) -> ReportDiff:
    """Metric deltas, status changes and the two hash flags."""
    first, second = load_report(a), load_report(b)
    metrics: dict[str, MetricDelta] = {}
    for name, direction in DIRECTIONS.items():
        before = float(getattr(first.metrics, name))
        after = float(getattr(second.metrics, name))
        delta = round(after - before, 6)
        improved = _improved(delta, direction)
        metrics[name] = MetricDelta(
            a=before,
            b=after,
            delta=delta,
            direction_is_good=direction,
            improved=improved,
        )
    return ReportDiff(
        a=first.run_id,
        b=second.run_id,
        metrics=metrics,
        changed_cases=_changed_cases(first, second),
        preset_hash_changed=first.preset_hash != second.preset_hash,
        dataset_hash_changed=first.dataset_hash != second.dataset_hash,
    )


def write_markdown(diff: ReportDiff, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(diff), encoding="utf-8")
    return target


def render_markdown(diff: ReportDiff) -> str:
    lines = [
        "# Report comparison",
        "",
        f"- **A:** `{diff.a}`",
        f"- **B:** `{diff.b}`",
        "",
    ]
    if diff.dataset_hash_changed:
        lines += [
            "> **Dataset changed.** The numbers are not comparable: change the dataset and the "
            "preset one at a time.",
            "",
        ]
    if diff.preset_hash_changed:
        lines += [
            "> **Preset changed.** The delta is the effect of the schema, checks, rubric or "
            "prompts — not of the model alone.",
            "",
        ]
    lines += [
        "## Metrics",
        "",
        "| Metric | A | B | Δ | Better |",
        "|---|---|---|---|---|",
    ]
    for name, delta in diff.metrics.items():
        verdict = _verdict(delta)
        lines.append(
            f"| {LABELS[name]} | {_value(name, delta.a)} | {_value(name, delta.b)} | "
            f"{_delta(name, delta.delta)} | {verdict} |"
        )
    lines.append("")
    lines += ["## Changed cases", ""]
    if not diff.changed_cases:
        lines += ["No case changed its status.", ""]
    else:
        lines += [
            "| Case | From | To | Codes |",
            "|---|---|---|---|",
        ]
        for change in diff.changed_cases:
            codes = ", ".join(change.failure_codes).replace("|", "/") or "—"
            lines.append(
                f"| `{change.case_id}` | {change.from_status} | {change.to_status} | {codes} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _value(name: str, value: float) -> str:
    if name in RATES:
        return f"{value * 100:.1f}%"
    if name in MONEY:
        return f"${value:,.4f}"
    if name in DURATIONS:
        return f"{value:,.0f} ms"
    return f"{value:,.4f}"


def _delta(name: str, delta: float) -> str:
    if name in RATES:
        return f"{delta * 100:+.1f} pp"
    if name in MONEY:
        return f"{delta:+,.4f}"
    if name in DURATIONS:
        return f"{delta:+,.0f} ms"
    return f"{delta:+,.4f}"


def _improved(delta: float, direction: Direction) -> bool:
    if direction == "up":
        return delta > TOLERANCE
    if direction == "down":
        return delta < -TOLERANCE
    return False


def _verdict(delta: MetricDelta) -> str:
    if delta.direction_is_good == "neutral":
        return "—"
    if delta.improved:
        return "better"
    return "worse" if abs(delta.delta) > TOLERANCE else "same"


def _changed_cases(first: EvalReport, second: EvalReport) -> list[CaseChange]:
    before = {case.case_id: case for case in first.cases}
    changes: list[CaseChange] = []
    for case in second.cases:
        previous = before.get(case.case_id)
        if previous is None or previous.status == case.status:
            continue
        changes.append(
            CaseChange(
                case_id=case.case_id,
                from_status=previous.status,
                to_status=case.status,
                failure_codes=list(case.failure_codes),
            )
        )
    return changes
