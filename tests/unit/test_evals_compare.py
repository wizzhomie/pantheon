"""Comparing reports: deltas with a direction, changed cases, and the hash warnings."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from qagent import EvalReport, Metrics
from qagent.contracts.evals import CaseResult
from qagent.evals.compare import compare, load_report, render_markdown, write_markdown

AT = datetime(2026, 11, 22, 17, 0, tzinfo=UTC)


def case(case_id: str, status: str, codes: list[str] | None = None) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        category="happy_path",
        status=status,  # type: ignore[arg-type]
        first_attempt_passed=status == "passed",
        final_passed=status in ("passed", "passed_after_retry"),
        retries=1 if status == "passed_after_retry" else 0,
        field_accuracy=1.0,
        failure_codes=codes or [],
        latency_ms=100.0,
        cost_usd=0.01,
    )


def report(
    run_id: str,
    *,
    metrics: Metrics,
    cases: list[CaseResult] | None = None,
    preset_hash: str = "a" * 64,
    dataset_hash: str = "b" * 64,
) -> EvalReport:
    return EvalReport(
        run_id=run_id,
        created_at=AT,
        mode="guarded",
        preset_name="toy",
        preset_hash=preset_hash,
        dataset_hash=dataset_hash,
        metrics=metrics,
        cases=cases or [],
    )


def metrics(**overrides: object) -> Metrics:
    values: dict[str, object] = {
        "cases": 2,
        "pass_rate_first_attempt": 0.5,
        "pass_rate_final": 0.5,
    }
    values.update(overrides)
    return Metrics(**values)  # type: ignore[arg-type]


def test_deltas_carry_a_direction_and_a_verdict() -> None:
    before = report(
        "a",
        metrics=metrics(cost_usd_per_case=0.05, hallucination_rate=0.5, false_pass_rate=0.5),
    )
    after = report(
        "b",
        metrics=metrics(
            pass_rate_final=1.0,
            cost_usd_per_case=0.08,
            hallucination_rate=0.1,
            false_pass_rate=0.0,
        ),
    )

    diff = compare(before, after)

    assert diff.metrics["pass_rate_final"].delta == 0.5
    assert diff.metrics["pass_rate_final"].improved is True
    assert diff.metrics["cost_usd_per_case"].improved is False
    assert diff.metrics["hallucination_rate"].improved is True
    assert diff.metrics["false_pass_rate"].direction_is_good == "down"
    assert diff.metrics["escalation_rate"].direction_is_good == "neutral"
    assert diff.metrics["escalation_rate"].improved is False


def test_a_missing_rate_delta_is_not_called_an_improvement() -> None:
    diff = compare(report("a", metrics=metrics()), report("b", metrics=metrics()))

    assert diff.metrics["pass_rate_final"].delta == 0.0
    assert diff.metrics["pass_rate_final"].improved is False


def test_changed_cases_are_reported_with_the_new_codes() -> None:
    before = report(
        "a",
        metrics=metrics(),
        cases=[case("c1", "passed"), case("c2", "escalated", ["G3_OUT_OF_RANGE"])],
    )
    after = report(
        "b",
        metrics=metrics(),
        cases=[case("c1", "escalated", ["G5_PII_LEAK"]), case("c2", "passed")],
    )

    diff = compare(before, after)

    changes = [
        (change.case_id, change.from_status, change.to_status)
        for change in diff.changed_cases
    ]
    assert changes == [
        ("c1", "passed", "escalated"),
        ("c2", "escalated", "passed"),
    ]
    assert diff.changed_cases[0].failure_codes == ["G5_PII_LEAK"]


def test_unchanged_cases_are_not_listed() -> None:
    cases = [case("c1", "passed")]
    diff = compare(
        report("a", metrics=metrics(), cases=cases),
        report("b", metrics=metrics(), cases=cases),
    )

    assert diff.changed_cases == []


def test_hash_changes_are_flagged() -> None:
    diff = compare(
        report("a", metrics=metrics()),
        report("b", metrics=metrics(), preset_hash="c" * 64, dataset_hash="d" * 64),
    )

    assert diff.preset_hash_changed is True
    assert diff.dataset_hash_changed is True


def test_loading_accepts_objects_paths_and_json_text(tmp_path: Path) -> None:
    source = report("a", metrics=metrics())
    target = tmp_path / "report.json"
    target.write_text(source.model_dump_json(), encoding="utf-8")

    assert load_report(source) == source
    assert load_report(target) == source
    assert load_report(source.model_dump_json()) == source


def test_markdown_explains_an_incomparable_comparison() -> None:
    diff = compare(
        report("a", metrics=metrics()),
        report(
            "b",
            metrics=metrics(pass_rate_final=1.0),
            preset_hash="c" * 64,
            dataset_hash="d" * 64,
        ),
    )
    text = render_markdown(diff)

    assert "**Dataset changed.**" in text
    assert "**Preset changed.**" in text
    assert "| Pass, final |" in text
    assert "better" in text


def test_comparison_markdown_is_written_to_disk(tmp_path: Path) -> None:
    diff = compare(report("a", metrics=metrics()), report("b", metrics=metrics()))
    target = write_markdown(diff, tmp_path / "diff.md")

    assert "No case changed its status" in target.read_text(encoding="utf-8")
