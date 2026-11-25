"""The context pack: what the model is told before it is asked, and what the record can prove.

These tests hold the reasons the pack exists: a past task near the declared paths is put in
front of the model with the fact that selected it, a task from another area is named as left
out instead of silently missing, the pack is bounded, and the dispatch record can say exactly
what the model was told. The last test runs a dispatch with an executor that refuses to work
unless the pack is in its request.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import context, records
from harness.cli import main
from harness.executors import container as box_agent
from harness.executors import deepseek

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

TASK = """\
schema_version = 1
id = "t-1"
goal = "bump the value"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[budget]
max_seconds = 60
max_cents = 1
"""

DIFF = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

DOCS_TASK = """\
schema_version = 1
id = "t-docs"
goal = "note the pack"
kind = "docs"
paths = ["docs/**"]
expected = "a line in docs/note.md"

[budget]
max_seconds = 60
max_cents = 1
"""

DOCS_DIFF = """\
--- a/docs/note.md
+++ b/docs/note.md
@@ -1 +1,2 @@
 note
+more
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


def make_box(tmp_path: Path) -> boxes.Box:
    return boxes.new_box(tmp_path / "castra", make_project(tmp_path))


def write_record(
    box: boxes.Box,
    task: str,
    stamp: str = "20261124T120000Z",
    *,
    goal: str = "touch the app",
    paths: tuple[str, ...] = ("src/**",),
    changed: tuple[str, ...] = ("src/app.py",),
) -> None:
    directory = box.root / "tabularium" / task
    directory.mkdir(parents=True, exist_ok=True)
    contract = box.root / "mandata" / f"{task}.contract.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "schema_version = 1\n"
        f'task = "{task}"\n'
        'kind = "bugfix"\n'
        f'goal = "{goal}"\n'
        f"allowed_paths = {json.dumps(list(paths))}\n",
        encoding="utf-8",
    )
    (directory / f"dispatch-{stamp}.json").write_text(
        json.dumps(
            {
                "task": task,
                "kind": "bugfix",
                "paths": list(paths),
                "state": "closed",
                "role": "executor",
                "verify": {"status": "pass"},
                "attempt": {"paths": list(changed)},
                "commit": None,
            }
        ),
        encoding="utf-8",
    )


def write_task(tmp_path: Path) -> Path:
    path = tmp_path / "t-1.toml"
    path.write_text(TASK, encoding="utf-8")
    return path


def write_profile(box: boxes.Box, command: Path) -> None:
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 30\n', encoding="utf-8"
    )


