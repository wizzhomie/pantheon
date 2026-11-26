"""The steps engine: one call per step, every step gated, the visible check between them.

A one-shot call has to get the change right blind. The steps engine pays for a second call
only when the first one can show what it did: the gate applied it or refused it, and the check
the task lets the model see said what happened to the behaviour. These tests hold the loop
closed — every step through the gate, the last check's words in the next request, the stops
that come from the box's own records — and the one thing the loop may not change: the verdict
is still the hidden check's.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from harness import box as boxes
from harness import contour, executor
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

FIXED = (
    "import pathlib, sys; v = pathlib.Path('src/app.py').read_text(); "
    "print('the file says', v.strip()); sys.exit(0 if 'value = 3' in v else 1)"
)
NEVER = "import sys; print('nope'); sys.exit(1)"

HEAD = """\
schema_version = 1
id = "t-1"
goal = "make the value three"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is three"
"""


def task(*, check: str = FIXED, max_steps: int = 6, repro: str | None = None) -> str:
    body = f'{HEAD}self_check = ["python", "-c", "{check}"]\nmax_steps = {max_steps}\n'
    if repro is not None:
        body += f'repro = ["python", "-c", "{repro}"]\n'
    body += (
        '\n[contract]\nsymptom = "the value is not three"\n'
        'visible = "src/app.py says value = 3"\nboundaries = "src/**"\nmissing = ""\n'
    )
    return body + '\n[budget]\nmax_cents = 6\nmax_seconds = 60\n'


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
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def write_task(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "t-1.toml"
    path.write_text(text, encoding="utf-8")
    return path


EXECUTOR = '''\
#!/usr/bin/env python3
import json, os, sys

request = json.load(sys.stdin)
with open(os.environ["FAKE_STEP_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(request) + "\\n")
mode = os.environ.get("FAKE_STEP_MODE", "next")
turns = request.get("turns") or []
applied = [item for item in turns if item.get("status") == "applied"]
current = 1 + len(applied)
fixed = current + 1
usage = {"prompt_tokens": 100, "completion_tokens": 20, "cost_cents": 0.1}


def patch(text):
    print(json.dumps({"patch": text, "model": "fake-model", "usage": usage}))


if mode == "fix-first":
    patch("--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-value = 1\\n+value = 3\\n")
elif mode == "done":
    print(json.dumps({"done": True, "model": "fake-model",
                      "usage": {"prompt_tokens": 10, "completion_tokens": 1,
                                "cost_cents": 0.1}}))
elif mode == "refuse-first":
    if turns:
        patch("--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-value = 1\\n+value = 3\\n")
    else:
        patch("--- a/docs/note.txt\\n+++ b/docs/note.txt\\n@@ -1 +1 @@\\n-a\\n+b\\n")
elif mode == "cut":
    print(json.dumps({
        "error": "the answer was cut before any text: the model spent the whole room on "
                 "thinking and returned nothing",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                  "reasoning_tokens": 20, "answer_tokens": 0, "cost_cents": 0.1},
        "finish_reason": "length",
        "billing_status": "billed",
        "model": "fake-model",
    }))
else:
    patch(f"--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n"
          f"-value = {current}\\n+value = {fixed}\\n")
'''


def write_executor(tmp_path: Path) -> Path:
    script = tmp_path / "fake-steps.py"
    script.write_text(EXECUTOR, encoding="utf-8")
    script.chmod(0o755)
    return script


def write_profile(
    box: boxes.Box, command: Path, engines: str = '[engines]\nbugfix = "steps"\n'
) -> None:
    (box.root / "persona" / "executor.toml").write_text(
        "schema_version = 1\n"
        f'command = "{command}"\n'
        "max_seconds = 60\n"
        'engine = "one-shot"\n'
        f"{engines}",
        encoding="utf-8",
    )


def dispatch(box: boxes.Box, path: Path) -> int:
    return main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(path)])


def resume(box: boxes.Box, *extra: str) -> int:
    return main(["--castra", str(box.root.parent), "resume", box.name, "--task", "t-1", *extra])


def calls(tmp_path: Path) -> int:
    log = tmp_path / "executor.log"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


def requests(tmp_path: Path) -> list[dict[str, object]]:
    log = tmp_path / "executor.log"
    return [cast(dict[str, object], json.loads(line)) for line in log.read_text().splitlines()]


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
    monkeypatch.setenv("FAKE_STEP_LOG", str(tmp_path / "executor.log"))
    monkeypatch.delenv("FAKE_STEP_MODE", raising=False)


@requires_trajan
def test_the_second_step_sees_what_the_first_one_did(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    assert dispatch(box, write_task(tmp_path, task(repro=FIXED))) == 0
    capsys.readouterr()
    assert calls(tmp_path) == 2
    first, second = requests(tmp_path)
    assert first["turn"] == 1 and first["turns"] == []
    turns = second["turns"]
    assert isinstance(turns, list) and len(turns) == 1
    only = turns[0]
    assert isinstance(only, dict) and only["status"] == "applied"
    check = only["check"]
    assert isinstance(check, dict) and check["status"] == "failed"
    assert "value = 2" in str(check["tail"])
    assert (project / "src" / "app.py").read_text() == "value = 3\n"
    payload = record(box)
    assert payload["engine"] == "steps"
    steps = payload["steps"]
    assert isinstance(steps, list) and [step["status"] for step in steps] == ["applied", "applied"]
    assert payload["state"] == "closed"
    commit = payload["commit"]
    assert isinstance(commit, dict) and commit["status"] == "made"


@requires_trajan
def test_two_steps_with_the_same_failed_check_stop_the_loop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    never = "import sys; print('nope'); sys.exit(1)"
    text = task(check=never, max_steps=4, repro=never)
    assert dispatch(box, write_task(tmp_path, text)) == 1
    out = capsys.readouterr().out
    assert "same exit code and the same words" in out
    assert calls(tmp_path) == 2, "the third call is not made: two steps failed the same way"
    payload = record(box)
    assert payload["stop_reason"]
    assert payload["engine"] == "steps"
    assert payload["state"] == "returned"


@requires_trajan
def test_a_refused_step_is_a_fact_the_next_step_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_STEP_MODE", "refuse-first")
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    assert dispatch(box, write_task(tmp_path, task(repro=FIXED))) == 0
    capsys.readouterr()
    assert calls(tmp_path) == 2
    second = requests(tmp_path)[1]
    turns = second["turns"]
    assert isinstance(turns, list) and len(turns) == 1
    only = turns[0]
    assert isinstance(only, dict) and only["status"] == "refused"
    assert "docs/note.txt" in str(only["reason"])
    payload = record(box)
    steps = payload["steps"]
    assert isinstance(steps, list)
    assert [step["status"] for step in steps] == ["refused", "applied"]


@requires_trajan
def test_a_done_answer_ends_the_loop_without_another_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_STEP_MODE", "done")
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    assert dispatch(box, write_task(tmp_path, task(repro=FIXED))) == 1
    capsys.readouterr()
    assert calls(tmp_path) == 1
    payload = record(box)
    assert payload["state"] == "returned"
    steps = payload["steps"]
    assert isinstance(steps, list) and steps[0]["status"] == "done"


def test_a_steps_task_without_a_call_ceiling_is_refused_before_any_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    text = task().replace("max_steps = 6\n", "")
    assert dispatch(box, write_task(tmp_path, text)) == 1
    out = capsys.readouterr().out
    assert "no call ceiling is declared" in out
    assert calls(tmp_path) == 0


@requires_trajan
def test_resume_continues_an_unfinished_steps_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    never = "import sys; print('nope'); sys.exit(1)"
    assert dispatch(box, write_task(tmp_path, task(check=never, max_steps=6, repro=never))) == 1
    capsys.readouterr()
    assert calls(tmp_path) == 2, "two steps applied and failed the visible check the same way"
    # A crash between the check and its record: the step file says the check failed, the check's
    # own file is gone, and the verdict never ran. Resume goes on with the loop instead of
    # verifying a tree the loop had not finished with.
    for stale in (box.root / "tabularium" / "t-1").glob("dispatch-*.json"):
        stale.unlink()
    (box.root / "tabularium" / "t-1" / "2" / "self-check.json").unlink()
    assert resume(box) == 1
    capsys.readouterr()
    assert calls(tmp_path) == 4, "the loop went on and asked again, with the steps before it"
    payload = record(box)
    assert payload["engine"] == "steps"
    steps = payload["steps"]
    assert isinstance(steps, list) and len(steps) == 4


@requires_trajan
def test_a_one_shot_kind_records_its_engine_and_no_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_STEP_MODE", "fix-first")
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path), engines="")
    text = task(repro=FIXED)
    assert dispatch(box, write_task(tmp_path, text)) == 0
    capsys.readouterr()
    payload = record(box)
    assert payload["engine"] == "one-shot"
    assert payload["steps"] == []
    assert calls(tmp_path) == 1


def test_the_step_lines_name_the_check_and_not_the_verdict() -> None:
    lines = executor.turn_lines(
        2,
        [
            {"turn": 1, "status": "applied", "check": {"status": "failed", "tail": "exit 1: nope"}},
            {"turn": 2, "status": "refused", "reason": "path outside the contract"},
        ],
        6,
    )
    text = "\n".join(lines)
    assert "STEP 2 OF AT MOST 6" in text
    assert "the check you can see still failed (exit 1: nope)" in text
    assert "the write gate refused the change (path outside the contract)" in text
    assert "DONE" in text
    first = "\n".join(executor.turn_lines(1, [], 5, steps=True))
    assert "STEP 1 OF AT MOST 5" in first and "ask you again" in first
    assert executor.turn_lines(1, [], 5) == [], "a one-shot call has no step heading"


@requires_trajan
def test_a_retry_starts_clean_and_does_not_replay_the_reverted_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    # The visible check passes after the first step; the hidden one never does. One call, a
    # hold, and calls left: the retry has to start from the reverted tree, not the old steps.
    two = (
        "import pathlib, sys; v = pathlib.Path('src/app.py').read_text(); "
        "sys.exit(0 if 'value = 2' in v else 1)"
    )
    never = "import sys; sys.exit(1)"
    assert dispatch(box, write_task(tmp_path, task(check=two, max_steps=4, repro=never))) == 1
    capsys.readouterr()
    assert calls(tmp_path) == 1
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    assert resume(box, "--retry") == 1
    capsys.readouterr()
    assert calls(tmp_path) == 2
    second = requests(tmp_path)[1]
    assert second["turns"] == [], "the reverted steps are not history the next run reads"
    previous = second["previous"]
    assert isinstance(previous, dict) and previous["verdict"] == "hold"
    marker = json.loads(
        (box.root / "tabularium" / "t-1" / "reverted.json").read_text(encoding="utf-8")
    )
    assert marker["attempts"] == [1]
    assert marker["after_turn"] == 1


def test_the_engine_table_is_validated(tmp_path: Path) -> None:
    profile = tmp_path / "executor.toml"
    profile.write_text(
        'schema_version = 1\ncommand = "/bin/true"\n[engines]\nbugfix = "sometimes"\n',
        encoding="utf-8",
    )
    with pytest.raises(boxes.HarnessError) as caught:
        executor.read_config(profile)
    assert "engines.bugfix" in str(caught.value)


def test_the_engine_is_chosen_by_kind_with_its_reason(tmp_path: Path) -> None:
    profile = tmp_path / "executor.toml"
    profile.write_text(
        'schema_version = 1\ncommand = "/bin/true"\nengine = "one-shot"\n'
        '[engines]\nbugfix = "steps"\n',
        encoding="utf-8",
    )
    config = executor.read_config(profile)
    assert executor.engine_for(config, "bugfix") == ("steps", "steps, declared for kind bugfix")
    assert executor.engine_for(config, "docs")[0] == "one-shot"
    assert "no entry for kind docs" in executor.engine_for(config, "docs")[1]
    assert "engine one-shot" in executor.describe(config)


class _FakeTask:
    """The slice of a task ``step_ceiling`` reads: the ceiling, and nothing else."""

    id = "t-1"
    goal = None
    kind = "bugfix"
    paths: tuple[str, ...] = ()
    expected = None
    self_check: tuple[str, ...] = ()
    shape = None
    forbidden: tuple[str, ...] = ()
    role = "executor"

    def __init__(self, max_steps: int | None = None) -> None:
        self.max_steps = max_steps
        self.budget: dict[str, int] = {}


def test_the_call_ceiling_is_the_lower_of_the_task_and_the_box(tmp_path: Path) -> None:
    config = executor.ExecutorConfig(path=tmp_path / "e.toml", command=("/bin/true",), max_steps=4)
    assert executor.step_ceiling(_FakeTask(max_steps=2), config) == 2
    assert executor.step_ceiling(_FakeTask(max_steps=9), config) == 4
    assert executor.step_ceiling(_FakeTask(), config) == 4


@requires_trajan
def test_a_cut_answer_is_recorded_with_its_reasoning_and_the_budget_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cut at the room is a fact with a cause: record the thinking and name the budget.

    The box does not retry a cut inside the same budget: the room was the money's maximum, so a
    retry would buy less, not more. The record says what the room was and how much of it was
    thinking, and the next step is the owner's budget decision.
    """
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    monkeypatch.setenv("FAKE_STEP_MODE", "cut")
    assert dispatch(box, write_task(tmp_path, task(repro=FIXED))) == 1
    capsys.readouterr()
    assert calls(tmp_path) == 1
    record_path = sorted((box.root / "tabularium" / "t-1").glob("proposal-*/proposal.json"))[0]
    proposal = json.loads(record_path.read_text(encoding="utf-8"))
    assert proposal["status"] == "truncated"
    assert proposal["finish_reason"] == "length"
    assert proposal["billing_status"] == "billed"
    assert proposal["truncated"] is True
    assert proposal["usage"]["reasoning_tokens"] == 20
    assert proposal["max_tokens"] == 32768, "no price: the provider ceiling is the room"
    notes = "\n".join(cast(list[str], record(box)["notes"]))
    assert "budget decision" in notes
    step = json.loads((record_path.parent / "step.json").read_text(encoding="utf-8"))
    assert step["status"] == "truncated"


@requires_trajan
def test_the_room_in_the_request_is_computed_from_the_task_money(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two cents buy 13 636 tokens at $1.10 per million: the request carries that number."""
    monkeypatch.setenv("PANTHEON_MODEL_PRICE_IN", "0.27")
    monkeypatch.setenv("PANTHEON_MODEL_PRICE_OUT", "1.10")
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    write_profile(box, write_executor(tmp_path))
    monkeypatch.setenv("FAKE_STEP_MODE", "fix-first")
    text = task(repro=FIXED).replace("max_cents = 6", "max_cents = 2")
    assert dispatch(box, write_task(tmp_path, text)) == 0
    limits = cast(dict[str, object], requests(tmp_path)[0]["limits"])
    assert limits["max_answer_tokens"] == 13_636
    proposal = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "proposal.json").read_text(
            encoding="utf-8"
        )
    )
    assert proposal["max_tokens"] == 13_636
