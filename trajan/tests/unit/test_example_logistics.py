"""The example is documentation that runs: if it rots, the README is lying.

These tests are deliberately end-to-end. They run the demo agent through the real loop, the
real scoring and the real report builder, so the numbers in the committed sample report are
reproducible from the test suite.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from examples.logistics_intake import runner
from examples.logistics_intake.build_dataset import DATASET_PATH, build
from examples.logistics_intake.demo import DEMO_MODEL, DEMO_PRESET, build_demo_guarded
from examples.logistics_intake.preset import PRESET, SCORING, build_retry
from examples.logistics_intake.runner import build_guarded
from qagent import Case, Dataset, EvalReport, FakeClient, Feedback, Issue, ReplayClient, evaluate
from qagent.checks.paths import MISSING, get_path

REPORTS = Path(__file__).resolve().parents[2] / "reports"


def test_the_committed_dataset_matches_the_builder() -> None:
    committed = Dataset.load(DATASET_PATH)
    built = build()

    assert committed.digest() == built.digest()
    assert len(committed.cases) == 32
    assert sum(case.human_verdict is not None for case in committed.cases) == 18
    assert {case.category for case in committed.cases} == {
        "happy_path",
        "edge_case",
        "adversarial",
    }


def test_every_scored_path_is_present_in_every_golden_answer() -> None:
    """A spec path the golden data never has would score every case as wrong."""
    for case in build().cases:
        for field in SCORING.fields:
            value = get_path(case.expected, field.path)
            assert value is not MISSING, f"{case.case_id}: {field.path}"


def test_demo_preset_is_the_real_preset_under_a_stub_model_name() -> None:
    assert PRESET.agent_model == "deepseek-flash"
    assert DEMO_PRESET.agent_model == DEMO_MODEL
    assert DEMO_PRESET.scoring is PRESET.scoring
    assert DEMO_PRESET.checks == PRESET.checks


def test_retry_hook_adds_domain_advice_and_respects_the_cap() -> None:
    issue = Issue(
        code="L2_REEFFER_WITHOUT_TEMPERATURE",
        severity="error",
        source="check",
        message="a reefer request has cargo without a temperature",
    )
    feedback = Feedback(
        attempt=1,
        issues=[issue],
        instructions=[f"instruction {index}" for index in range(10)],
    )

    adjusted = build_retry(Case(case_id="case-1", payload="body"), feedback)

    assert len(adjusted.instructions) == 10
    assert "reefer temperature" in adjusted.instructions[0].casefold()


def test_the_cli_factory_builds_the_demo_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QAGENT_LLM", raising=False)

    guarded = build_guarded()

    assert guarded.mode == "guarded"
    assert guarded.preset.agent_model == DEMO_MODEL


def _demo_report() -> EvalReport:
    return asyncio.run(
        evaluate(build_demo_guarded(), build(), concurrency=1, revision="test")
    )


def test_the_demo_run_reproduces_the_sample_numbers() -> None:
    report = _demo_report()
    metrics = report.metrics

    assert metrics.cases == 32
    assert metrics.pass_rate_final == pytest.approx(30 / 32, abs=1e-4)
    assert metrics.pass_rate_first_attempt == pytest.approx(21 / 32, abs=1e-4)
    assert metrics.false_pass_rate == pytest.approx(1 / 32, abs=1e-4)
    assert metrics.escalation_rate == pytest.approx(2 / 32, abs=1e-4)
    assert metrics.retry_recovery_rate == pytest.approx(8 / 9, abs=1e-4)

    escalated = {case.case_id for case in report.cases if case.status == "escalated"}
    assert escalated == {"ed-04", "adv-05"}
    false_passes = {
        case.case_id
        for case in report.cases
        if case.status in ("passed", "passed_after_retry") and not case.final_passed
    }
    assert false_passes == {"adv-01"}


def test_the_committed_sample_report_is_labelled_as_a_stub() -> None:
    report = (REPORTS / "sample_guarded.md").read_text(encoding="utf-8")

    assert report.startswith("> **Stub run — no model calls.**")
    assert "demo-stub" in report
    assert (REPORTS / "sample_baseline.json").exists()
    assert "Stub run" in (REPORTS / "sample_diff.md").read_text(encoding="utf-8")


def test_record_mode_appends_live_answers_to_the_cassette(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = FakeClient()
    recorder = runner._recording(inner)

    assert isinstance(recorder, ReplayClient)
    assert recorder.mode == "record"
    assert recorder.inner is inner

    monkeypatch.setenv("QAGENT_RECORD", "1")
    assert runner._record_requested() is True

    monkeypatch.setenv("QAGENT_RECORD", "TRUE")
    assert runner._record_requested() is True

    monkeypatch.setenv("QAGENT_RECORD", "0")
    assert runner._record_requested() is False
    monkeypatch.delenv("QAGENT_RECORD")
    assert runner._record_requested() is False
