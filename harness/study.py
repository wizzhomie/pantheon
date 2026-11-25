"""The R&D cycle: one question, experiments with a prediction, runs with numbers, a decision.

A study is a file in the project (``.pantheon/studia/<id>.toml``): the question, when it counts
as closed, and the experiments. An experiment declares a hypothesis, metrics with a unit, a
direction, a threshold and a pattern that pulls the number out of a command's output, and runs
with a parameter matrix and repeats. The code runs the matrix, aggregates the numbers the way
the metric declared, compares them with the thresholds, and chooses the next experiment by a
table — ``on_confirmed`` or ``on_refuted`` — instead of guessing.

Two verdicts, and they are not the same: the checks judge the code (a study declares no change
yet, so it says that no gate ran), and the numbers judge the hypothesis. A refuted hypothesis
with green checks is a normal success of the cycle, not a broken build. A run that produced no
number is ``failed``, which is neither confirmation nor refutation.

Expectation is declared before the run: every metric may carry ``expected``, and the result
compares it with what happened. The money side is a number only when the command prints it:
the box measures time, not an interpreter's bill.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes

SCHEMA_VERSION = 1
DIR = Path(".pantheon") / "studia"
RECORDS = Path("tabularium") / "studia"
DIRECTIONS = frozenset({"up", "down"})
AGGREGATES = frozenset({"median", "mean", "min", "max"})
DECISIONS = frozenset({"close"})
DEFAULT_TIMEOUT = 300
MAX_FILES = 50


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One number the experiment reads from a command, and what counts as a good value."""

    name: str
    unit: str
    direction: str
    threshold: float
    expected: float | None
    pattern: str
    aggregate: str


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One command of the matrix: a parameter table, how many repeats, where and with what env."""

    argv: tuple[str, ...]
    params: dict[str, tuple[str, ...]]
    repeats: int
    cwd: str | None
    env: dict[str, str]
    timeout: int


@dataclass(frozen=True, slots=True)
class Experiment:
    """A hypothesis with a prediction, the metrics that judge it, and where each verdict leads."""

    id: str
    hypothesis: str
    prediction: str | None
    metrics: tuple[MetricSpec, ...]
    runs: tuple[RunSpec, ...]
    on_confirmed: str
    on_refuted: str
    on_failed: str | None


@dataclass(frozen=True, slots=True)
class Research:
    """The question, when it counts as closed, and the experiments that answer it."""

    path: Path
    id: str
    question: str
    closes_when: str
    budget: dict[str, int]
    experiments: tuple[Experiment, ...]

    def experiment(self, name: str) -> Experiment | None:
        for item in self.experiments:
            if item.id == name:
                return item
        return None


@dataclass(frozen=True, slots=True)
class Cell:
    """One run of the matrix: what it was asked, what it said, and what it cost in time."""

    index: int
    params: dict[str, str]
    repeat: int
    exit_code: int | None
    seconds: float
    numbers: dict[str, float]
    error: str | None
    stdout: Path
    stderr: Path


@dataclass(frozen=True, slots=True)
class MetricResult:
    """The aggregate of one metric against its threshold and the number declared beforehand."""

    spec: MetricSpec
    value: float | None
    confirmed: bool

    @property
    def expected_held(self) -> bool | None:
        if self.value is None or self.spec.expected is None:
            return None
        if self.spec.direction == "up":
            return self.value >= self.spec.expected
        return self.value <= self.spec.expected


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one experiment said: its verdict, the numbers, the checks, and where it leads."""

    research: Research
    experiment: Experiment
    cells: tuple[Cell, ...]
    metrics: tuple[MetricResult, ...]
    verdict: str
    reason: str
    code: str
    next: str | None
    seconds: float
    at: str
    directory: Path


def files(project: Path) -> tuple[Path, ...]:
    """The research files the project declares, oldest name first."""
    directory = project / DIR
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("*.toml")))[:MAX_FILES]


