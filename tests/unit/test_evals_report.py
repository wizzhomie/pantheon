"""The report: facts that make it distrusted, in a form a human reads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from support import (
    BAD_SCORE,
    GOOD_SCORE,
    ScriptedAgent,
    case_record,
    eval_case,
    toy_preset,
    toy_scoring,
)

from qagent import (
    Dataset,
    EvalReport,
    JudgePolicy,
    MetaEval,
    QAConfig,
    guard,
)
from qagent.contracts.evals import Metrics
from qagent.evals.report import (
    build_report,
    git_revision,
    make_run_id,
    render_markdown,
    write_json,
    write_markdown,
)

AT = datetime(2026, 11, 22, 17, 0, tzinfo=UTC)


def make_guarded(rubric: object):
    preset = toy_preset(rubric, scoring=toy_scoring())
    return guard(
        ScriptedAgent({"qty": 2}),
        preset,
        config=QAConfig(judge=JudgePolicy(enabled=False)),
    )


def dataset() -> Dataset:
    return Dataset(name="toy", cases=[eval_case("c1"), eval_case("c2")])


def make_report(
    rubric: object,
    *,
    scores: tuple = (GOOD_SCORE, GOOD_SCORE),
    meta_eval: MetaEval | None = None,
) -> EvalReport:
    records = [
        case_record(f"c{index + 1}", score=score) for index, score in enumerate(scores)
    ]
    return build_report(
        records,
        guarded=make_guarded(rubric),
        dataset=dataset(),
        created_at=AT,
        revision="deadbee",
        meta_eval=meta_eval,
    )


def test_the_report_carries_the_hashes_and_the_models(rubric: object) -> None:
    report = make_report(rubric)

    assert report.run_id == "20261122T170000-guarded-deadbee"
    assert report.created_at == AT
    assert report.git_sha == "deadbee"
    assert report.mode == "guarded"
    assert report.preset_name == "toy"
    assert report.preset_hash == make_guarded(rubric).preset.fingerprint()
    assert report.dataset_hash == dataset().digest()
    assert report.llm == {"agent": "toy-model", "judge": "off"}
    assert report.metrics.pass_rate_final == 1.0
    assert [case.case_id for case in report.cases] == ["c1", "c2"]


def test_the_markdown_shows_quality_and_loop_numbers(rubric: object) -> None:
    text = render_markdown(make_report(rubric))

    assert "# Evaluation report — `guarded`" in text
    assert "## Quality (ground truth from the golden values)" in text
    assert "| Pass, final | 100.0% |" in text
    assert "## Loop" in text
    assert "| Escalation rate | 0.0% |" in text
    assert "## Cases" in text
    assert "| `c1` | happy_path | passed | yes |" in text


def test_the_markdown_names_a_zero_denominator(rubric: object) -> None:
    report = make_report(rubric, scores=(BAD_SCORE, BAD_SCORE))
    text = render_markdown(report)

    assert "No case passed" in text
    assert "cost per passed case is undefined" in text


def test_the_markdown_includes_calibration_when_labels_exist(rubric: object) -> None:
    meta = MetaEval(
        n=3,
        agreement=0.67,
        cohens_kappa=0.4,
        judge_precision=0.8,
        judge_recall=0.7,
        false_pass_ids=["c9"],
        false_fail_ids=["c10"],
    )
    text = render_markdown(make_report(rubric, meta_eval=meta))

    assert "## Judge calibration (hand labels)" in text
    assert "Cohen's kappa" in text
    assert "`c9`" in text and "`c10`" in text


def test_an_unknown_commit_is_said_out_loud(rubric: object) -> None:
    report = build_report(
        [case_record("c1")],
        guarded=make_guarded(rubric),
        dataset=dataset(),
        created_at=AT,
        revision="",
    )
    text = render_markdown(report)

    assert "`unknown`" in text
    assert "Commit is unknown" in text


def test_json_round_trip_keeps_the_report(rubric: object, tmp_path: Path) -> None:
    report = make_report(rubric)
    target = write_json(report, tmp_path / "nested" / "report.json")

    restored = EvalReport.model_validate_json(target.read_text(encoding="utf-8"))

    assert restored == report


def test_markdown_is_written_to_disk(rubric: object, tmp_path: Path) -> None:
    target = write_markdown(make_report(rubric), tmp_path / "reports" / "run.md")

    assert target.read_text(encoding="utf-8").startswith("# Evaluation report")


def test_run_id_is_sortable_and_short(rubric: object) -> None:
    assert make_run_id("baseline", AT, "deadbeef") == "20261122T170000-baseline-deadbee"
    assert make_run_id("guarded", AT, "") == "20261122T170000-guarded-nogit"


def test_git_revision_reads_the_environment(monkeypatch: object) -> None:
    monkeypatch.setenv("QAGENT_GIT_SHA", "abc1234")  # type: ignore[attr-defined]

    assert git_revision() == "abc1234"


def test_metrics_are_aggregated_from_the_records(rubric: object) -> None:
    report = make_report(rubric, scores=(GOOD_SCORE, BAD_SCORE))

    assert isinstance(report.metrics, Metrics)
    assert report.metrics.cases == 2
    assert report.metrics.pass_rate_final == 0.5
    assert report.metrics.false_pass_rate == 0.5


REPORTS = Path(__file__).resolve().parents[2] / "reports"


def test_every_committed_report_renders_its_own_markdown() -> None:
    """A report is a pair: the markdown must be the rendering of the json next to it.

    The pair drifted once — a replay rewrote the markdown while the live json stayed —
    and a reader could not tell which numbers were the run's.
    """
    import json

    checked = 0
    for path in sorted(REPORTS.rglob("*.json")):
        if path.stem.startswith("sample_") or "diff" in path.stem:
            continue  # stub reports carry a hand-written banner; diffs have another shape
        report = EvalReport.model_validate(json.loads(path.read_text(encoding="utf-8")))
        assert path.with_suffix(".md").read_text(encoding="utf-8") == render_markdown(report)
        checked += 1
    assert checked >= 4
