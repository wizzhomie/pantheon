"""Budget and stops: a task's cents are a total, its calls are counted, and a stuck retry stops.

The pilot's leak was simple: every attempt was given the task's budget again, so eight attempts
on one defect cost four times what the task declared. These tests hold the other contract — the
box's records decide how much is left, the next call gets only that, and a task that cannot move
is stopped before it pays again.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from harness import augustus, contour, executor
from harness import box as boxes
from harness.cli import main

FAILING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('no'); raise SystemExit(1)"]
purpose = "a check that always fails"
"""

TASK = """\
schema_version = 1
id = "t-1"
goal = "change the app value"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[budget]
max_cents = 1
max_seconds = 60
"""

TASK_WITH_CHECK = """\
schema_version = 1
id = "t-1"
goal = "change the app value"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"
self_check = ["python", "-c", "print('same words'); raise SystemExit(1)"]

[budget]
max_cents = 5
max_seconds = 60
"""

PATCH = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(FAILING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path, project: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project)


def write_task(tmp_path: Path, text: str = TASK, name: str = "t-1.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def write_executor(tmp_path: Path, cost: str = "0.6") -> Path:
    script = tmp_path / "fake-executor.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        'with open(os.environ["FAKE_EXECUTOR_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write("called\\n")\n'
        "print(json.dumps({\n"
        f'    "patch": {json.dumps(PATCH)},\n'
        '    "model": "fake-model",\n'
        f'    "usage": {{"prompt_tokens": 100, "completion_tokens": 20, "cost_cents": {cost}}},\n'
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_profile(box: boxes.Box, command: Path, extra: str = "") -> None:
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 60\n{extra}',
        encoding="utf-8",
    )


def dispatch(box: boxes.Box, task: Path) -> int:
    return main(
        ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]
    )


def resume(box: boxes.Box, *extra: str) -> int:
    return main(["--castra", str(box.root.parent), "resume", box.name, "--task", "t-1", *extra])


def calls(tmp_path: Path) -> int:
    log = tmp_path / "executor.log"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


def record(box: boxes.Box) -> dict[str, object]:
    path = sorted((box.root / "tabularium" / "t-1").glob("dispatch-*.json"))[-1]
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def trajan_runnable() -> bool:
    try:
        root = contour.trajan_root()
    except boxes.HarnessError:
        return False
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    done = subprocess.run(
        [str(contour.trajan_python(root)), "-m", "trajan", "--help"],
        capture_output=True,
        check=False,
        env=env,
    )
    return done.returncode == 0


requires_trajan = pytest.mark.skipif(not trajan_runnable(), reason="trajan cannot run here")


@pytest.fixture(autouse=True)
def executor_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_EXECUTOR_LOG", str(tmp_path / "executor.log"))


@requires_trajan
def test_a_task_budget_is_a_total_across_calls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path, cost="0.6"), extra="max_cents = 3\n")
    task = write_task(tmp_path)
    assert dispatch(box, task) == 1, "the change applied, the check failed: returned"
    capsys.readouterr()
    # The box itself asked once more: 0.6 of the 1 cent was left as 0.4 of room, the answer
    # cost 0.6, so the second answer is over budget and only recorded, never applied.
    assert calls(tmp_path) == 2
    payload = record(box)
    engine = payload["executor"]
    assert isinstance(engine, dict) and engine["status"] == "over-budget"
    # 1.2 cents are already on the record: another retry is not made at all
    assert resume(box, "--retry") == 2
    out = capsys.readouterr().out
    assert "stopped" in out and "declared 1 cents" in out and "1.20 cents" in out
    assert calls(tmp_path) == 2, "a task that spent its total must not be asked again"


@requires_trajan
def test_the_next_call_is_given_only_what_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path, cost="0.6"), extra="max_cents = 3\n")
    task = write_task(tmp_path)
    assert dispatch(box, task) == 1
    capsys.readouterr()
    request = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-2" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert request["limits"]["max_cents"] == pytest.approx(0.4)
    assert request["task"]["budget"]["max_cents"] == 1, "the task is not rewritten by the guard"


@requires_trajan
def test_a_task_may_not_make_more_calls_than_it_allows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path, cost="0.1"), extra="max_cents = 3\n")
    task = write_task(
        tmp_path, TASK.replace('expected = "the value is two"', 'expected = "x"\nmax_steps = 1')
    )
    # The task allows one call; the box asked once, then wanted a second attempt and was
    # refused before asking: the stop is the last word of the run.
    assert dispatch(box, task) == 2
    out = capsys.readouterr().out
    assert "allows 1 call(s)" in out and "already made 1" in out
    assert calls(tmp_path) == 1