def read(project: Path, value: str) -> Research:
    """Read a research by id or by path; a name that resolves to nothing is a refusal."""
    direct = Path(value).expanduser()
    path = direct if direct.is_file() else project / DIR / f"{value}.toml"
    if not path.is_file():
        known = ", ".join(item.stem for item in files(project)) or "none"
        raise boxes.HarnessError(
            f"no research {value!r}: {path} does not exist; the project declares {known}"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"the research {path} does not parse: {error}") from error
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(f"the research {path} is not schema_version {SCHEMA_VERSION}")
    identifier = _required(str(data.get("id", path.stem)), "id", path)
    question = _required(_string(data.get("question")), "question", path)
    closes_when = _required(_string(data.get("closes_when")), "closes_when", path)
    budget = _budget(data.get("budget"), path)
    raw_experiments = data.get("experiment")
    if not isinstance(raw_experiments, list) or not raw_experiments:
        raise boxes.HarnessError(f"the research {path} declares no [[experiment]]")
    experiments = tuple(
        _experiment(item, path, index) for index, item in enumerate(raw_experiments, start=1)
    )
    seen: list[str] = []
    for item in experiments:
        if item.id in seen:
            raise boxes.HarnessError(f"the research {path} names experiment {item.id!r} twice")
        seen.append(item.id)
    allowed = {item.id for item in experiments} | DECISIONS
    for item in experiments:
        for key, target in (
            ("on_confirmed", item.on_confirmed),
            ("on_refuted", item.on_refuted),
            ("on_failed", item.on_failed),
        ):
            if target is None:
                continue
            if target not in allowed:
                raise boxes.HarnessError(
                    f"the research {path} sends experiment {item.id!r} {key} to {target!r}, "
                    f"which is not an experiment id and not one of {', '.join(sorted(DECISIONS))}"
                )
    return Research(
        path=path,
        id=identifier,
        question=question,
        closes_when=closes_when,
        budget=budget,
        experiments=experiments,
    )


def next_experiment(
    research: Research, latest: dict[str, object] | None, explicit: str | None = None
) -> Experiment:
    """The experiment to run: the named one, or the one the last verdict pointed at."""
    if explicit is not None:
        found = research.experiment(explicit)
        if found is None:
            known = ", ".join(item.id for item in research.experiments)
            raise boxes.HarnessError(
                f"the research names no experiment {explicit!r}; known: {known}"
            )
        return found
    if latest is None:
        return research.experiments[0]
    target = latest.get("next")
    if target in DECISIONS or target is None:
        raise boxes.HarnessError(
            f"the research {research.id!r} is closed by its last verdict; pass --experiment to "
            "run one anyway, or open a new research for a new question"
        )
    found = research.experiment(str(target))
    if found is None:
        raise boxes.HarnessError(
            f"the last verdict points at {target!r}, which the research does not declare"
        )
    return found


def latest(box: boxes.Box, research_id: str) -> dict[str, object] | None:
    """The newest result of a research, or ``None``; an unreadable result stops the reading."""
    directory = box.root / RECORDS / research_id
    if not directory.is_dir():
        return None
    results = sorted(directory.glob("*/result.json"))
    if not results:
        return None
    path = results[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"the study result {path} does not parse: {error}") from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"{path} is not a study result")
    return data


