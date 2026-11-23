"""The second example: a review must find the planted bug and invent nothing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from examples.code_review import runner
from examples.code_review.build_dataset import DATASET_PATH
from examples.code_review.checks import (
    CODE_LINE_OUT_OF_RANGE,
    CODE_QUOTE_INVENTED,
    CODE_QUOTE_OFF_LINE,
    CODE_VERDICT_CONTRADICTS,
    AnchorCheck,
    VerdictCheck,
)
from examples.code_review.schema import Finding, Review
from examples.code_review.scoring import score_review
from qagent import Case, CheckContext, Dataset, evaluate
from qagent.cli import main
from qagent.evals.scoring import CaseScore

CONFIG = Path(__file__).resolve().parents[2] / "examples" / "code_review" / "qagent.toml"
NOW = datetime(2026, 11, 23, tzinfo=UTC)

SOURCE = """\
def remaining_amount(total: float, paid: float) -> float:
    return round(total + paid, 2)
"""


def _ctx(payload: str = SOURCE) -> CheckContext:
    return CheckContext(case=Case(case_id="c-1", payload=payload), now=NOW)


def _review(line: int, quote: str, verdict: str = "problems") -> Review:
    return Review(
        verdict=verdict,  # type: ignore[arg-type]
        findings=[Finding(line=line, message="a defect", quote=quote)],
    )


def test_the_committed_dataset_matches_the_builder() -> None:
    committed = Dataset.load(DATASET_PATH)
    # The builder is deterministic: regenerating from the fixtures gives the same file.
    import tempfile

    from examples.code_review.build_dataset import build as build_text

    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "dataset.jsonl"
        target.write_text(build_text(), encoding="utf-8")
        assert Dataset.load(target).digest() == committed.digest()
    assert len(committed.cases) == 18
    broken = [case for case in committed.cases if case.expected["bug_lines"]]
    assert len(broken) == 14
    assert {case.category for case in committed.cases} == {
        "happy_path",
        "edge_case",
        "adversarial",
    }


def test_a_quote_must_be_the_line_it_points_at() -> None:
    check = AnchorCheck()

    good = check(_review(2, "    return round(total + paid, 2)"), _ctx())
    assert good == []

    invented = check(_review(2, "    return total - paid"), _ctx())
    assert [issue.code for issue in invented] == [CODE_QUOTE_INVENTED]

    off_line = check(_review(1, "    return round(total + paid, 2)"), _ctx())
    assert [issue.code for issue in off_line] == [CODE_QUOTE_OFF_LINE]

    past_end = check(_review(9, "    return round(total + paid, 2)"), _ctx())
    assert [issue.code for issue in past_end] == [CODE_LINE_OUT_OF_RANGE]


def test_the_verdict_cannot_contradict_the_findings() -> None:
    check = VerdictCheck()

    contradiction = check(Review(verdict="problems", findings=[]), _ctx())
    assert [issue.code for issue in contradiction] == [CODE_VERDICT_CONTRADICTS]
    assert contradiction[0].severity == "error"

    warned = check(_review(2, "    return round(total + paid, 2)", verdict="ok"), _ctx())
    assert [issue.code for issue in warned] == [CODE_VERDICT_CONTRADICTS]
    assert warned[0].severity == "warning"

    assert check(Review(verdict="ok", findings=[]), _ctx()) == []


def test_scoring_counts_found_missed_and_invented() -> None:
    found = score_review(_review(2, "x"), {"bug_lines": [2]})
    assert isinstance(found, CaseScore)
    assert found.passed is True and found.field_accuracy == 1.0

    missed = score_review(Review(verdict="ok", findings=[]), {"bug_lines": [2]})
    assert missed.passed is False
    assert missed.missing == ["line 2"]

    invented = score_review(
        Review(verdict="problems", findings=[Finding(line=2, message="a", quote="q"),
                                             Finding(line=8, message="b", quote="r")]),
        {"bug_lines": [2]},
    )
    assert invented.passed is False
    assert invented.wrong == ["line 8"]

    clean = score_review(Review(verdict="ok", findings=[]), {"bug_lines": []})
    assert clean.passed is True

    noisy = score_review(_review(1, "x"), {"bug_lines": []})
    assert noisy.passed is False and noisy.field_accuracy == 0.0


def test_the_demo_walks_the_loop_paths() -> None:
    guarded = runner.build_guarded()
    dataset = Dataset.load(DATASET_PATH)
    report = asyncio.run(evaluate(guarded, dataset, concurrency=1))
    by_id = {case.case_id: case for case in report.cases}

    assert by_id["cr-05"].status == "passed_after_retry"
    assert by_id["cr-09"].status == "passed_after_retry"
    # The model simply says "ok"; nothing deterministic can see the bug, so the loop
    # accepts a wrong answer. That single case is the false pass of this report.
    assert by_id["cr-03"].status == "passed"
    assert by_id["cr-03"].field_accuracy == 0.0
    assert report.metrics.false_pass_rate > 0


def test_the_config_file_describes_this_example(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["check", "--config", str(CONFIG)]) == 0
    assert "code_review" in capsys.readouterr().out