@requires_trajan
def test_two_attempts_that_fail_the_same_visible_check_stop_the_third(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path, cost="0.1"), extra="max_cents = 3\n")
    task = write_task(tmp_path, TASK_WITH_CHECK)
    assert dispatch(box, task) == 1
    capsys.readouterr()
    own = record(box)["self_check"]
    assert isinstance(own, dict) and own["status"] == "failed"
    assert calls(tmp_path) == 2, "the box asked twice on its own (D47)"
    assert resume(box, "--retry") == 2
    out = capsys.readouterr().out
    assert "same exit code and the same words" in out
    assert calls(tmp_path) == 2


def test_spent_counts_unknown_prices_at_the_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "box"
    directory = root / "tabularium" / "t-1"
    (directory / "proposal-1").mkdir(parents=True)
    (directory / "proposal-1" / "proposal.json").write_text(
        json.dumps({"usage": {"cost_cents": 0.5}}), encoding="utf-8"
    )
    (directory / "proposal-2").mkdir()
    (directory / "proposal-2" / "proposal.json").write_text(
        json.dumps({"usage": {"cost_cents": None}}), encoding="utf-8"
    )
    (directory / "dispatch-1.json").write_text(
        json.dumps({"verify": {"review": {"cost_cents": 0.2}}}), encoding="utf-8"
    )
    (directory / "dispatch-2.json").write_text(
        json.dumps({"verify": {"review": {"cost_cents": None}}, "review_cap": 0.3}),
        encoding="utf-8",
    )
    spend = augustus.spent(root, "t-1", ceiling=2.0)
    assert spend.calls == 2
    assert spend.unpriced == 1
    assert spend.cents == pytest.approx(0.5 + 2.0 + 0.2 + 0.3)


def test_a_call_that_never_left_the_box_is_not_spend(tmp_path: Path) -> None:
    """No key is not a paid call: charging it at the ceiling let one mistake eat the budget."""
    root = tmp_path / "box"
    directory = root / "tabularium" / "t-1"
    (directory / "proposal-1").mkdir(parents=True)
    (directory / "proposal-1" / "proposal.json").write_text(
        json.dumps({"billing_status": "not_billed", "usage": None}), encoding="utf-8"
    )
    (directory / "proposal-2").mkdir()
    (directory / "proposal-2" / "proposal.json").write_text(
        json.dumps({"billing_status": "billed", "usage": {"cost_cents": 0.5}}),
        encoding="utf-8",
    )
    (directory / "proposal-3").mkdir()
    (directory / "proposal-3" / "proposal.json").write_text(
        json.dumps({"billing_status": "unknown", "usage": {"cost_cents": None}}),
        encoding="utf-8",
    )
    spend = augustus.spent(root, "t-1", ceiling=2.0)
    assert spend.calls == 2
    assert spend.unpriced == 1
    assert spend.cents == pytest.approx(0.5 + 2.0)


def with_steps(text: str, value: str) -> str:
    """The task with a step ceiling: the key belongs before the ``[budget]`` table."""
    return text.replace('expected = "the value is two"', f'expected = "x"\nmax_steps = {value}')


def test_max_steps_is_read_and_frozen(tmp_path: Path) -> None:
    task = write_task(tmp_path, with_steps(TASK, "3"))
    read = augustus.read_task(task)
    assert read.max_steps == 3
    frozen = tmp_path / "frozen.toml"
    frozen.write_text(augustus.task_text(read), encoding="utf-8")
    assert augustus.read_task(frozen).max_steps == 3


@pytest.mark.parametrize("bad", ["0", "-1", '"3"', "true"])
def test_max_steps_must_be_a_positive_whole_number(tmp_path: Path, bad: str) -> None:
    task = write_task(
        tmp_path,
        TASK.replace('expected = "the value is two"', f'expected = "x"\nmax_steps = {bad}'),
        name="bad.toml",
    )
    with pytest.raises(boxes.HarnessError, match="max_steps"):
        augustus.read_task(task)


def test_the_box_may_cap_the_calls_a_task_makes(tmp_path: Path) -> None:
    profile = tmp_path / "executor.toml"
    profile.write_text('schema_version = 1\ncommand = "/bin/true"\nmax_steps = 4\n')
    assert executor.read_config(profile).max_steps == 4
    profile.write_text('schema_version = 1\ncommand = "/bin/true"\nmax_steps = 0\n')
    with pytest.raises(boxes.HarnessError, match="max_steps"):
        executor.read_config(profile)


def test_the_box_money_ceiling_bounds_the_task_total(tmp_path: Path) -> None:
    """A task may ask for more than the box; the box's cents are still the task's ceiling."""
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    write_profile(box, write_executor(tmp_path), extra="max_cents = 2\n")
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    asking = augustus.read_task(
        write_task(tmp_path, TASK.replace("max_cents = 1", "max_cents = 8"))
    )
    guard = augustus._guard(box, asking, config, box.root / "tabularium" / asking.id)
    assert guard.stop is None and guard.cents == 2.0
    silent = augustus.read_task(
        write_task(tmp_path, TASK.replace("max_cents = 1\n", ""))
    )
    guarded = augustus._guard(box, silent, config, box.root / "tabularium" / silent.id)
    assert guarded.stop is None and guarded.cents == 2.0