def run_experiment(
    box: boxes.Box, research: Research, experiment: Experiment, *, when: datetime | None = None
) -> Outcome:
    """Run the matrix, aggregate the numbers, and choose where the verdict leads."""
    stamp = _stamp((when or datetime.now(UTC)).replace(microsecond=0))
    directory = box.root / RECORDS / research.id / f"{stamp}-{experiment.id}"
    directory.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    budget = research.budget.get("max_seconds")
    cells: list[Cell] = []
    budget_hit = False
    for index, (run, values, repeat) in enumerate(_matrix(experiment)):
        cells.append(
            _run_cell(box.project, directory, experiment.metrics, run, values, repeat, index)
        )
        if budget is not None and time.monotonic() - began > budget:
            budget_hit = True
            break
    metrics = _aggregate(experiment, tuple(cells))
    verdict, reason = _verdict(experiment, tuple(cells), metrics)
    if budget_hit:
        verdict = "failed"
        reason = f"the research budget of {budget} s was spent before the matrix finished"
    target: str | None
    if verdict == "confirmed":
        target = experiment.on_confirmed
    elif verdict == "refuted":
        target = experiment.on_refuted
    else:
        target = experiment.on_failed
    seconds = round(time.monotonic() - began, 2)
    outcome = Outcome(
        research=research,
        experiment=experiment,
        cells=tuple(cells),
        metrics=metrics,
        verdict=verdict,
        reason=reason,
        code="not routed",
        next=target,
        seconds=seconds,
        at=stamp,
        directory=directory,
    )
    (directory / "result.json").write_text(
        json.dumps(_result_json(outcome), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return outcome


def render(outcome: Outcome) -> list[str]:
    """The experiment as a person reads it: cells, aggregates, the two verdicts, the decision."""
    lines = [
        f"research   {outcome.research.id} — {outcome.research.question}",
        f"experiment {outcome.experiment.id} — {outcome.experiment.hypothesis}",
    ]
    if outcome.experiment.prediction:
        lines.append(f"predicted  {outcome.experiment.prediction}")
    lines.append(f"cells      {len(outcome.cells)} run(s) in {outcome.seconds:.2f} s")
    for cell in outcome.cells:
        bits = ", ".join(f"{key}={value}" for key, value in cell.params.items()) or "no params"
        numbers = ", ".join(f"{key}={value:g}" for key, value in cell.numbers.items())
        state = "ok" if cell.error is None else cell.error
        lines.append(
            f"  cell {cell.index:<2} {bits} repeat {cell.repeat}  {state}  {numbers}".rstrip()
        )
    for metric in outcome.metrics:
        value = "no number" if metric.value is None else f"{metric.value:g}"
        mark = "confirmed" if metric.confirmed else "not met"
        line = (
            f"metric     {metric.spec.name}  {metric.spec.unit}  {metric.spec.direction}  "
            f"threshold {metric.spec.threshold:g}  {metric.spec.aggregate} {value}  {mark}"
        )
        expected = metric.expected_held
        if expected is not None:
            line += "  expected held" if expected else "  expected missed"
        lines.append(line)
    lines.append(f"code       {outcome.code} — the experiment declares no change")
    lines.append(f"verdict    hypothesis {outcome.verdict} — {outcome.reason}")
    lines.append(f"next       {outcome.next or 'stop: a person decides'}")
    lines.append(f"record     {outcome.directory}")
    return lines


def render_all(box: boxes.Box) -> list[str]:
    """Every research the project declares and where its last verdict left it."""
    found = files(box.project)
    if not found:
        return [
            f"no research in {box.project / DIR}: a study is a file at "
            f"{DIR}/<id>.toml"
        ]
    lines: list[str] = []
    for path in found:
        research = read(box.project, str(path))
        if lines:
            lines.append("")
        lines.append(f"research   {research.id} — {research.question}")
        lines.append(f"closes     {research.closes_when}")
        state = latest(box, research.id)
        if state is None:
            lines.append(f"state      not started — next {research.experiments[0].id}")
            continue
        target = state.get("next")
        where = "closed" if target in DECISIONS or target is None else f"next {target}"
        lines.append(
            f"state      {state.get('experiment')} {state.get('verdict')} — {where} "
            f"({state.get('at')})"
        )
    return lines


def payload(box: boxes.Box) -> list[dict[str, object]]:
    body: list[dict[str, object]] = []
    for path in files(box.project):
        research = read(box.project, str(path))
        body.append(
            {
                "id": research.id,
                "question": research.question,
                "closes_when": research.closes_when,
                "state": latest(box, research.id),
            }
        )
    return body


def outcome_json(outcome: Outcome) -> dict[str, object]:
    return _result_json(outcome)


def _matrix(experiment: Experiment) -> list[tuple[RunSpec, dict[str, str], int]]:
    cells: list[tuple[RunSpec, dict[str, str], int]] = []
    for run in experiment.runs:
        for values in _combinations(run.params):
            for repeat in range(1, run.repeats + 1):
                cells.append((run, values, repeat))
    return cells


def _combinations(params: dict[str, tuple[str, ...]]) -> list[dict[str, str]]:
    if not params:
        return [{}]
    keys = list(params)
    rows: list[dict[str, str]] = [{}]
    for key in keys:
        rows = [{**row, key: value} for row in rows for value in params[key]]
    return rows


def _run_cell(
    project: Path,
    directory: Path,
    metrics: tuple[MetricSpec, ...],
    run: RunSpec,
    values: dict[str, str],
    repeat: int,
    index: int,
) -> Cell:
    cell_dir = directory / "cells" / f"{index:03d}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = cell_dir / "stdout.txt"
    stderr_path = cell_dir / "stderr.txt"
    try:
        argv = [item.format(**values) for item in run.argv]
    except KeyError as missing:
        return Cell(
            index=index,
            params=values,
            repeat=repeat,
            exit_code=None,
            seconds=0.0,
            numbers={},
            error=f"the command placeholder {missing} has no parameter",
            stdout=stdout_path,
            stderr=stderr_path,
        )
    cwd = project / run.cwd if run.cwd else project
    env = {**os.environ, **run.env}
    began = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=run.timeout,
            check=False,
        )
        stdout, stderr, code, error = done.stdout, done.stderr, done.returncode, None
    except subprocess.TimeoutExpired as expired:
        stdout = _text(expired.stdout)
        stderr = _text(expired.stderr)
        code = None
        error = f"the run passed its {run.timeout} s limit"
    except OSError as failure:
        stdout = ""
        stderr = str(failure)
        code = None
        error = f"the command did not start: {failure}"
    seconds = round(time.monotonic() - began, 2)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    if error is not None:
        return Cell(index, values, repeat, code, seconds, {}, error, stdout_path, stderr_path)
    if code != 0:
        return Cell(
            index,
            values,
            repeat,
            code,
            seconds,
            {},
            f"the command exited {code}",
            stdout_path,
            stderr_path,
        )
    numbers: dict[str, float] = {}
    for spec in metrics:
        match = re.search(spec.pattern, stdout)
        if match is None:
            continue
        try:
            numbers[spec.name] = float(match.group(1))
        except ValueError:
            return Cell(
                index,
                values,
                repeat,
                code,
                seconds,
                {},
                f"the pattern for {spec.name!r} pulled {match.group(1)!r}, not a number",
                stdout_path,
                stderr_path,
            )
    return Cell(index, values, repeat, code, seconds, numbers, None, stdout_path, stderr_path)


def _aggregate(experiment: Experiment, cells: tuple[Cell, ...]) -> tuple[MetricResult, ...]:
    results: list[MetricResult] = []
    for spec in experiment.metrics:
        values = [
            cell.numbers[spec.name]
            for cell in cells
            if cell.error is None and spec.name in cell.numbers
        ]
        value = _aggregate_value(spec.aggregate, values)
        confirmed = value is not None and _meets(spec, value)
        results.append(MetricResult(spec=spec, value=value, confirmed=confirmed))
    return tuple(results)


def _aggregate_value(kind: str, values: list[float]) -> float | None:
    if not values:
        return None
    if kind == "median":
        return statistics.median(values)
    if kind == "mean":
        return statistics.fmean(values)
    if kind == "min":
        return min(values)
    return max(values)


def _meets(spec: MetricSpec, value: float) -> bool:
    if spec.direction == "up":
        return value >= spec.threshold
    return value <= spec.threshold


def _verdict(
    experiment: Experiment, cells: tuple[Cell, ...], metrics: tuple[MetricResult, ...]
) -> tuple[str, str]:
    failed = [cell for cell in cells if cell.error is not None]
    if failed:
        return "failed", f"{len(failed)} of {len(cells)} run(s) produced no number"
    missing = [metric.spec.name for metric in metrics if metric.value is None]
    if missing:
        return "failed", f"no number for {', '.join(missing)}"
    missed = [metric.spec.name for metric in metrics if not metric.confirmed]
    if missed:
        return "refuted", f"the numbers missed the threshold for {', '.join(missed)}"
    return "confirmed", "every metric met its threshold"


def _result_json(outcome: Outcome) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "research": outcome.research.id,
        "experiment": outcome.experiment.id,
        "at": outcome.at,
        "hypothesis": outcome.experiment.hypothesis,
        "prediction": outcome.experiment.prediction,
        "verdict": outcome.verdict,
        "reason": outcome.reason,
        "code": {"status": outcome.code, "notes": "the experiment declares no change"},
        "seconds": outcome.seconds,
        "next": outcome.next,
        "metrics": [
            {
                "name": item.spec.name,
                "unit": item.spec.unit,
                "direction": item.spec.direction,
                "threshold": item.spec.threshold,
                "expected": item.spec.expected,
                "aggregate": item.spec.aggregate,
                "value": item.value,
                "confirmed": item.confirmed,
                "expected_held": item.expected_held,
            }
            for item in outcome.metrics
        ],
        "cells": [
            {
                "index": cell.index,
                "params": cell.params,
                "repeat": cell.repeat,
                "exit_code": cell.exit_code,
                "seconds": cell.seconds,
                "numbers": cell.numbers,
                "error": cell.error,
                "stdout": _relative(cell.stdout, outcome.directory),
                "stderr": _relative(cell.stderr, outcome.directory),
            }
            for cell in outcome.cells
        ],
        "record": str(outcome.directory),
        "budget": outcome.research.budget,
    }


