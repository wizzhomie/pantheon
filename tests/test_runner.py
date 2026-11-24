"""The runner: an auto campaign advances by its own rule, and stops with the reason.

The runner adds no judgement of its own. It asks the plan which step may run, takes it through
the same gates a person gets with ``pantheon step``, retries only while the retry rule allows,
and spends no more attempts than the plan's own ceiling, summed over its steps. These tests hold
the four outcomes that matter: a campaign that completes, a retry that turns a failure into a
close, a failure that stops for good, and a refusal before anything is paid.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import box as boxes
from harness import campaign, frame
from harness.cli import main

MODEL = """\
schema_version = 1

[project]
root_packages = ["probe"]

[[layer]]
name = "app"
modules = ["probe"]
"""

POLICY = f"""\
name = "probe"

[[checks]]
id = "parses"
argv = [
    {json.dumps(sys.executable)},
    "-c",
    "compile(open('src/app.py').read(), 'src/app.py', 'exec')",
]
purpose = "the file still parses"
"""

CAMPAIGN = """\
schema_version = 1
id = "c-auto"
goal = "two notes in one file"
mode = "auto"
attempts = {attempts}
frame = "{frame}"

[[step]]
id = "s1"
goal = "add a note to src/app.py"
kind = "bugfix"
paths = ["src/**"]
expected = "the file still parses"

{second}

[[acceptance]]
id = "a1"
statement = "both notes are in the file"
step = "s1"

