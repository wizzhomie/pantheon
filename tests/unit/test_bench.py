"""The holdout bench: the contour graded on answers written down before the run.

The tests that matter are about the numbers a reader may quote: a planted defect that comes
out PASS is a miss, a clean change that comes out HOLD is a false alarm, and neither can be
quietly counted as the other. The suite itself is checked as data, so a case that no longer
matches reality fails loudly instead of being adjusted.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qagent.bench import BenchReport, CaseResult, discover, run_suite, write_bench
from qagent.bench.runner import render_bench
from qagent.cli import main

SUITE = Path(__file__).resolve().parents[2] / "examples" / "holdout"
SCRIPTED = "qagent.review.scripted:from_env"
LABEL = "scripted (no model call)"


@pytest.fixture(autouse=True)
def _tools_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cases run the project's own tools; the bench must find them the way a user would."""
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", '{"findings": []}')


def one_case(
    tmp_path: Path, name: str, expected: str, *, needs_reviewer: bool = False
) -> Path:
    """A suite with the real base and exactly one case, with a chosen answer."""
    suite = tmp_path / "suite"
    shutil.copytree(SUITE / "base", suite / "base")
    shutil.copytree(SUITE / "cases" / name, suite / "cases" / name)
    reviewer = "needs_reviewer = true\n" if needs_reviewer else ""
    (suite / "cases" / name / "case.toml").write_text(
        f'expected = "{expected}"\n{reviewer}note = "set by the test"\n', encoding="utf-8"
    )
    return suite


def test_the_suite_is_discovered_as_data() -> None:
    cases = discover(SUITE)

    assert [case.id for case in cases] == [
        "control-clean",
        "control-unrelated",
        "mutant-arithmetic",
        "mutant-sql",
        "mutant-types",
        "reviewer-injection",
        "reviewer-percent",
        "reviewer-swallow",
    ]
    assert [case.expected for case in cases].count("hold") == 3
    assert [case.expected for case in cases].count("finding") == 3
    assert sum(1 for case in cases if case.needs_reviewer) == 3
    assert all(case.note for case in cases)


def test_the_bench_reproduces_the_committed_answers() -> None:
    report, target = asyncio.run(run_suite(SUITE, reviewer=SCRIPTED, model=LABEL))

    assert report.passed, [(case.id, case.actual) for case in report.cases]
    assert (report.caught, report.missed, report.false_alarms) == (3, 0, 0)
    assert report.summary() == "3 of 3 planted defects held, 0 of 2 clean changes held"
    assert target is None


def test_the_layers_partition_the_suite() -> None:
    cases = discover(SUITE)

    checks_layer = [case.id for case in cases if not case.needs_reviewer]
    reviewer_layer = [case.id for case in cases if case.needs_reviewer]

    assert len(checks_layer) == 5
    assert reviewer_layer == ["reviewer-injection", "reviewer-percent", "reviewer-swallow"]


def test_a_defect_the_checks_cannot_see_is_not_claimed_as_caught(tmp_path: Path) -> None:
    """With a scripted reviewer a reviewer-only case must pass its checks and still miss."""
    suite = one_case(tmp_path, "reviewer-injection", "finding", needs_reviewer=True)

    report, _ = asyncio.run(run_suite(suite, reviewer=SCRIPTED, model=LABEL, layer="reviewer"))

    assert report.cases[0].actual == "pass"
    assert (report.cases[0].confirmed, report.cases[0].detected) == (0, False)
    assert (report.detected, report.missed) == (0, 1)
    assert report.summary() == "0 of 1 reviewer cases detected"


def test_a_miss_and_a_false_alarm_are_different_numbers() -> None:
    missed = CaseResult(id="m", expected="hold", actual="pass_with_conditions", matched=False)
    false_alarm = CaseResult(id="c", expected="pass", actual="hold", matched=False)
    odd = CaseResult(id="o", expected="pass", actual="inconclusive", matched=False)
    report = BenchReport(
        suite="test", reviewer=LABEL, started_at=datetime.now(UTC), cases=[missed, false_alarm, odd]
    )

    assert (missed.missed, missed.caught) == (True, False)
    assert (false_alarm.false_alarm, false_alarm.missed) == (True, False)
    assert (odd.unexpected, odd.missed, odd.false_alarm) == (True, False, False)
    assert (report.caught, report.missed, report.false_alarms, report.unexpected) == (0, 1, 1, 1)
    assert report.passed is False


def test_a_report_names_every_case_that_did_not_match() -> None:
    ok = CaseResult(id="ok", expected="hold", actual="hold", matched=True)
    off = CaseResult(id="odd", expected="pass", actual="pass_with_conditions", matched=False)
    report = BenchReport(
        suite="test", reviewer=LABEL, started_at=datetime.now(UTC), cases=[ok, off]
    )

    markdown = render_bench(report)

    assert "Result: 1 of 2 cases matched the suite; `odd` did not." in markdown
    assert "**off the answer**" in markdown


def test_a_matching_report_has_no_result_line() -> None:
    report, _ = asyncio.run(run_suite(SUITE, reviewer=SCRIPTED, model=LABEL))

    assert "Result:" not in render_bench(report)


def test_the_report_is_a_rendering_of_the_json(tmp_path: Path) -> None:
    report, _ = asyncio.run(run_suite(SUITE, reviewer=SCRIPTED, model=LABEL))
    json_path, md_path = write_bench(report, tmp_path)

    stored = BenchReport.model_validate(json.loads(json_path.read_text(encoding="utf-8")))
    assert stored.summary() == report.summary()

    markdown = md_path.read_text(encoding="utf-8")
    assert report.summary() in markdown
    assert "| Case | Expected | Actual | Result | Why |" in markdown
    for case in report.cases:
        assert f"`{case.id}`" in markdown


def test_the_cli_reports_agreement_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = one_case(tmp_path, "control-clean", "pass")

    exit_code = main(["bench", "--suite", str(suite), "--reviewer", SCRIPTED, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["cases"][0]["matched"] is True
    assert payload["cases"][0]["actual"] == "pass"


def test_the_cli_stops_on_a_wrong_committed_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = one_case(tmp_path, "control-clean", "hold")

    exit_code = main(["bench", "--suite", str(suite), "--reviewer", SCRIPTED])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "0 of 1 planted defects held" in output
    assert "MISS" in output


@pytest.mark.parametrize("name", ["holdout", "holdout_reviewer", "holdout_all"])
def test_a_committed_bench_is_the_rendering_of_its_json(name: str) -> None:
    """The committed pair must not drift: a bench report is evidence, not a note."""
    target = Path(__file__).resolve().parents[2] / "reports" / name
    stored = BenchReport.model_validate(
        json.loads((target / "bench.json").read_text(encoding="utf-8"))
    )

    assert (target / "bench.md").read_text(encoding="utf-8") == render_bench(stored)
