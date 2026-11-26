"""Two attempts: the box asks again by itself when the class says a repair can work.

D47 fixes what may be retried and how often: a class from the checks decides, the second attempt
is paid for, and the same class twice is where the box stops. These tests hold the decision on a
stand (no model, no trajan) and one end-to-end run where the executor answers badly and then
well.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import augustus, aurelius, contour, executor
from harness import box as boxes
from harness.cli import main


def make_task() -> augustus.Task:
    return augustus.Task(
        path=Path("t-1.toml"),
        id="t-1",
        goal="bump the value",
        kind="bugfix",
        paths=("src/**",),
        expected="the value is two",
        change=None,
        forbidden=(),
        budget={"max_cents": 2},
    )


def made(
    state: str,
    *,
    verify: str | None = "fail",
    own: str | None = None,
    arch: str | None = None,
    engine: str = "one-shot",
) -> augustus.Dispatch:
    outcome = (
        None
        if verify is None
        else contour.VerifyResult(
            run=Path("run"),
            report=Path("report"),
            run_id="r-1",
            status=verify,
            reasons=(),
            findings=0,
            blocking=0,
            checks=1,
            exit_code=0 if verify == "pass" else 1,
        )
    )
    return augustus.Dispatch(
        task=make_task(),
        state=state,
        stages=("run", "verify"),
        verify=outcome,
        self_check=None if own is None else {"status": own},
        engine=engine,
        arch=None
        if arch is None
        else aurelius.Result(
            status=arch,
            kept=0,
            broken=1,
            tool="lint-imports",
            version="1",
            directory=Path("."),
            exit_code=1,
            digest="d",
        ),
    )


class Stand:
    """A dispatch and a resume that answer from a list, so the loop is what is tested."""

    def __init__(self, first: augustus.Dispatch, then: list[augustus.Dispatch]) -> None:
        self.first = first
        self.then = list(then)
        self.dispatch_calls = 0
        self.resume_calls: list[dict[str, object]] = []

    def dispatch(self, *args: object, **kwargs: object) -> augustus.Dispatch:
        self.dispatch_calls += 1
        return self.first

    def resume(self, *args: object, **kwargs: object) -> augustus.Dispatch:
        self.resume_calls.append(kwargs)
        if not self.then:
            raise AssertionError("the loop asked for a third attempt")
        return self.then.pop(0)


def _executor_config(declared: bool, path: Path) -> executor.ExecutorConfig:
    return executor.ExecutorConfig(
        path=path, command=("/bin/true",) if declared else None
    )


def run_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stand: Stand,
    *,
    attempts: int = 2,
    declared: bool = True,
) -> augustus.Dispatch:
    box = boxes.Box(root=tmp_path / "box", name="probe", project=tmp_path / "probe", created="")
    config = _executor_config(declared, tmp_path / "executor.toml")
    monkeypatch.setattr(augustus, "dispatch", stand.dispatch)
    monkeypatch.setattr(augustus, "resume", stand.resume)
    return augustus.dispatch_with_attempts(
        box,
        make_task(),
        routes={},
        trajan_root=tmp_path,
        trajan_python=tmp_path / "python",
        executor_config=config,
        attempts=attempts,
    )


def events(box_root: Path) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for path in sorted((box_root / "fasti").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            found.append(json.loads(line))
    return found


def test_a_retryable_failure_is_asked_again_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(made("returned"), [made("closed", verify="pass")])
    result = run_loop(tmp_path, monkeypatch, stand)
    assert result.state == "closed"
    assert stand.dispatch_calls == 1 and len(stand.resume_calls) == 1
    assert stand.resume_calls[0]["retry"] is True
    assert "attempt 2 of 2" in str(stand.resume_calls[0]["notes_prefix"])


def test_a_broken_architecture_is_not_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(made("returned", verify="pass", arch="broken"), [])
    result = run_loop(tmp_path, monkeypatch, stand)
    assert result.state == "returned" and stand.resume_calls == []
    assert augustus.failure_class(result) == augustus.FAILURE_ARCHITECTURE
    assert events(tmp_path / "box") == [], "nothing to stop: the class already says no"


def test_a_closed_task_is_not_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand = Stand(made("closed", verify="pass"), [])
    run_loop(tmp_path, monkeypatch, stand)
    assert stand.resume_calls == []


def test_a_steps_run_is_not_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The steps engine has its own loop and its own fuses: a second attempt would redo it."""
    stand = Stand(made("returned", engine="steps"), [])
    result = run_loop(tmp_path, monkeypatch, stand)
    assert result.state == "returned" and stand.resume_calls == []
    stopped = [item for item in events(tmp_path / "box") if item["type"] == "retry.stopped"]
    assert stopped and "own loop" in str(stopped[-1]["reason"])