[budget]
max_cents = {budget}
"""

SECOND_STEP = """\
[[step]]
id = "s2"
goal = "add a second note"
kind = "bugfix"
paths = ["src/**"]
expected = "the file still parses"
depends_on = ["s1"]
"""

FAKE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

request = json.load(sys.stdin)
counter = Path(request["box"]["root"]) / "counter"
calls = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(calls + 1))
call = calls + 1
if {fails}:
    print("the model answered without a unified diff", file=sys.stderr)
    sys.exit(1)
attempt = request["attempt"]
path = Path(request["project"]["path"]) / "src" / "app.py"
lines = path.read_text(encoding="utf-8").splitlines()
if {breaks}:
    added = "value = " + str(attempt) + " +"
else:
    added = "# " + request["task"]["id"] + " attempt " + str(attempt)
patch = "--- a/src/app.py\\n+++ b/src/app.py\\n"
patch += "@@ -1,%d +1,%d @@\\n" % (len(lines), len(lines) + 1)
patch += "".join(" " + line + "\\n" for line in lines)
patch += "+" + added + "\\n"
print(json.dumps({{
    "patch": patch,
    "model": "fake-model",
    "usage": {{"prompt_tokens": 5, "completion_tokens": 5, "cost_cents": {cost}}},
}}))
'''


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".pantheon").mkdir()
    (project / ".pantheon" / "architecture.toml").write_text(MODEL, encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(POLICY, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def fake_executor(
    tmp_path: Path, *, breaks: str = "False", fails: str = "False", cost: float = 0.1
) -> Path:
    script = tmp_path / "fake-executor.py"
    script.write_text(FAKE.format(breaks=breaks, fails=fails, cost=cost), encoding="utf-8")
    script.chmod(0o755)
    return script


def write_campaign(
    project: Path, *, attempts: int = 1, budget: int = 10, second: bool = True
) -> Path:
    text = CAMPAIGN.format(
        attempts=attempts,
        budget=budget,
        frame=frame.read_frame(project).digest,
        second=SECOND_STEP if second else "",
    )
    directory = project / ".pantheon" / "campaigns"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "c-auto.toml"
    path.write_text(text, encoding="utf-8")
    return path


def approve(box: boxes.Box) -> None:
    assert run(box, "approve", "probe") == 0


def commit_plan(project: Path) -> None:
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "the plan")


def run(box: boxes.Box, *args: str) -> int:
    return main(["--boxes", str(box.root.parent), *args])


def advance(box: boxes.Box, *extra: str) -> int:
    return run(box, "advance", "probe", "--campaign", "c-auto", *extra)


def state(box: boxes.Box) -> dict[str, object]:
    path = box.root / "artifacts" / "campaign" / "c-auto" / "state.json"
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def run_record(box: boxes.Box) -> dict[str, object]:
    paths = sorted((box.root / "artifacts" / "campaign" / "c-auto").glob("run-*.json"))
    assert paths, "a run writes its own record"
    data: dict[str, object] = json.loads(paths[-1].read_text(encoding="utf-8"))
    return data


def ledger(box: boxes.Box, kind: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for path in sorted((box.root / "ledger").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if isinstance(event, dict) and event.get("type") == kind:
                found.append(event)
    return found


def trajan_runnable() -> bool:
    from harness import contour

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


def prepared(
    tmp_path: Path,
    *,
    attempts: int = 1,
    budget: int = 10,
    second: bool = True,
    cost: float = 0.1,
    breaks: str = "False",
    fails: str = "False",
) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    command = fake_executor(tmp_path, breaks=breaks, fails=fails, cost=cost)
    (box.root / "profile" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 60\n',
        encoding="utf-8",
    )
    approve(box)
    write_campaign(project, attempts=attempts, budget=budget, second=second)
    commit_plan(project)
    return project, box


def test_the_ceiling_is_the_plan_own_sum_and_steps_run_in_their_order(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    write_campaign(project, attempts=2)
    plan = campaign.read_campaign(project / ".pantheon" / "campaigns" / "c-auto.toml")
    assert campaign.attempts_allowed(plan) == 4
    assert [item.id for item in plan.steps] == ["s1", "s2"]
    first = campaign.next_step(plan, None)
    assert first is not None and first.id == "s1"


@requires_trajan
def test_a_run_closes_every_step_and_writes_the_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = prepared(tmp_path)
    capsys.readouterr()
    assert advance(box) == 0
    out = capsys.readouterr().out
    assert "2 of 2 steps closed in the campaign, 2 by this run" in out
    assert "stop       none: every step closed with a strict pass" in out
    assert "verdict    complete — 2 of 2 steps closed" in out
    written = state(box)
    steps = written["steps"]
    assert isinstance(steps, dict)
    assert all(item["state"] == "closed" and item["verdict"] == "pass" for item in steps.values())
    notes = (project / "src" / "app.py").read_text(encoding="utf-8")
    assert "# c-auto-s1 attempt 1" in notes and "# c-auto-s2 attempt 1" in notes
    assert git(project, "status", "--porcelain") == "", "both steps are committed"
    account = run_record(box)
    assert account["stop"] == "" and account["verdict"] == "complete"
    assert len(ledger(box, "campaign.completed")) == 1


@requires_trajan
def test_a_retry_inside_the_paths_closes_the_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(tmp_path, attempts=2, second=False, breaks="call == 1")
    capsys.readouterr()
    assert advance(box) == 0
    out = capsys.readouterr().out
    assert "attempt    s1: not closed (hold)" in out
    assert "attempt    s1: closed (pass)" in out
    assert "verdict    complete — 1 of 1 steps closed" in out
    steps = state(box)["steps"]
    assert isinstance(steps, dict)
    assert steps["s1"]["attempts"] == 2 and steps["s1"]["verdict"] == "pass"
    reverted = ledger(box, "step.draft-reverted")
    assert len(reverted) == 1, "the failed draft is undone before the retry"


@requires_trajan
def test_the_attempt_ceiling_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(tmp_path, attempts=2, second=False, breaks="True")
    capsys.readouterr()
    assert advance(box) == 2
    out = capsys.readouterr().out
    assert "stop       attempts 2 of 2 used" in out
    assert "last       " in out, "a stop names the box's own words for the last attempt"
    assert "verdict    incomplete — 0 of 1 steps closed" in out
    account = run_record(box)
    assert "attempts 2 of 2 used" in str(account["stop"])
    assert len(ledger(box, "campaign.stopped")) == 1


@requires_trajan
def test_the_budget_stops_the_run_before_the_attempt_ceiling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(
        tmp_path, attempts=5, second=False, budget=1, cost=1.0, breaks="True"
    )
    capsys.readouterr()
    assert advance(box) == 2
    out = capsys.readouterr().out
    assert "spent 1.00 cents of the 1 declared" in out


@requires_trajan
def test_a_step_that_wrote_nothing_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = prepared(tmp_path, second=False)
    (box.root / "profile" / "executor.toml").unlink()
    capsys.readouterr()
    assert advance(box) == 2
    out = capsys.readouterr().out
    assert "step s1 wrote nothing" in out
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"


@requires_trajan
def test_a_closed_plan_without_an_approved_frame_stops_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    (box.root / "profile" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{fake_executor(tmp_path)}"\nmax_seconds = 60\n',
        encoding="utf-8",
    )
    write_campaign(project, second=False)
    commit_plan(project)
    capsys.readouterr()
    assert advance(box) == 2
    out = capsys.readouterr().out
    assert "every step is closed, but the campaign is not complete" in out
    assert "the frame was never approved" in out


def test_a_manual_campaign_is_not_run_and_nothing_is_paid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    path = write_campaign(project, second=False)
    path.write_text(path.read_text(encoding="utf-8").replace('mode = "auto"', 'mode = "manual"'))
    capsys.readouterr()
    assert advance(box) == 2
    out = capsys.readouterr().out
    assert "campaign mode is manual" in out
    assert not (box.root / "artifacts" / "campaign" / "c-auto" / "state.json").exists()
    assert not (box.root / "artifacts" / "campaign" / "c-auto").exists() or not list(
        (box.root / "artifacts" / "campaign" / "c-auto").glob("run-*.json")
    )


def test_a_plan_that_cannot_start_runs_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = prepared(tmp_path, second=False)
    path = project / ".pantheon" / "campaigns" / "c-auto.toml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('kind = "bugfix"', 'kind = "mystery"'),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert advance(box) == 2
    out = capsys.readouterr().out
    assert "the plan cannot start" in out and "no route for kind 'mystery'" in out
    assert not (box.root / "artifacts" / "campaign" / "c-auto" / "state.json").exists()

@requires_trajan
def test_a_technical_executor_failure_is_retried_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = prepared(tmp_path, attempts=2, second=False, fails="call == 1")
    capsys.readouterr()
    assert advance(box) == 0
    out = capsys.readouterr().out
    assert "attempt    s1: not closed (returned)" in out
    assert "attempt    s1: closed (pass)" in out
    steps = state(box)["steps"]
    assert isinstance(steps, dict)
    assert steps["s1"]["attempts"] == 2 and steps["s1"]["verdict"] == "pass"
