"""The CLI: evaluate a dataset and compare reports through a project-owned factory."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from support import eval_case

from agentqa import Dataset, EvalReport
from agentqa.cli import main

SPEC_SOURCE = '''
from pydantic import BaseModel

from agentqa import (
    Case,
    Criterion,
    FieldSpec,
    FunctionJudge,
    JudgeVerdict,
    Preset,
    QAConfig,
    Rubric,
    ScoringSpec,
    guard,
)


class Out(BaseModel):
    qty: int = 0


RUBRIC = Rubric(
    criteria=[Criterion(id="grounding", title="Grounding", description="g", weight=1.0)]
)


async def AGENT(case: Case, feedback: object) -> dict[str, int]:
    return {"qty": 2}


def build_guarded():
    preset = Preset(
        name="cli_toy",
        output_model=Out,
        rubric=RUBRIC,
        checks=[],
        agent_model="toy-model",
        scoring=ScoringSpec(fields=[FieldSpec(path="qty", comparator="numeric")]),
    )
    judge = FunctionJudge(
        lambda case, output: JudgeVerdict.from_rubric(RUBRIC, {"grounding": 5})
    )
    return guard(AGENT, preset, judge=judge, config=QAConfig())


def not_guarded():
    return 42
'''


def write_spec(tmp_path: Path, monkeypatch: object) -> str:
    name = f"cli_spec_{uuid4().hex[:8]}"
    (tmp_path / f"{name}.py").write_text(SPEC_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))  # type: ignore[attr-defined]
    return f"{name}:build_guarded"


def write_dataset(tmp_path: Path) -> Path:
    dataset = Dataset(
        cases=[
            eval_case("c1", payload="x", expected={"qty": 2}),
            eval_case("c2", payload="y", expected={"qty": 2}),
        ]
    )
    return dataset.save(tmp_path / "dataset.jsonl")


def test_eval_writes_json_and_markdown(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    spec = write_spec(tmp_path, monkeypatch)
    out = tmp_path / "report.json"
    markdown = tmp_path / "report.md"

    code = main(
        [
            "eval",
            "--spec",
            spec,
            "--dataset",
            str(write_dataset(tmp_path)),
            "--out",
            str(out),
            "--markdown",
            str(markdown),
        ]
    )

    assert code == 0
    report = EvalReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert report.metrics.cases == 2
    assert report.metrics.pass_rate_final == 1.0
    assert markdown.exists()
    assert "pass 100.0%" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_compare_reads_two_reports(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    spec = write_spec(tmp_path, monkeypatch)
    dataset = str(write_dataset(tmp_path))
    baseline = tmp_path / "baseline.json"
    guarded = tmp_path / "guarded.json"
    common = ["--spec", spec, "--dataset", dataset]

    assert main(["eval", *common, "--out", str(baseline), "--mode", "baseline"]) == 0
    assert main(["eval", *common, "--out", str(guarded)]) == 0

    diff = tmp_path / "diff.md"
    code = main(["compare", str(baseline), str(guarded), "--markdown", str(diff)])

    assert code == 0
    assert "0 changed case(s)" in capsys.readouterr().out  # type: ignore[attr-defined]
    text = diff.read_text(encoding="utf-8")
    assert "Report comparison" in text
    assert "**Dataset changed.**" not in text


def test_an_unimportable_spec_fails_cleanly(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    spec = write_spec(tmp_path, monkeypatch).replace("cli_spec_", "missing_spec_")

    code = main(
        [
            "eval",
            "--spec",
            spec,
            "--dataset",
            str(write_dataset(tmp_path)),
            "--out",
            str(tmp_path / "r.json"),
        ]
    )

    assert code == 1
    assert "agentqa:" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_a_factory_returning_the_wrong_type_fails_cleanly(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    name, _, _ = write_spec(tmp_path, monkeypatch).partition(":")

    code = main(
        [
            "eval",
            "--spec",
            f"{name}:not_guarded",
            "--dataset",
            str(write_dataset(tmp_path)),
            "--out",
            str(tmp_path / "r.json"),
        ]
    )

    assert code == 1
    assert "must return a GuardedAgent" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_a_malformed_spec_is_rejected(tmp_path: Path, capsys: object) -> None:
    code = main(
        [
            "eval",
            "--spec",
            "nocolon",
            "--dataset",
            str(write_dataset(tmp_path)),
            "--out",
            str(tmp_path / "r.json"),
        ]
    )

    assert code == 1
    assert "package.module:factory" in capsys.readouterr().err  # type: ignore[attr-defined]