# --- reading the research file -------------------------------------------------------------


def _experiment(value: object, path: Path, index: int) -> Experiment:
    where = f"experiment {index}"
    if not isinstance(value, dict):
        raise boxes.HarnessError(f"the research {path} has {where} that is not a table")
    identifier = _required(_string(value.get("id")), f"{where}.id", path)
    hypothesis = _required(_string(value.get("hypothesis")), f"{where}.hypothesis", path)
    metrics = _metrics(value.get("metric"), path, identifier)
    runs = _runs(value.get("run"), path, identifier)
    decision = _required(_string(value.get("on_confirmed")), f"{where}.on_confirmed", path)
    refuted = _required(_string(value.get("on_refuted")), f"{where}.on_refuted", path)
    failed = _string(value.get("on_failed"))
    return Experiment(
        id=identifier,
        hypothesis=hypothesis,
        prediction=_string(value.get("prediction")),
        metrics=metrics,
        runs=runs,
        on_confirmed=decision,
        on_refuted=refuted,
        on_failed=failed,
    )


def _metrics(value: object, path: Path, experiment: str) -> tuple[MetricSpec, ...]:
    if not isinstance(value, list) or not value:
        raise boxes.HarnessError(
            f"the research {path} experiment {experiment!r} declares no [[experiment.metric]]"
        )
    specs: list[MetricSpec] = []
    names: list[str] = []
    for index, item in enumerate(value, start=1):
        where = f"experiment {experiment!r} metric {index}"
        if not isinstance(item, dict):
            raise boxes.HarnessError(f"the research {path} has {where} that is not a table")
        name = _required(_string(item.get("name")), f"{where}.name", path)
        if name in names:
            raise boxes.HarnessError(
                f"the research {path} experiment {experiment!r} names the metric {name!r} twice"
            )
        names.append(name)
        direction = _required(_string(item.get("direction")), f"{where}.direction", path)
        if direction not in DIRECTIONS:
            raise boxes.HarnessError(
                f"the research {path} says direction {direction!r} for {name!r}; expected one of "
                f"{', '.join(sorted(DIRECTIONS))}"
            )
        aggregate = _string(item.get("aggregate")) or "median"
        if aggregate not in AGGREGATES:
            raise boxes.HarnessError(
                f"the research {path} aggregates {name!r} as {aggregate!r}; expected one of "
                f"{', '.join(sorted(AGGREGATES))}"
            )
        pattern = _required(_string(item.get("pattern")), f"{where}.pattern", path)
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise boxes.HarnessError(
                f"the research {path} pattern for {name!r} does not compile: {error}"
            ) from error
        if compiled.groups != 1:
            raise boxes.HarnessError(
                f"the research {path} pattern for {name!r} needs exactly one group to pull the "
                f"number out, not {compiled.groups}"
            )
        specs.append(
            MetricSpec(
                name=name,
                unit=_string(item.get("unit")) or "",
                direction=direction,
                threshold=_required_number(
                    item.get("threshold"), f"{where}.threshold", path
                ),
                expected=_number(item.get("expected"), f"{where}.expected", path),
                pattern=pattern,
                aggregate=aggregate,
            )
        )
    return tuple(specs)