def test_a_box_without_an_executor_is_not_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand(made("returned"), [])
    result = run_loop(tmp_path, monkeypatch, stand, declared=False)
    assert result.state == "returned" and stand.resume_calls == []


def test_one_attempt_means_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand = Stand(made("returned"), [])
    run_loop(tmp_path, monkeypatch, stand, attempts=1)
    assert stand.resume_calls == []
    stopped = [item for item in events(tmp_path / "box") if item["type"] == "retry.stopped"]
    assert stopped and "attempts are spent" in str(stopped[-1]["reason"])


def test_the_same_class_twice_stops_before_the_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is not the ceiling that stops the loop, it is the answer not moving (D47)."""
    stand = Stand(made("returned"), [made("returned"), made("returned")])
    run_loop(tmp_path, monkeypatch, stand, attempts=4)
    assert len(stand.resume_calls) == 1, "the second attempt failed the same way: stop there"
    stopped = [item for item in events(tmp_path / "box") if item["type"] == "retry.stopped"]
    assert stopped and "same class twice (incomplete)" in str(stopped[-1]["reason"])


PASSING = """\
name = "probe"

[[checks]]
id = "value"
argv = ["grep", "-qx", "value = 2", "src/app.py"]
purpose = "the value is two"
"""

TASK = """\
schema_version = 1
id = "t-1"
goal = "bump the value in src/app.py"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[budget]
max_cents = 5
max_seconds = 60
"""

BAD = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 3
"""

GOOD = """\
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
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def write_executor(tmp_path: Path) -> Path:
    """The first answer misses the criterion, the second one meets it."""
    script = tmp_path / "two-answers.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        'log = os.environ["FAKE_EXECUTOR_LOG"]\n'
        "first = not os.path.exists(log)\n"
        'with open(log, "a", encoding="utf-8") as handle:\n'
        '    handle.write("called\\n")\n'
        f"patch = {json.dumps(BAD)} if first else {json.dumps(GOOD)}\n"
        'print(json.dumps({"patch": patch, "model": "fake-model", '
        '"usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_cents": 0.1}}))\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


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


@requires_trajan
def test_one_dispatch_asks_twice_and_the_second_answer_closes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict is the second attempt's, and both attempts keep their own record."""
    monkeypatch.setenv("FAKE_EXECUTOR_LOG", str(tmp_path / "executor.log"))
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{write_executor(tmp_path)}"\nmax_seconds = 60\n',
        encoding="utf-8",
    )
    task = tmp_path / "t-1.toml"
    task.write_text(TASK, encoding="utf-8")
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out
    assert "attempt 2 of 2" in out, "the record says this was the second attempt"
    log = (tmp_path / "executor.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2, "the box asked once more after the first answer missed"
    records = sorted((box.root / "tabularium" / "t-1").glob("dispatch-*.json"))
    assert len(records) == 2, "each attempt has its own record"
    first = json.loads(records[0].read_text(encoding="utf-8"))
    assert first["state"] == "returned" and first["failure_class"] == "incomplete"
    second = json.loads(records[1].read_text(encoding="utf-8"))
    assert second["state"] == "closed" and second["failure_class"] is None
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    acta = sorted((box.root / "acta").glob("*-t-1*.md"))
    assert len(acta) == 2, "the first attempt's summary is not overwritten by the second"