def fake_executor(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-executor.py"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


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

def sees_the_pack(patch: str) -> str:
    return f"""\
import json, sys
request = json.load(sys.stdin)
lines = (request.get("context") or {{}}).get("lines") or []
if not any("prior" in line for line in lines):
    print("the request carries no context pack: " + json.dumps(lines), file=sys.stderr)
    sys.exit(2)
print(json.dumps({{
    "patch": {json.dumps(patch)},
    "model": "fake-model",
    "usage": {{"prompt_tokens": 10, "completion_tokens": 5, "cost_cents": 0.1}},
    "notes": ["the pack was in the request"],
}}))
"""


SEES_THE_PACK = sees_the_pack(DIFF)


def test_a_past_record_on_the_declared_paths_is_selected_with_its_reason(
    tmp_path: Path,
) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-old", goal="bump the value once")
    pack = context.build(box, "t-1", ("src/**",))
    assert [item.entry.task for item in pack.selected] == ["t-old"]
    assert pack.selected[0].reason == "touches src/app.py"
    text = "\n".join(pack.lines)
    assert "t-old" in text and "why" not in text
    assert "tabularium/t-old/dispatch-20261124T120000Z.json" in text
    assert "frame:" in text
    assert pack.omitted["no_overlap"] == 0


def test_a_record_from_another_area_is_named_as_left_out(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-docs", paths=("docs/**",), changed=("docs/note.md",))
    pack = context.build(box, "t-1", ("src/**",))
    assert pack.selected == ()
    text = "\n".join(pack.lines)
    assert "records: no record touches the declared paths" in text
    assert "left out: 1 record does not touch the declared paths" in text
    assert pack.omitted["no_overlap"] == 1


def test_the_task_does_not_pick_its_own_earlier_record(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-1", goal="the earlier attempt")
    pack = context.build(box, "t-1", ("src/**",))
    assert pack.selected == ()
    assert pack.omitted["own"] == 1
    assert "this task's own earlier record" in "\n".join(pack.lines)


def test_selection_is_capped_and_the_rest_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "MAX_SELECTED", 1)
    box = make_box(tmp_path)
    write_record(box, "t-a", "20261124T100000Z")
    write_record(box, "t-b", "20261124T110000Z")
    pack = context.build(box, "t-1", ("src/**",))
    assert len(pack.selected) == 1
    assert pack.selected[0].entry.task == "t-b", "the digests newest-first order is kept"
    assert pack.omitted["over_limit"] == 1
    assert "selection cap" in "\n".join(pack.lines)


def test_the_size_cap_drops_lines_instead_of_growing_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "LIMIT_CHARS", 400)
    box = make_box(tmp_path)
    write_record(box, "t-long", goal="a very long goal " * 8)
    pack = context.build(box, "t-1", ("src/**",))
    assert pack.selected, "the record still matched"
    assert pack.omitted["over_budget"] == 1
    assert "size cap" in pack.lines[-1]
    assert pack.selected[0].entry.task not in "\n".join(pack.lines[:-1])


def test_the_pack_is_written_with_the_frame_it_rested_on(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    assert run(box, "approve", "probe") == 0
    pack = context.build(box, "t-1", ("src/**",))
    assert pack.frame.state == "approved"
    assert pack.lines[1].startswith("frame: approved")
    context.write(pack)
    written = json.loads(pack.path.read_text(encoding="utf-8"))
    assert written["task"] == "t-1"
    assert written["frame"]["state"] == "approved"
    assert written["records"]["digest"] == pack.records_digest
    assert written["budget"]["limit"] == context.LIMIT_CHARS
    sent = context.payload(pack)
    assert sent["path"] == str(pack.path)
    assert sent["lines"] == list(pack.lines)


@requires_trajan
def test_a_dispatch_gives_the_model_the_pack_and_records_what_it_was_told(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_record(box, "prior", goal="the earlier app change")
    write_profile(box, fake_executor(tmp_path, SEES_THE_PACK))
    task = write_task(tmp_path)
    assert run(box, "dispatch", "probe", "--task", str(task), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "closed"
    pack = payload["context"]
    assert pack["records"]["selected"][0]["task"] == "prior"
    assert pack["records"]["selected"][0]["reason"] == "touches src/app.py"
    assert pack["budget"]["chars"] <= pack["budget"]["limit"] + 200
    assert Path(pack["path"]).is_file()

    request = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert any("prior" in line for line in request["context"]["lines"])

    assert "the pack was in the request" in payload["executor"]["notes"]
    outbox = next((box.root / "acta").glob("*.md")).read_text(encoding="utf-8")
    assert "context: 1 past records by paths" in outbox

    stored = records.read(box)
    assert stored is not None and stored.count == 2, "the digest was written after the record"
    fresh, state = records.state(box)
    assert state == "fresh" and fresh.count == 2


@requires_trajan
def test_a_declared_executor_makes_a_checks_only_kind_do_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_record(box, "prior", paths=("docs/**",), changed=("docs/note.md",))
    write_profile(box, fake_executor(tmp_path, sees_the_pack(DOCS_DIFF)))
    task = tmp_path / "t-docs.toml"
    task.write_text(DOCS_TASK, encoding="utf-8")
    assert run(box, "dispatch", "probe", "--task", str(task), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stages"] == ["run", "verify"], "the default docs route is checks-only"
    assert payload["executor"]["status"] == "ok"
    assert payload["state"] == "closed"
    assert payload["context"]["records"]["selected"][0]["task"] == "prior"
    assert (box.project / "docs" / "note.md").read_text().endswith("more\n")


def test_both_executors_put_the_pack_in_the_prompt(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    lines = ["CONTEXT (assembled by code):", "- t-old | docs | touches src/app.py"]
    prompt = deepseek._prompt(
        {"id": "t-1", "goal": "g", "kind": "bugfix", "paths": ["src/**"]}, project, lines
    )
    assert "CONTEXT (assembled by code):" in prompt.text
    assert prompt.text.index("CONTEXT") < prompt.text.index("FILES IN THE PROJECT")

    builder = box_agent._prompt(
        {"role": "executor", "goal": "g", "paths": ["src/**"]}, lines
    )
    assert "Контекст, собранный коробкой" in builder
    assert "- t-old" in builder

    architect = box_agent._prompt(
        {"role": "architect", "goal": "g", "paths": [".pantheon/**"]}, lines
    )
    assert "Контекст, собранный коробкой" in architect
    assert "Ты — архитектор коробки" in architect


def test_a_named_record_is_selected_even_when_its_paths_do_not_overlap(
    tmp_path: Path,
) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-docs", paths=("docs/**",), changed=("docs/note.md",))
    pack = context.build(box, "t-1", ("src/**",), named=("t-docs/20261124T120000Z",))
    assert [item.entry.task for item in pack.selected] == ["t-docs"]
    assert pack.selected[0].reason == context.NAMED_REASON
    assert pack.named == ("t-docs/20261124T120000Z",)
    text = "\n".join(pack.lines)
    assert "t-docs/20261124T120000Z -> " in text
    assert "named by the task" in text
    assert "1 named record(s)" in context.line(pack)
    assert pack.omitted["no_overlap"] == 0
    assert context.payload(pack)["named"] == ["t-docs/20261124T120000Z"]


def test_a_named_address_the_digest_does_not_have_is_a_refusal(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-docs", paths=("docs/**",), changed=("docs/note.md",))
    with pytest.raises(boxes.HarnessError, match="t-docs/20260101T000000Z"):
        context.build(box, "t-1", ("src/**",), named=("t-docs/20260101T000000Z",))


def test_the_task_may_name_its_own_earlier_record(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-1")
    pack = context.build(box, "t-1", ("src/**",), named=("t-1/20261124T120000Z",))
    assert [item.entry.task for item in pack.selected] == ["t-1"]
    assert pack.named == ("t-1/20261124T120000Z",)


def test_named_records_share_the_selection_cap_and_are_not_doubled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "MAX_SELECTED", 1)
    box = make_box(tmp_path)
    write_record(box, "t-a", "20261124T100000Z")
    write_record(box, "t-b", "20261124T110000Z")
    pack = context.build(box, "t-1", ("src/**",), named=("t-a/20261124T100000Z",))
    assert [item.entry.task for item in pack.selected] == ["t-a"]
    assert pack.omitted["over_limit"] == 1
    with pytest.raises(boxes.HarnessError, match="at most 1"):
        context.build(
            box,
            "t-1",
            ("src/**",),
            named=("t-a/20261124T100000Z", "t-b/20261124T110000Z"),
        )


def test_a_named_record_is_not_selected_twice_by_its_paths(tmp_path: Path) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-old")
    pack = context.build(box, "t-1", ("src/**",), named=("t-old/20261124T120000Z",))
    assert [item.reason for item in pack.selected] == [context.NAMED_REASON]
    assert "1 named record(s)" in context.line(pack)
    assert "2 past records" not in context.line(pack)


@requires_trajan
def test_a_bad_address_stops_the_dispatch_before_the_model_is_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_record(box, "prior")
    write_profile(box, fake_executor(tmp_path, SEES_THE_PACK))
    task = tmp_path / "t-1.toml"
    task.write_text(
        TASK.replace("[budget]", 'records = ["prior/20260101T000000Z"]\n\n[budget]'),
        encoding="utf-8",
    )
    assert run(box, "dispatch", "probe", "--task", str(task)) == 2
    assert "prior/20260101T000000Z" in capsys.readouterr().err
    assert not (box.root / "tabularium" / "t-1" / "proposal-1").exists()


@requires_trajan
def test_a_named_record_reaches_the_model_and_the_record_keeps_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    box = make_box(tmp_path)
    write_record(box, "t-docs", paths=("docs/**",), changed=("docs/note.md",))
    asks_for_the_named = f"""\
import json, sys
request = json.load(sys.stdin)
lines = (request.get("context") or {{}}).get("lines") or []
if not any("named by the task" in line for line in lines):
    print("the request carries no named record", file=sys.stderr)
    sys.exit(2)
print(json.dumps({{
    "patch": {json.dumps(DIFF)},
    "model": "fake-model",
    "usage": {{"prompt_tokens": 10, "completion_tokens": 5, "cost_cents": 0.1}},
}}))
"""
    write_profile(box, fake_executor(tmp_path, asks_for_the_named))
    task = tmp_path / "t-1.toml"
    task.write_text(
        TASK.replace("[budget]", 'records = ["t-docs/20261124T120000Z"]\n\n[budget]'),
        encoding="utf-8",
    )
    assert run(box, "dispatch", "probe", "--task", str(task), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "closed"
    assert payload["context"]["records"]["named"] == ["t-docs/20261124T120000Z"]
    assert payload["context"]["records"]["selected"][0]["reason"] == "named by the task"
    request = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        "t-docs/20261124T120000Z" in line for line in request["context"]["lines"]
    ), "the named address is in the request the model sees"
