"""The R&D cycle: one question, a matrix of runs, numbers, and a decision the code makes.

What these tests hold: the matrix expands parameters and repeats, the aggregate follows the
declaration, the threshold decides the hypothesis and not the mood, a run with no number is
"failed" rather than "refuted", and the next experiment comes from the file's table.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness import box as boxes
from harness import study
from harness.cli import main

WHEN = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)


def make_box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "probe"
    project.mkdir()
    return boxes.new_box(tmp_path / "castra", project)


def study_text(
    *,
    argv: list[str] | None = None,
    params: dict[str, list[str]] | None = None,
    repeats: int = 1,
    threshold: float = 70,
    expected: float = 80,
    on_confirmed: str = "close",
    on_refuted: str = "close",
    metrics: str | None = None,
    extra: str = "",
) -> str:
    command = argv or [sys.executable, "-c", "print('quality={profile}')"]
    matrix = params or {"profile": ["80", "60"]}
    pairs = ", ".join(f"{key} = {json.dumps(values)}" for key, values in matrix.items())
    inline = "{ " + pairs + " }"
    metric_block = metrics or f"""\
[[experiment.metric]]
name = "quality"
unit = "%"
direction = "up"
threshold = {threshold}
expected = {expected}
pattern = 'quality=([0-9.]+)'
aggregate = "median"
"""
    return f"""\
schema_version = 1
id = "q-cost"
question = "the wrapper is cheaper"
closes_when = "e1 answers it"

[[experiment]]
id = "e1"
hypothesis = "a is cheaper"
prediction = "quality 80"
on_confirmed = {json.dumps(on_confirmed)}
on_refuted = {json.dumps(on_refuted)}

{metric_block}
[[experiment.run]]
argv = {json.dumps(command)}
params = {inline}
repeats = {repeats}
{extra}"""


def write_study(project: Path, text: str) -> Path:
    directory = project / ".pantheon" / "studia"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "q-cost.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_valid_research_is_read_with_its_matrix(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text())
    research = study.read(box.project, "q-cost")
    assert research.question == "the wrapper is cheaper"
    assert research.experiments[0].id == "e1"
    assert research.experiments[0].runs[0].params == {"profile": ("80", "60")}


def test_the_matrix_expands_parameters_and_repeats(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text(params={"profile": ["80", "60"]}, repeats=2))
    research = study.read(box.project, "q-cost")
    outcome = study.run_experiment(box, research, research.experiments[0], when=WHEN)
    assert len(outcome.cells) == 4
    assert [cell.numbers["quality"] for cell in outcome.cells] == [80, 80, 60, 60]
    assert outcome.metrics[0].value == 70
    assert outcome.verdict == "confirmed"
    assert outcome.metrics[0].expected_held is False, "70 missed the declared 80"


def test_missing_the_threshold_refutes_the_hypothesis(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text(threshold=75))
    research = study.read(box.project, "q-cost")
    outcome = study.run_experiment(box, research, research.experiments[0], when=WHEN)
    assert outcome.verdict == "refuted"
    assert "quality" in outcome.reason


def test_a_run_without_a_number_is_failed_not_refuted(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(
        box.project,
        study_text(argv=[sys.executable, "-c", "print('nothing {profile}')"]),
    )
    research = study.read(box.project, "q-cost")
    outcome = study.run_experiment(box, research, research.experiments[0], when=WHEN)
    assert outcome.verdict == "failed"
    assert outcome.next is None


def test_the_decision_comes_from_the_table(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    text = study_text(on_confirmed="e2") + """

[[experiment]]
id = "e2"
hypothesis = "b is better"
on_confirmed = "close"
on_refuted = "close"

[[experiment.metric]]
name = "quality"
unit = "%"
direction = "up"
threshold = 70
pattern = 'quality=([0-9.]+)'

[[experiment.run]]
argv = ["true"]
"""
    write_study(box.project, text)
    research = study.read(box.project, "q-cost")
    outcome = study.run_experiment(box, research, research.experiments[0], when=WHEN)
    assert outcome.verdict == "confirmed"
    assert outcome.next == "e2"
    following = study.next_experiment(research, study.outcome_json(outcome))
    assert following.id == "e2"


def test_the_two_verdicts_are_separate(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text())
    research = study.read(box.project, "q-cost")
    outcome = study.run_experiment(box, research, research.experiments[0], when=WHEN)
    body = study.outcome_json(outcome)
    assert body["verdict"] == "confirmed"
    code = body["code"]
    assert isinstance(code, dict) and code["status"] == "not routed"
    cells = body["cells"]
    assert isinstance(cells, list) and isinstance(cells[0], dict)
    assert str(cells[0]["stdout"]).startswith("cells/")
    record = Path(str(body["record"])) / "result.json"
    assert record.is_file()


def test_a_closed_research_refuses_the_next_run(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text())
    research = study.read(box.project, "q-cost")
    outcome = study.run_experiment(box, research, research.experiments[0], when=WHEN)
    assert outcome.next == "close"
    with pytest.raises(boxes.HarnessError, match="closed"):
        study.next_experiment(research, study.outcome_json(outcome))


def test_an_unknown_decision_target_is_refused(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text(on_confirmed="e9"))
    with pytest.raises(boxes.HarnessError, match="e9"):
        study.read(box.project, "q-cost")


def test_a_pattern_needs_exactly_one_group(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    metrics = """\
[[experiment.metric]]
name = "quality"
direction = "up"
threshold = 70
pattern = 'quality=([0-9.]+)-([0-9.]+)'
"""
    write_study(box.project, study_text(metrics=metrics))
    with pytest.raises(boxes.HarnessError, match="exactly one group"):
        study.read(box.project, "q-cost")


def test_a_parameter_no_argv_uses_is_refused(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_study(
        box.project,
        study_text(
            argv=[sys.executable, "-c", "print('quality=80')"],
            params={"profile": ["80"]},
        ),
    )
    with pytest.raises(boxes.HarnessError, match="that no argv uses"):
        study.read(box.project, "q-cost")


def test_the_cli_lists_and_runs_a_study(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box = make_box(tmp_path)
    write_study(box.project, study_text())
    assert main(["--castra", str(box.root.parent), "study", box.name]) == 0
    assert "not started — next e1" in capsys.readouterr().out
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "study",
                box.name,
                "--run",
                "q-cost",
                "--json",
            ]
        )
        == 0
    )
    body = json.loads(capsys.readouterr().out)
    assert body["verdict"] == "confirmed" and body["next"] == "close"
    assert main(["--castra", str(box.root.parent), "study", box.name]) == 0
    assert "e1 confirmed — closed" in capsys.readouterr().out


def test_no_research_under_the_project_is_said_plainly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    assert main(["--castra", str(box.root.parent), "study", box.name]) == 0
    assert "no research in" in capsys.readouterr().out
