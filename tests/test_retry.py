"""The retry rule and the campaign's fuses: one more attempt only when the verdict says where.

D19 puts the mode and the attempt ceiling in the campaign file; D20 makes a retry follow a
named reason inside the declared paths, never a mood. These tests hold the parse-time rules,
the decision matrix over real report shapes, and
two end-to-end runs: a retry that changes the answer and closes, and a retry that answers the
same change and is stopped before it is applied a second time.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import campaign, executor
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "value"
argv = ["grep", "-qx", "value = 2", "src/app.py"]
purpose = "the value is two"
"""

MODEL = """\
schema_version = 1

[project]
root_packages = ["probe"]

[[layer]]
name = "app"
modules = ["probe"]
"""

CAMPAIGN = """\
schema_version = 1
id = "c-1"
goal = "bump the value"
mode = "manual"
attempts = 2

[[step]]
id = "s1"
goal = "bump the value in src/app.py"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[[acceptance]]
id = "a1"
statement = "the value is two"
step = "s1"

[budget]
max_cents = 10
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
    (project / ".pantheon").mkdir()
    (project / ".pantheon" / "architecture.toml").write_text(MODEL, encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", make_project(tmp_path))


def write_campaign(box: boxes.Box, text: str = CAMPAIGN) -> Path:
    path = box.project / campaign.CAMPAIGN_DIR / "c-1.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_profile(box: boxes.Box, command: Path) -> None:
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 30\n', encoding="utf-8"
    )


def fake_executor(tmp_path: Path, diffs: list[str]) -> Path:
    script = tmp_path / "fake-executor.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        f"patches = {json.dumps(diffs)}\n"
        "patch = patches[min(request['attempt'] - 1, len(patches) - 1)]\n"
        "print(json.dumps({'patch': patch, 'model': 'fake-model', 'usage': "
        "{'prompt_tokens': 5, 'completion_tokens': 5, 'cost_cents': 0.1}}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def step(box: boxes.Box, *extra: str) -> int:
    return main(
        [
            "--castra",
            str(box.root.parent),
            "step",
            "probe",
            "--campaign",
            "c-1",
            "--step",
            "s1",
            *extra,
        ]
    )


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


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

INSIDE: dict[str, object] = {
    "id": "F1",
    "title": "the value is wrong",
    "kind": "defect",
    "status": "confirmed",
    "severity": "blocking",
    "source": "reviewer",
    "location": {"file": "src/app.py", "line": 1, "quote": "value = 3"},
}
OUTSIDE: dict[str, object] = {
    "id": "F2",
    "title": "the note is stale",
    "kind": "defect",
    "status": "confirmed",
    "severity": "blocking",
    "source": "reviewer",
    "location": {"file": "docs/note.md", "line": 1, "quote": "note"},
}


def with_report(
    box: boxes.Box,
    findings: list[dict[str, object]],
    verdict: str = "hold",
    run: dict[str, object] | None = None,
) -> campaign.State:
    directory = box.root / "tabularium" / "c-1-s1"
    report = directory / "verify-1" / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"findings": findings}
    if run is not None:
        payload["run"] = run
    report.write_text(json.dumps(payload), encoding="utf-8")
    record = directory / "dispatch-20261124T120000Z.json"
    record.write_text(
        json.dumps(
            {
                "task": "c-1-s1",
                "state": "returned",
                "verify": {"status": verdict, "report": str(report)},
                "attempt": {"paths": ["src/app.py"], "patch_digest": "abc123"},
            }
        ),
        encoding="utf-8",
    )
    return campaign.State(
        path=box.root / "tabularium" / "expeditio" / "c-1" / "state.json",
        campaign="c-1",
        steps={
            "s1": campaign.StepState(
                state="returned",
                verdict=verdict,
                cents=0.1,
                at="2026-11-24T12:00:00Z",
                record=str(record),
                attempts=1,
            )
        },
        cents=0.1,
        at="2026-11-24T12:00:00Z",
    )


def test_the_plan_declares_mode_and_attempts_and_refuses_nonsense(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    assert (plan.mode, plan.attempts) == ("manual", 2)

    plain = CAMPAIGN.replace('mode = "manual"\n', "").replace("attempts = 2\n", "")
    default = campaign.read_campaign(write_campaign(box, plain))
    assert (default.mode, default.attempts) == ("manual", 1)

    for broken, match in (
        (CAMPAIGN.replace('mode = "manual"', 'mode = "sometimes"'), "mode"),
        (CAMPAIGN.replace("attempts = 2", "attempts = 0"), "attempts"),
        (CAMPAIGN.replace("attempts = 2", "attempts = true"), "attempts"),
        (CAMPAIGN.replace("attempts = 2", 'attempts = "two"'), "attempts"),
    ):
        with pytest.raises(boxes.HarnessError, match=match):
            campaign.read_campaign(write_campaign(box, broken))


def test_an_auto_campaign_is_a_valid_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_campaign(box, CAMPAIGN.replace('mode = "manual"', 'mode = "auto"'))
    assert run(box, "campaign", "probe") == 0
    out = capsys.readouterr().out
    assert "mode       auto, 2 attempts per step" in out
    assert "problem" not in out


def test_a_retry_is_allowed_for_a_finding_inside_the_declared_paths(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    state = with_report(box, [INSIDE])
    decision = campaign.retry(box, plan, plan.steps[0], state)
    assert decision is not None and decision.allowed
    assert "inside the declared paths" in decision.reason


def test_a_finding_outside_the_paths_stops_the_retry(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    state = with_report(box, [OUTSIDE])
    decision = campaign.retry(box, plan, plan.steps[0], state)
    assert decision is not None and not decision.allowed
    assert "docs/note.md" in decision.reason and "outside the declared paths" in decision.reason


def test_the_attempt_ceiling_and_the_budget_stop_the_retry(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    spent = campaign.State(
        path=box.root / "state.json",
        campaign="c-1",
        steps={
            "s1": campaign.StepState(
                state="returned",
                verdict="hold",
                cents=0.1,
                at="",
                record=str(box.root / "missing.json"),
                attempts=2,
            )
        },
        cents=10.0,
        at="",
    )
    decision = campaign.retry(box, plan, plan.steps[0], spent)
    assert decision is not None and not decision.allowed
    assert "attempts 2 of 2" in decision.reason

    budget_plan = campaign.read_campaign(
        write_campaign(box, CAMPAIGN.replace("max_cents = 10", "max_cents = 1"))
    )
    state = with_report(box, [INSIDE])
    state = campaign.State(
        path=state.path,
        campaign=state.campaign,
        steps=state.steps,
        cents=2.0,
        at=state.at,
    )
    decision = campaign.retry(box, budget_plan, budget_plan.steps[0], state)
    assert decision is not None and not decision.allowed
    assert "spent 2.00 cents" in decision.reason


def test_a_recorded_stop_is_not_reconsidered(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    state = with_report(box, [INSIDE])
    stopped = campaign.State(
        path=state.path,
        campaign=state.campaign,
        steps={
            "s1": campaign.StepState(
                state="returned",
                verdict="hold",
                cents=0.1,
                at="",
                record=state.steps["s1"].record,
                attempts=1,
                stop="the change did not change",
            )
        },
        cents=0.1,
        at="",
    )
    decision = campaign.retry(box, plan, plan.steps[0], stopped)
    assert decision is not None and not decision.allowed
    assert decision.reason == "the change did not change"


def test_a_step_that_wrote_nothing_may_be_tried_again(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    state = with_report(box, [], verdict="pass")
    noop = campaign.State(
        path=state.path,
        campaign="c-1",
        steps={
            "s1": campaign.StepState(
                state="noop",
                verdict="pass",
                cents=0.1,
                at="",
                record=state.steps["s1"].record,
                attempts=1,
            )
        },
        cents=0.1,
        at="",
    )
    decision = campaign.retry(box, plan, plan.steps[0], noop)
    assert decision is not None and decision.allowed
    assert "wrote nothing" in decision.reason


def test_a_verdict_without_a_place_or_a_verdict_at_all_stops_the_retry(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    check_failure: dict[str, object] = {
        "id": "check-value",
        "title": "value failed",
        "kind": "check-failure",
        "status": "confirmed",
        "severity": "blocking",
        "source": "check",
    }
    state = with_report(box, [check_failure])
    decision = campaign.retry(box, plan, plan.steps[0], state)
    assert decision is not None and decision.allowed and "check(s) failed" in decision.reason

    no_verdict = campaign.State(
        path=state.path,
        campaign="c-1",
        steps={
            "s1": campaign.StepState(
                state="returned",
                verdict=None,
                cents=0.1,
                at="",
                record=state.steps["s1"].record,
                attempts=1,
            )
        },
        cents=0.1,
        at="",
    )
    decision = campaign.retry(box, plan, plan.steps[0], no_verdict)
    assert decision is not None and not decision.allowed and "no verdict" in decision.reason

    # A failed executor is not a refused change: nothing was applied and nothing was judged, so
    # one technical retry is allowed — the same answer cannot repeat because there was none.
    record_path = Path(str(state.steps["s1"].record))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert isinstance(record, dict)
    record["verify"] = None
    record["executor"] = {"status": "failed", "reason": "the model answered without a diff"}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    decision = campaign.retry(box, plan, plan.steps[0], no_verdict)
    assert decision is not None and decision.allowed and "executor failed" in decision.reason


@requires_trajan
def test_a_retry_that_changes_the_answer_runs_again_and_closes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_campaign(box)
    write_profile(box, fake_executor(tmp_path, [BAD, GOOD]))
    assert run(box, "approve", "probe") == 0
    capsys.readouterr()

    assert step(box, "--json") == 1
    first = json.loads(capsys.readouterr().out)
    assert first["step"]["state"] == "returned"
    assert first["step"]["verdict"] == "hold"
    assert (box.project / "src" / "app.py").read_text() == "value = 3\n"

    assert step(box, "--retry", "--json") == 0
    second = json.loads(capsys.readouterr().out)
    assert second["step"]["state"] == "closed"
    assert second["step"]["verdict"] == "pass"
    assert second["step"]["state"] == "closed"
    assert (box.project / "src" / "app.py").read_text() == "value = 2\n"
    assert second["campaign"]["verdict"]["status"] == "complete"

    stored = json.loads(
        (box.root / "tabularium" / "expeditio" / "c-1" / "state.json").read_text(encoding="utf-8")
    )
    assert stored["steps"]["s1"]["attempts"] == 2
    ledger = box.root / "fasti"
    events = [
        json.loads(line)
        for path in ledger.glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(item["type"] == "step.draft-reverted" for item in events)

    subject = git(box.project, "log", "-1", "--format=%s")
    assert subject.startswith("bugfix:") and "bump the value" in subject


@requires_trajan
def test_a_retry_that_answers_the_same_change_is_stopped_before_it_is_applied_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_campaign(box)
    write_profile(box, fake_executor(tmp_path, [BAD]))
    assert run(box, "approve", "probe") == 0
    capsys.readouterr()

    assert step(box) == 1
    assert (box.project / "src" / "app.py").read_text() == "value = 3\n"
    capsys.readouterr()

    assert step(box, "--retry") == 1
    assert (box.project / "src" / "app.py").read_text() == "value = 1\n", (
        "the failed draft was reverted, and the same answer was not applied again"
    )
    assert not (box.root / "tabularium" / "c-1-s1" / "2").exists(), "no second attempt was made"
    stored = json.loads(
        (box.root / "tabularium" / "expeditio" / "c-1" / "state.json").read_text(encoding="utf-8")
    )
    assert stored["steps"]["s1"]["attempts"] == 2
    assert "same change" in stored["steps"]["s1"]["stop"]

    assert step(box, "--retry") == 2
    assert "refused" in capsys.readouterr().err


@requires_trajan
def test_a_step_that_already_ran_needs_the_retry_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_campaign(box)
    write_profile(box, fake_executor(tmp_path, [BAD]))
    assert run(box, "approve", "probe") == 0
    capsys.readouterr()
    assert step(box) == 1
    capsys.readouterr()
    assert step(box) == 2
    err = capsys.readouterr().err
    assert "needs `--retry`" in err




def test_the_previous_failure_is_told_as_facts(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    state = with_report(box, [INSIDE])
    previous = campaign.previous_failure(state, plan.steps[0])
    assert previous is not None
    assert previous["verdict"] == "hold"
    assert previous["findings"] == ["src/app.py: the value is wrong"]
    assert previous["attempts"] == 1

    lines = executor.previous_lines(previous)
    assert any("hold" in line for line in lines)
    assert any("src/app.py" in line for line in lines)


def test_a_failed_check_carries_the_message_not_only_the_exit_code(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    finding: dict[str, object] = {
        "id": "check-lint",
        "title": "ruff failed",
        "kind": "check-failure",
        "status": "confirmed",
        "detail": "exit 1, expected 0",
        "evidence": ["lint", "checks/lint.stdout.txt"],
    }
    check: dict[str, object] = {
        "check_id": "lint",
        "status": "failed",
        "output_tail": (
            "invalid-syntax: Expected an indented block after function definition\n"
            " --> tests/unit/test_m.py:8:27\n\nFound 1 error."
        ),
    }
    state = with_report(box, [finding], run={"checks": [check]})
    previous = campaign.previous_failure(state, plan.steps[0])
    assert previous is not None
    checks = previous["checks"]
    assert isinstance(checks, list)
    assert any("invalid-syntax" in str(item) for item in checks)
    assert any("test_m.py:8:27" in str(item) for item in checks)

    lines = executor.previous_lines(previous)
    assert any("invalid-syntax" in line for line in lines)


def test_a_failed_check_without_output_keeps_the_exit_code(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    finding: dict[str, object] = {
        "id": "check-tests",
        "title": "test suite failed",
        "kind": "check-failure",
        "status": "confirmed",
        "detail": "exit 2, expected 0",
        "evidence": ["tests", "checks/tests.stdout.txt"],
    }
    state = with_report(box, [finding])
    previous = campaign.previous_failure(state, plan.steps[0])
    assert previous is not None
    assert previous["checks"] == ["test suite failed (exit 2, expected 0)"]


def test_a_closed_step_has_no_previous_failure(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    plan = campaign.read_campaign(write_campaign(box))
    state = with_report(box, [INSIDE])
    closed = campaign.State(
        path=state.path,
        campaign="c-1",
        steps={
            "s1": campaign.StepState(
                state="closed",
                verdict="pass",
                cents=0.1,
                at="",
                record=state.steps["s1"].record,
                attempts=1,
            )
        },
        cents=0.1,
        at="",
    )
    assert campaign.previous_failure(closed, plan.steps[0]) is None
    assert campaign.previous_failure(None, plan.steps[0]) is None