def _runs(value: object, path: Path, experiment: str) -> tuple[RunSpec, ...]:
    if not isinstance(value, list) or not value:
        raise boxes.HarnessError(
            f"the research {path} experiment {experiment!r} declares no [[experiment.run]]"
        )
    runs: list[RunSpec] = []
    for index, item in enumerate(value, start=1):
        where = f"experiment {experiment!r} run {index}"
        if not isinstance(item, dict):
            raise boxes.HarnessError(f"the research {path} has {where} that is not a table")
        argv = item.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise boxes.HarnessError(f"the research {path} {where}.argv must be a non-empty list")
        params: dict[str, tuple[str, ...]] = {}
        raw_params = item.get("params", {})
        if not isinstance(raw_params, dict):
            raise boxes.HarnessError(f"the research {path} {where}.params must be a table")
        for key, values in raw_params.items():
            if not isinstance(values, list) or not values or not all(
                isinstance(x, str) for x in values
            ):
                raise boxes.HarnessError(
                    f"the research {path} {where}.params.{key} must be a non-empty list of strings"
                )
            params[str(key)] = tuple(values)
        for key in params:
            if not any(f"{{{key}}}" in argument for argument in argv):
                raise boxes.HarnessError(
                    f"the research {path} {where} declares parameter {key!r} that no argv uses"
                )
        repeats = item.get("repeats", 1)
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats <= 0:
            raise boxes.HarnessError(f"the research {path} {where}.repeats must be positive")
        timeout = item.get("timeout", DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise boxes.HarnessError(f"the research {path} {where}.timeout must be positive")
        cwd = _string(item.get("cwd"))
        env: dict[str, str] = {}
        raw_env = item.get("env", {})
        if not isinstance(raw_env, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in raw_env.items()
        ):
            raise boxes.HarnessError(
                f"the research {path} {where}.env must be a table of strings"
            )
        env = {str(key): value for key, value in raw_env.items()}
        runs.append(
            RunSpec(
                argv=tuple(argv),
                params=params,
                repeats=repeats,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        )
    return tuple(runs)


def _budget(value: object, path: Path) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise boxes.HarnessError(f"the research {path} budget is not a table")
    budget: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise boxes.HarnessError(
                f"the research {path} budget.{key} must be a positive whole number"
            )
        budget[str(key)] = item
    return budget


def _required_number(value: object, where: str, path: Path) -> float:
    number = _number(value, where, path)
    if number is None:
        raise boxes.HarnessError(f"the research {path} has no {where}")
    return number


def _number(value: object, where: str, path: Path) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise boxes.HarnessError(f"the research {path} {where} must be a number")
    return float(value)


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required(value: str | None, where: str, path: Path) -> str:
    if value is None:
        raise boxes.HarnessError(f"the research {path} has no {where}")
    return value


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")
