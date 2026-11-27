"""The campaign: a plan in the repository, a record in the box, a verdict computed from both.

The point of these tests is that the verdict is not a feeling: a campaign is complete only when
every step closed with a strict pass, every acceptance item rests on such a step, the frame is
the approved one and the money stayed inside the budget. Everything short of that is a named
status with reasons, and a plan that cannot start is refused before anything runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
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
goal = "document the commit rule"
frame = "{frame}"

[[step]]
id = "s1"
goal = "write the note"
kind = "docs"
paths = ["docs/**"]
expected = "a section in the docs"
[step.contract]
symptom = "the work is not done"
visible = "a section in the docs"
boundaries = "docs/**"
missing = ""

[[step]]
id = "s2"
goal = "check the note"
kind = "docs"
paths = ["docs/**"]
expected = "the note is checked"
depends_on = ["s1"]
[step.contract]
symptom = "the work is not done"
visible = "the note is checked"
boundaries = "docs/**"
missing = ""

[[acceptance]]
id = "a1"
statement = "the docs describe the rule"
step = "s2"

[budget]
max_cents = 10
max_seconds = 600
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "src" / "probe").mkdir(parents=True)
    (project / "src" / "probe" / "__init__.py").write_text("", encoding="utf-8")
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("note\n", encoding="utf-8")
    (project / ".pantheon").mkdir()
    (project / ".pantheon" / "architecture.toml").write_text(MODEL, encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path, project: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", project)


def approved_frame(project: Path, box: boxes.Box) -> str:
    assert run(box, "approve", box.name) == 0
    from harness import frame

    return frame.read_frame(project).digest


def write_campaign(project: Path, text: str, name: str = "c-1.toml") -> Path:
    directory = project / ".pantheon" / "expeditiones"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def write_state(
    box: boxes.Box,
    steps: dict[str, dict[str, object]],
    cents: float = 0.0,
    campaign: str = "c-1",
    folder: str | None = None,
) -> None:
    directory = box.root / "tabularium" / "expeditio" / (folder or campaign)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "campaign": campaign,
        "at": "2026-11-24T19:00:00Z",
        "plan": {"digest": "sha256:plan"},
        "steps": steps,
        "spend": {"cents": cents, "seconds": 0},
    }
    (directory / "state.json").write_text(json.dumps(data), encoding="utf-8")


def closed(verdict: str = "pass") -> dict[str, object]:
    return {"state": "closed", "verdict": verdict, "cents": 0.5, "at": "2026-11-24T19:00:00Z"}


def run(box: boxes.Box, *args: str) -> int:
    return main(["--castra", str(box.root.parent), *args])


def check(box: boxes.Box, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = run(box, "campaign", box.name)
    return code, capsys.readouterr().out


def test_a_declared_campaign_is_ready_when_nothing_contradicts_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    write_campaign(project, CAMPAIGN.format(frame=digest))
    code, out = check(box, capsys)
    assert code == 0
    assert "campaign   c-1" in out
    assert "frame      approved" in out
    assert "planned    against sha256:" in out
    assert "  s1         docs           not started" in out
    assert "  s2         docs           not started after s1" in out
    assert "accept     a1: the docs describe the rule — not satisfied by s2" in out
    assert "budget     0.00 cents of 10" in out
    assert "verdict    ready — 0 of 2 steps closed" in out


def test_the_verdict_is_complete_only_when_everything_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    write_campaign(project, CAMPAIGN.format(frame=digest))
    write_state(box, {"s1": closed(), "s2": closed()})
    code, out = check(box, capsys)
    assert code == 0
    assert "verdict    complete — 2 of 2 steps closed" in out


def test_one_conditioned_step_keeps_the_campaign_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    write_campaign(project, CAMPAIGN.format(frame=digest))
    write_state(box, {"s1": closed(), "s2": closed("pass_with_conditions")})
    code, out = check(box, capsys)
    assert code == 0
    assert "verdict    incomplete — 1 of 2 steps closed" in out
    assert "steps without a strict pass: s2 (pass_with_conditions)" in out
    assert "acceptance a1 rests on step s2, which has no strict pass" in out


def test_a_campaign_that_spent_more_than_it_declared_is_over_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    write_campaign(project, CAMPAIGN.format(frame=digest))
    write_state(box, {"s1": closed(), "s2": closed()}, cents=42.5)
    code, out = check(box, capsys)
    assert code == 0
    assert "verdict    over budget" in out
    assert "spent 42.50 cents of the 10" in out


def test_a_campaign_against_an_unapproved_frame_cannot_be_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    (project / ".trajan" / "context.toml").write_text("schema_version = 1\n", encoding="utf-8")
    write_campaign(project, CAMPAIGN.format(frame=digest))
    write_state(box, {"s1": closed(), "s2": closed()})
    code, out = check(box, capsys)
    assert code == 0
    assert "verdict    incomplete" in out
    assert "changed after it was approved" in out


def test_a_plan_that_cannot_start_is_refused_with_every_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    broken = CAMPAIGN.format(frame=digest).replace(
        'depends_on = ["s1"]', 'depends_on = ["s1", "s2"]'
    )
    broken = broken.replace('paths = ["docs/**"]', 'paths = [".pantheon/architecture.toml"]', 1)
    write_campaign(project, broken)
    code, out = check(box, capsys)
    assert code == 1
    assert "verdict    blocked — 0 of 2 steps closed" in out
    assert "path .pantheon/architecture.toml is never written by kind 'docs'" in out
    assert "depends on itself" in out


def test_a_duplicate_step_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    text = CAMPAIGN.format(frame=digest).replace('id = "s2"', 'id = "s1"')
    write_campaign(project, text)
    code, out = check(box, capsys)
    assert code == 1 and "declared twice" in out


def test_a_loop_between_steps_is_named_as_a_loop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    text = CAMPAIGN.format(frame=digest).replace(
        '''id = "s1"
goal = "write the note"''',
        '''id = "s1"
goal = "write the note"
depends_on = ["s2"]''',
    )
    write_campaign(project, text)
    code, out = check(box, capsys)
    assert code == 1 and "loop: s1 → s2 → s1" in out


def test_an_unknown_key_in_a_campaign_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    write_campaign(project, "owner = \"me\"\n" + CAMPAIGN.format(frame=digest))
    assert run(box, "campaign", box.name) == 2
    assert "unknown key" in capsys.readouterr().err


def test_state_of_another_campaign_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    digest = approved_frame(project, box)
    capsys.readouterr()
    write_campaign(project, CAMPAIGN.format(frame=digest))
    write_state(box, {"s1": closed()}, campaign="c-2", folder="c-1")
    assert run(box, "campaign", box.name) == 2
    assert "not of 'c-1'" in capsys.readouterr().err


def test_a_step_can_cap_what_it_reads(tmp_path: Path) -> None:
    from harness import campaign

    project = make_project(tmp_path)
    text = CAMPAIGN.replace(
        'expected = "a section in the docs"',
        'expected = "a section in the docs"\nmax_read_chars = 4000',
    ).format(frame="sha256:" + "0" * 64)
    plan = campaign.read_campaign(write_campaign(project, text))
    task = campaign.task_for(plan, plan.steps[0])
    assert task.budget["max_read_chars"] == 4000


def test_a_step_can_name_the_spec_package_it_rests_on(tmp_path: Path) -> None:
    from harness import campaign

    project = make_project(tmp_path)
    text = CAMPAIGN.replace(
        'expected = "a section in the docs"',
        'expected = "a section in the docs"\nspec = "pkg-one"',
    ).format(frame="sha256:" + "0" * 64)
    plan = campaign.read_campaign(write_campaign(project, text))
    task = campaign.task_for(plan, plan.steps[0])
    assert task.spec == "pkg-one", "a feature step must carry the package the spec gate reads"
    assert task.spec_task == plan.steps[0].id, (
        "the step is dispatched under the campaign's name, so the gate needs the package's id"
    )


def test_a_refusal_at_the_gate_uses_no_attempt_and_can_be_retried(tmp_path: Path) -> None:
    from harness import augustus, campaign

    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    plan = campaign.read_campaign(
        write_campaign(project, CAMPAIGN.format(frame=approved_frame(project, box)))
    )
    step = plan.steps[0]
    result = augustus.Dispatch(
        task=campaign.task_for(plan, step),
        state="refused",
        stages=(),
        directory=tmp_path / "record",
    )
    campaign.record_step(box, plan, step, result)
    state = campaign.read_state(box, plan)
    assert state is not None
    recorded = state.steps[step.id]
    assert recorded.state == "refused"
    assert recorded.attempts == 0, "a refusal before the model is not an attempt at the work"
    decision = campaign.retry(box, plan, step, state)
    assert decision is not None and decision.allowed, (
        "the gate refused before any model call: fixing the gate and dispatching again is not "
        "the same attempt repeated"
    )
    assert "refused at the gate" in decision.reason


def test_a_step_carries_the_repro_of_its_package_task(tmp_path: Path) -> None:
    from harness import augustus, campaign

    project = make_project(tmp_path)
    text = CAMPAIGN.replace(
        'kind = "docs"\npaths = ["docs/**"]\nexpected = "a section in the docs"',
        'kind = "feature"\npaths = ["docs/**"]\nexpected = "a section in the docs"\n'
        'spec = "pkg-one"\nrepro = ["python", "-c", "assert 1 == 1"]',
    ).format(frame="sha256:" + "0" * 64)
    plan = campaign.read_campaign(write_campaign(project, text))
    task = campaign.task_for(plan, plan.steps[0])
    assert task.repro == ("python", "-c", "assert 1 == 1"), (
        "the step's verdict has to be the check the owner accepted, not the project's suite"
    )
    found = campaign.problems(plan, project, augustus.DEFAULT_ROUTES)
    assert not any("declares no repro" in item for item in found)


def test_a_feature_step_that_rests_on_a_package_without_repro_is_refused(
    tmp_path: Path,
) -> None:
    from harness import augustus, campaign

    project = make_project(tmp_path)
    text = CAMPAIGN.replace(
        'kind = "docs"\npaths = ["docs/**"]\nexpected = "a section in the docs"',
        'kind = "feature"\npaths = ["docs/**"]\nexpected = "a section in the docs"\n'
        'spec = "pkg-one"',
    ).format(frame="sha256:" + "0" * 64)
    plan = campaign.read_campaign(write_campaign(project, text))
    found = campaign.problems(plan, project, augustus.DEFAULT_ROUTES)
    assert any("declares no repro" in item for item in found), (
        "a package step without the task's repro would verify the project, not the criterion"
    )


def test_a_blocked_plan_still_counts_the_steps_the_box_closed(tmp_path: Path) -> None:
    from harness import augustus, campaign

    project = make_project(tmp_path)
    box = make_box(tmp_path, project)
    text = CAMPAIGN.replace(
        'kind = "docs"\npaths = ["docs/**"]\nexpected = "a section in the docs"',
        'kind = "feature"\npaths = ["docs/**"]\nexpected = "a section in the docs"\n'
        'spec = "pkg-one"',
    ).format(frame="sha256:" + "0" * 64)
    plan = campaign.read_campaign(write_campaign(project, text))
    write_state(box, {"s1": closed()})
    report = campaign.report(box, plan, routes=augustus.DEFAULT_ROUTES)
    assert report.verdict.status == "blocked"
    assert report.verdict.closed == 1, (
        "the box closed the step; a blocked plan must not report it as not closed"
    )


def test_a_step_can_name_the_records_it_wants(tmp_path: Path) -> None:
    from harness import campaign

    project = make_project(tmp_path)
    text = CAMPAIGN.replace(
        'expected = "a section in the docs"',
        'expected = "a section in the docs"\nrecords = ["t-old/20261124T120000Z"]',
    ).format(frame="sha256:" + "0" * 64)
    plan = campaign.read_campaign(write_campaign(project, text))
    task = campaign.task_for(plan, plan.steps[0])
    assert task.records == ("t-old/20261124T120000Z",)
    with pytest.raises(boxes.HarnessError, match="not a record address"):
        campaign.read_campaign(
            write_campaign(project, text.replace("t-old/20261124T120000Z", "t-old"))
        )
