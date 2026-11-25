"""The executor: who proposes a change, what it costs, and what the harness refuses to believe.

The cases that matter are the ones this module exists for: a price that was reported rather
than assumed, a budget that stops an answer instead of decoratively accompanying it, and an
executor that writes to the project behind the gate — caught, recorded, and never verified.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from harness import augustus, contour, executor
from harness import box as boxes
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

TASK = """\
schema_version = 1
id = "t-1"
goal = "change the app value"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[budget]
max_cents = 5
max_seconds = 60
"""

DIFF = """\
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


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project, boxes.new_box(tmp_path / "castra", project)


def write_task(tmp_path: Path, text: str = TASK) -> Path:
    path = tmp_path / "t-1.toml"
    path.write_text(text, encoding="utf-8")
    return path


def fake_executor(tmp_path: Path, body: str, name: str = "fake-executor.py") -> Path:
    script = tmp_path / name
    script.write_text("#!/usr/bin/env python3\nimport json, sys, time\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


def write_profile(box: boxes.Box, command: Path, extra: str = "") -> None:
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{command}"\nmax_seconds = 30\n{extra}',
        encoding="utf-8",
    )


def probe(tmp_path: Path, body: str, extra: str = "") -> tuple[Path, boxes.Box, Path, Path]:
    project, box = make_box(tmp_path)
    script = fake_executor(tmp_path, body)
    write_profile(box, script, extra)
    return project, box, script, write_task(tmp_path)


def dispatch(box: boxes.Box, task: Path, *extra: str) -> int:
    argv = ["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task), *extra]
    return main(argv)


def ledger_events(box: boxes.Box) -> list[dict[str, Any]]:
    path = box.root / "fasti" / f"{datetime.now(UTC):%Y-%m-%d}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


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

ANSWER = """\
request = json.load(sys.stdin)
print(json.dumps({
    "patch": "--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-value = 1\\n+value = 2\\n",
    "model": "fake-model",
    "usage": {"prompt_tokens": 123, "completion_tokens": 45, "cost_cents": 0.5},
    "notes": ["a fake executor"],
}))
"""

ANSWER_CUT = """\
request = json.load(sys.stdin)
print(json.dumps({
    "patch": "--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-value = 1\\n+value = 2\\n",
    "model": "fake-model",
    "usage": {"prompt_tokens": 123, "completion_tokens": 4096, "cost_cents": 0.5},
    "truncated": True,
    "notes": ["the answer hit the token cap: the diff may be incomplete"],
}))
"""


def test_a_box_without_a_profile_declares_no_executor(tmp_path: Path) -> None:
    config = executor.read_config(tmp_path / "executor.toml")
    assert not config.declared
    assert config.command is None
    assert "not declared" in executor.describe(config)
    with pytest.raises(boxes.HarnessError, match="declares no executor"):
        executor.propose(config, _task(), _box(tmp_path), when=None)


def test_the_profile_is_data_and_is_read_strictly(tmp_path: Path) -> None:
    path = tmp_path / "executor.toml"
    path.write_text(
        'schema_version = 1\ncommand = "/bin/true --flag"\nmodel = "m"\n'
        "max_seconds = 120\nmax_cents = 2.5\n",
        encoding="utf-8",
    )
    config = executor.read_config(path)
    assert config.command == ("/bin/true", "--flag")
    assert (config.model, config.max_seconds, config.max_cents) == ("m", 120, 2.5)
    broken = [
        ('schema_version = 1\ncommand = "/bin/true"\nsurprise = 1\n', "unknown key"),
        ('schema_version = 2\ncommand = "/bin/true"\n', "schema_version"),
        ("schema_version = 1\n", "command is required"),
        ('schema_version = 1\ncommand = ""\n', "command is required"),
        ('schema_version = 1\ncommand = "/bin/true"\nmax_seconds = 0\n', "max_seconds"),
        ('schema_version = 1\ncommand = "/bin/true"\nmax_cents = -1\n', "max_cents"),
    ]
    for text, match in broken:
        path.write_text(text, encoding="utf-8")
        with pytest.raises(boxes.HarnessError, match=match):
            executor.read_config(path)


def test_the_task_budget_can_lower_but_not_raise_the_box_cap(tmp_path: Path) -> None:
    config = executor.ExecutorConfig(tmp_path / "e.toml", ("x",), None, 300, 5.0)
    assert executor.limits(config, {}) == (300, 5.0)
    assert executor.limits(config, {"max_seconds": 30, "max_cents": 1}) == (30, 1.0)
    assert executor.limits(config, {"max_seconds": 600, "max_cents": 50}) == (300, 5.0)


def test_the_profile_can_choose_a_model_by_kind(tmp_path: Path) -> None:
    """The table is the box's decision: an entry names the model and its rate, or neither."""
    path = tmp_path / "executor.toml"
    path.write_text(
        'schema_version = 1\ncommand = "/bin/true"\nmodel = "cheap"\n'
        '[models.docs]\nmodel = "cheap"\nprice_in = 0.15\nprice_out = 0.6\n'
        '[models.security]\nmodel = "dear"\n',
        encoding="utf-8",
    )
    config = executor.read_config(path)
    assert config.models["docs"] == executor.ModelChoice("cheap", 0.15, 0.6)
    assert config.models["security"] == executor.ModelChoice("dear")
    assert executor.model_for(config, "security") == (
        executor.ModelChoice("dear"),
        "declared for kind security",
    )
    assert executor.model_for(config, "bugfix") == (
        executor.ModelChoice("cheap"),
        "the profile's model: no entry for kind bugfix",
    )
    assert executor.model_for(config, None) == (
        executor.ModelChoice("cheap"),
        "the profile's model: the task declared no kind",
    )
    assert "by kind: docs \u2192 cheap (0.15/0.6 per M), security \u2192 dear" in (
        executor.describe(config)
    )

    plain = executor.ExecutorConfig(path=path, command=("/bin/true",), model="only")
    assert executor.model_for(plain, "docs") == (executor.ModelChoice("only"), None)

    broken = [
        ('schema_version = 1\ncommand = "/bin/true"\nmodels = "x"\n', "models must be a table"),
        ('schema_version = 1\ncommand = "/bin/true"\n[models.docs]\nmodel = ""\n', "models.docs"),
        (
            'schema_version = 1\ncommand = "/bin/true"\n'
            '[models.docs]\nmodel = "m"\nprice_in = 0.1\n',
            "one half of a rate",
        ),
        (
            'schema_version = 1\ncommand = "/bin/true"\n'
            '[models.docs]\nmodel = "m"\nprice_in = -1\nprice_out = 1\n',
            "price_in",
        ),
        (
            'schema_version = 1\ncommand = "/bin/true"\n[models.docs]\nmodel = "m"\nsurprise = 1\n',
            "unknown key",
        ),
    ]
    for text, match in broken:
        path.write_text(text, encoding="utf-8")
        with pytest.raises(boxes.HarnessError, match=match):
            executor.read_config(path)


def test_a_declared_kind_model_reaches_the_request_and_the_record(tmp_path: Path) -> None:
    """The choice is a fact of the call: the command is told, priced by its own rate, and named."""
    _project, box = make_box(tmp_path)
    script = fake_executor(tmp_path, ANSWER)
    write_profile(
        box,
        script,
        'model = "cheap"\n[models.bugfix]\nmodel = "clever"\nprice_in = 0.5\nprice_out = 1.0\n',
    )
    task = augustus.read_task(write_task(tmp_path))
    config = executor.read_config(box.root / "persona" / "executor.toml")

    proposal = executor.propose(config, task, box, when=None)

    request = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "request.json").read_text()
    )
    assert request["model"] == "clever"
    assert proposal.model == "fake-model", "the answer's own model still wins the field"
    assert proposal.model_choice == "declared for kind bugfix"
    assert proposal.data()["model_choice"] == "declared for kind bugfix"
    assert "model clever chosen: declared for kind bugfix" in proposal.notes

    env = executor._environment(config.models["bugfix"])
    assert env is not None
    assert env["PANTHEON_MODEL_PRICE_IN"] == "0.5"
    assert env["PANTHEON_MODEL_PRICE_OUT"] == "1"
    assert executor._environment(executor.ModelChoice("clever")) is None


@requires_trajan
def test_a_declared_executor_supplies_the_patch_and_the_bill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box, _script, task = probe(tmp_path, ANSWER)
    assert dispatch(box, task, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "closed"
    assert payload["executor"]["status"] == "ok"
    assert payload["executor"]["usage"]["prompt_tokens"] == 123
    assert payload["executor"]["usage"]["completion_tokens"] == 45
    assert payload["executor"]["usage"]["cost_cents"] == 0.5
    assert payload["budget"] == {"max_cents": 5, "max_seconds": 60}
    assert (project / "src" / "app.py").read_text() == "value = 2\n"

    record = box.root / "tabularium" / "t-1" / "proposal-1"
    assert (record / "request.json").is_file()
    assert (record / "stdout.txt").is_file()
    assert (record / "change.diff").read_text() == DIFF
    proposal = json.loads((record / "proposal.json").read_text())
    assert proposal["status"] == "ok" and proposal["model"] == "fake-model"
    assert proposal["usage"]["cost_cents"] == 0.5

    events = ledger_events(box)
    executed = [item for item in events if item["type"] == "task.executed"]
    assert len(executed) == 1
    assert executed[0]["data"]["prompt_tokens"] == 123
    assert executed[0]["data"]["cost_cents"] == 0.5

    outbox = next((box.root / "acta").glob("*.md")).read_text()
    assert "0.50 cents" in outbox
    assert "123 in / 45 out tokens" in outbox
    assert "budget declared in the task: 5 cents, 60 s" in outbox


@requires_trajan
def test_the_change_an_executor_proposed_is_the_change_a_reader_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An executor's diff lives in the attempt, not in the task: the reader must get that file."""
    _project, box, _script, task = probe(tmp_path, ANSWER)
    seen: dict[str, Path | None] = {}
    real = augustus._verify

    def spy(
        box_: boxes.Box,
        directory: Path,
        stamp: datetime,
        root: Path,
        python: Path,
        review: contour.Reviewer | None = None,
        change: Path | None = None,
        paths: tuple[str, ...] | None = None,
        repro: tuple[str, ...] = (),
    ) -> contour.VerifyResult:
        seen["change"] = change
        return real(
            box_, directory, stamp, root, python, review=None, change=change, paths=paths
        )

    monkeypatch.setattr(augustus, "_verify", spy)
    assert dispatch(box, task) == 0
    assert seen["change"] is not None, "a reader would have been told there is no change"
    assert seen["change"].name == "change.diff"
    assert seen["change"].parent.name == "1"
    assert seen["change"].is_file()


@requires_trajan
def test_an_answer_that_is_not_json_is_recorded_and_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box, _script, task = probe(tmp_path, 'print("I have no idea")\n')
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "state      returned" in out and "not JSON" in out
    assert (project / "src" / "app.py").read_text() == "value = 1\n"
    assert not list((box.root / "tabularium" / "t-1").glob("verify-*"))
    record = box.root / "tabularium" / "t-1" / "proposal-1" / "proposal.json"
    proposal = json.loads(record.read_text())
    assert proposal["status"] == "failed" and "not JSON" in proposal["reason"]


@requires_trajan
def test_an_executor_that_reports_its_own_failure_is_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box, _script, task = probe(
        tmp_path,
        'print(json.dumps({"error": "the task needs a human", "model": "fake-model", '
        '"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost_cents": 0.5}}))\n',
    )
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "the task needs a human" in out
    record = box.root / "tabularium" / "t-1" / "proposal-1" / "proposal.json"
    proposal = json.loads(record.read_text())
    assert proposal["status"] == "failed"
    assert proposal["usage"]["cost_cents"] == 0.5, "a failed call still costs money"


@requires_trajan
def test_an_answer_that_costs_more_than_the_budget_is_recorded_not_applied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        "request = json.load(sys.stdin)\n"
        'print(json.dumps({"patch": "--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n'
        '-value = 1\\n+value = 2\\n", "usage": {"prompt_tokens": 10, '
        '"completion_tokens": 5, "cost_cents": 9.0}}))\n'
    )
    project, box, _script, task = probe(tmp_path, body)
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "over-budget" in out and "9.00 cents" in out
    assert (project / "src" / "app.py").read_text() == "value = 1\n"
    assert not list(box.root.glob("tabularium/t-1/verify-*"))


@requires_trajan
def test_an_answer_cut_at_the_cap_is_recorded_not_applied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A diff that stops at the cap can apply and still be half a fix: refuse it, name it."""
    project, box, _script, task = probe(tmp_path, ANSWER_CUT)
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "cut at the token cap" in out
    assert (project / "src" / "app.py").read_text() == "value = 1\n"
    assert not list(box.root.glob("tabularium/t-1/verify-*"))
    proposal = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "proposal.json").read_text()
    )
    assert proposal["status"] == "incomplete"
    assert proposal["usage"]["completion_tokens"] == 4096
    assert proposal["usage"]["cost_cents"] == 0.5, "a cut call still costs money"


@requires_trajan
def test_an_executor_that_writes_to_the_project_behind_the_gate_is_caught(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        "request = json.load(sys.stdin)\n"
        "from pathlib import Path\n"
        'Path(request["project"]["path"], "src", "app.py").write_text("value = 99\\n")\n'
        'print(json.dumps({"patch": "--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n'
        '-value = 99\\n+value = 2\\n", "usage": {"prompt_tokens": 1, '
        '"completion_tokens": 1, "cost_cents": 0.01}}))\n'
    )
    _project, box, _script, task = probe(tmp_path, body)
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "unapproved-write" in out and "src/app.py" in out
    assert not list(box.root.glob("tabularium/t-1/verify-*"))


@requires_trajan
def test_an_executor_that_hangs_is_stopped_at_its_deadline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box, script, task = probe(tmp_path, "time.sleep(10)\n")
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{script}"\nmax_seconds = 1\n', encoding="utf-8"
    )
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "did not answer within 1 s" in out
    assert not list(box.root.glob("tabularium/t-1/verify-*"))


@requires_trajan
def test_a_task_carrying_a_patch_does_not_ask_the_executor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box, _script, _task = probe(
        tmp_path, 'print(json.dumps({"error": "do not call me"}))\n'
    )
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(project, "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    task = write_task(tmp_path, TASK.replace("[budget]", f'change = "{patch}"\n\n[budget]'))
    assert dispatch(box, task) == 0
    out = capsys.readouterr().out
    assert "executor   not run" in out and "attempt    applied" in out


@requires_trajan
def test_a_patch_file_written_into_the_record_directory_is_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        "request = json.load(sys.stdin)\n"
        "from pathlib import Path\n"
        'Path(request["record"]["directory"], "answer.diff").write_text('
        '"--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-value = 1\\n+value = 2\\n")\n'
        'print(json.dumps({"patch_file": "answer.diff", "usage": '
        '{"prompt_tokens": 2, "completion_tokens": 2, "cost_cents": 0.02}}))\n'
    )
    project, box, _script, task = probe(tmp_path, body)
    assert dispatch(box, task) == 0
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    assert (box.root / "tabularium" / "t-1" / "proposal-1" / "patch-source.txt").is_file()


@requires_trajan
def test_the_cli_can_override_the_profile_for_one_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box, _script, task = probe(tmp_path, ANSWER)
    write_profile(box, Path("/bin/false"))
    good = fake_executor(tmp_path, ANSWER, name="good.py")
    assert dispatch(box, task, "--executor", str(good)) == 0
    assert (project / "src" / "app.py").read_text() == "value = 2\n"


def test_status_names_the_executor_and_its_limits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box, script, _task = probe(tmp_path, "pass\n", extra="max_cents = 5\n")
    capsys.readouterr()
    assert main(["--castra", str(box.root.parent), "status", "probe", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["executor"]["declared"] is True
    assert payload["executor"]["command"] == [str(script)]
    assert payload["executor"]["max_cents"] == 5.0


def test_the_adapter_turns_an_endpoint_answer_into_a_patch_and_a_bill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = {
        "task": {"id": "t", "goal": "g", "kind": "bugfix", "paths": ["src/**"]},
        "project": {"path": str(tmp_path)},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setenv(deepseek.KEY_ENV, "key-that-must-not-be-printed")
    monkeypatch.setenv(deepseek.PRICE_IN_ENV, "0.27")
    monkeypatch.setenv(deepseek.PRICE_OUT_ENV, "1.1")

    def fake_post(url: str, key: str, payload: dict[str, object]) -> dict[str, Any]:
        assert "messages" in payload
        return {
            "model": "reported-model",
            "choices": [
                {
                    "message": {"content": "```diff\n" + DIFF + "```"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

    monkeypatch.setattr(deepseek, "_post", fake_post)
    assert deepseek.main() == 0
    out = capsys.readouterr().out
    answer = json.loads(out)
    assert answer["patch"] == DIFF
    assert answer["model"] == "reported-model"
    assert answer["usage"]["cost_cents"] == 0.0049
    assert "key-that-must-not-be-printed" not in out


def test_the_adapter_refuses_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(deepseek.KEY_ENV, raising=False)
    monkeypatch.delenv(deepseek.FALLBACK_KEY_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"task": {"id": "t"}}'))
    assert deepseek.main() == 1


def test_the_adapter_reports_an_answer_without_a_diff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = {"task": {"id": "t"}, "project": {"path": str(tmp_path)}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setenv(deepseek.KEY_ENV, "k")

    def fake_post(url: str, key: str, payload: dict[str, object]) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "I would rather not."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(deepseek, "_post", fake_post)
    monkeypatch.setenv(deepseek.PRICE_IN_ENV, "0.27")
    monkeypatch.setenv(deepseek.PRICE_OUT_ENV, "1.1")
    assert deepseek.main() == 1
    answer = json.loads(capsys.readouterr().out)
    assert "without a unified diff" in answer["error"]
    assert answer["usage"]["cost_cents"] is not None, "a call without a diff is still billed"


def test_a_bare_diff_and_an_error_line_are_understood() -> None:
    assert deepseek.extract_diff(DIFF) == DIFF
    assert deepseek.extract_diff("prefix\n" + DIFF) == DIFF
    assert deepseek.extract_diff("no diff here") is None
    assert deepseek.extract_diff("ERROR: this cannot be a diff") is None
    assert deepseek._error_line("ERROR: this cannot be a diff") == "this cannot be a diff"


def _task() -> Any:
    from harness import augustus

    return augustus.Task(
        path=Path("t.toml"),
        id="t",
        goal=None,
        kind="bugfix",
        paths=("src/**",),
        expected=None,
        change=None,
        forbidden=(),
        budget={},
    )


def _box(tmp_path: Path) -> boxes.Box:
    project = tmp_path / "p"
    project.mkdir()
    return boxes.Box(root=tmp_path, name="b", project=project, created="now")


def test_a_retry_prompt_carries_the_previous_failure(tmp_path: Path) -> None:
    task: dict[str, object] = {
        "id": "t",
        "goal": "g",
        "kind": "bugfix",
        "paths": ["src/**"],
        "role": "executor",
    }
    previous = {
        "verdict": "hold",
        "attempts": 1,
        "reason": "1 check(s) failed on the declared change",
        "checks": ["ruff failed (exit 1)"],
        "findings": ["src/app.py: the value is wrong"],
    }
    prompt = deepseek._prompt(task, tmp_path, None, previous)
    assert "THE PREVIOUS ATTEMPT DID NOT PASS" in prompt.text
    assert "ruff failed (exit 1)" in prompt.text and "src/app.py" in prompt.text
    built = box_agent._prompt(task, None, previous)
    assert "Предыдущая попытка не прошла" in built and "ruff failed" in built
    assert "Каждый файл заканчивается переводом строки" in built


READING_ANSWER = """\
request = json.load(sys.stdin)
print(json.dumps({
    "patch": "--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-value = 1\\n+value = 2\\n",
    "model": "fake-model",
    "usage": {"prompt_tokens": 123, "completion_tokens": 45, "cost_cents": 0.5},
    "reading": {"files": 2, "chars": 40, "tokens_estimate": 10,
                "cap_chars": 60000, "declared_chars": None, "left_out": []},
}))
"""


@requires_trajan
def test_what_the_executor_read_is_recorded_and_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box, _script, task = probe(tmp_path, READING_ANSWER)
    assert dispatch(box, task, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reading"] == {
        "files": 2,
        "chars": 40,
        "tokens_estimate": 10,
        "cap_chars": 60000,
        "declared_chars": None,
        "left_out": [],
    }
    proposal = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "proposal.json").read_text()
    )
    assert proposal["reading"]["chars"] == 40


@requires_trajan
def test_the_dispatch_output_names_what_was_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box, _script, task = probe(tmp_path, READING_ANSWER)
    assert dispatch(box, task) == 0
    out = capsys.readouterr().out
    assert "reading" in out
    assert "2 file(s), 40 chars (~10 tokens)" in out


@requires_trajan
def test_a_task_read_budget_reaches_the_executor(tmp_path: Path) -> None:
    _project, box, _script, task = probe(tmp_path, ANSWER)
    text = task.read_text() + "\nmax_read_chars = 400\n"
    task.write_text(text, encoding="utf-8")
    assert dispatch(box, task) == 0
    request = json.loads(
        (box.root / "tabularium" / "t-1" / "proposal-1" / "request.json").read_text()
    )
    assert request["limits"]["max_read_chars"] == 400


def test_the_read_budget_leaves_files_out_and_names_them(tmp_path: Path) -> None:
    project = tmp_path / "read"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("a" * 50 + "\n", encoding="utf-8")
    (project / "src" / "b.py").write_text("b" * 50 + "\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    prompt = deepseek._prompt({"id": "t", "paths": ["src/**"]}, project, None, None, 60)
    assert prompt.reading["files"] == 1
    assert prompt.reading["declared_chars"] == 60
    left = prompt.reading["left_out"]
    assert isinstance(left, list)
    assert any("1 file(s) after the declared read budget of 60 chars" in str(x) for x in left)
    assert "--- src/b.py ---" not in prompt.text
    assert "--- src/a.py ---" in prompt.text


def test_a_file_that_fits_the_budget_is_read_whole(tmp_path: Path) -> None:
    """A 45k file used to arrive cut to 40k: the model then cannot anchor a hunk below the cut."""
    project = tmp_path / "whole"
    (project / "src").mkdir(parents=True)
    body = "a" * 45_000 + "\n"
    (project / "src" / "big.py").write_text(body, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    prompt = deepseek._prompt({"id": "t", "paths": ["src/**"]}, project, None, None)
    assert prompt.reading["files"] == 1
    assert prompt.reading["chars"] == len(body)
    assert body in prompt.text
    assert prompt.reading["left_out"] == []


@requires_trajan
def test_status_names_what_the_task_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box, _script, task = probe(tmp_path, READING_ANSWER)
    assert dispatch(box, task) == 0
    capsys.readouterr()
    assert main(["--castra", str(box.root.parent), "status", "probe"]) == 0
    out = capsys.readouterr().out
    assert "read 2 file(s), 40 chars" in out


def test_an_answer_cut_before_any_text_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reasoning model can spend the whole budget and return nothing: name that, not "no diff"."""
    request = {"task": {"id": "t"}, "project": {"path": str(tmp_path)}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setenv(deepseek.KEY_ENV, "k")

    def fake_post(url: str, key: str, payload: dict[str, object]) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8192},
        }

    monkeypatch.setattr(deepseek, "_post", fake_post)
    assert deepseek.main() == 1
    answer = json.loads(capsys.readouterr().out)
    assert "cut before any text" in answer["error"]
    assert any("raise" in note for note in answer["notes"])
    assert answer["usage"]["completion_tokens"] == 8192


def test_a_diff_that_hit_the_cap_is_marked_truncated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The diff parses, but the answer was cut: the box must see the cut, not just a patch."""
    request = {"task": {"id": "t"}, "project": {"path": str(tmp_path)}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setenv(deepseek.KEY_ENV, "k")

    def fake_post(url: str, key: str, payload: dict[str, object]) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": DIFF}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
        }

    monkeypatch.setattr(deepseek, "_post", fake_post)
    assert deepseek.main() == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["truncated"] is True
    assert answer["patch"] == DIFF
    assert any("token cap" in note for note in answer["notes"])


def test_a_reproduction_that_already_passes_stops_the_task_before_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect must be shown first: a repro that already passes describes no live defect."""
    _project, box, _script, task = probe(tmp_path, ANSWER)
    text = task.read_text(encoding="utf-8")
    repro = 'repro = ["python", "-c", "raise SystemExit(0)"]\n'
    task.write_text(text.replace("\n[budget]", "\n" + repro + "\n[budget]"), encoding="utf-8")
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "already passes before any change" in out
    assert not list(box.root.glob("tabularium/t-1/proposal-*"))


def test_the_task_shows_the_model_one_check_and_judges_with_another(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two checks, one visible: the model cannot fit an answer to what it never sees.

    A blind executor answers the task it imagines (c-d4); a check it can read gives it a real
    target without handing it the verdict. The visible check passing while the box's own check
    fails is the finding, and the retry is told about the visible one only — handing over the
    verdict's words would make the two checks one.
    """
    _project, box, _script, task = probe(tmp_path, ANSWER)
    text = task.read_text(encoding="utf-8")
    own = (
        'self_check = ["python", "-c", "import sys; '
        "sys.exit(0 if '2' in open('src/app.py').read() else 1)" + '"]\n'
    )
    hidden = 'repro = ["python", "-c", "print(\'HIDDEN-CHECK\'); raise SystemExit(1)"]\n'
    task.write_text(
        text.replace("\n[budget]", "\n" + own + hidden + "\n[budget]"), encoding="utf-8"
    )
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "own check  passed" in out, out
    assert "fitted to what it could see" in out

    record = json.loads(next((box.root / "tabularium" / "t-1").glob("dispatch-*.json")).read_text())
    assert record["self_check"]["status"] == "passed"
    facts = augustus.previous_failure(record)
    assert facts is not None
    checks = cast(list[str], facts["checks"])
    assert any("the check the task shows you passed" in item for item in checks)
    assert not any("HIDDEN-CHECK" in item for item in [*checks, str(facts["reason"])])


def test_the_request_carries_the_visible_check_and_not_the_hidden_one(tmp_path: Path) -> None:
    """The executor's request is the model's whole view of the task: one check, never both."""
    project, box, _script, _task = probe(tmp_path, ANSWER)
    task = augustus.Task(
        path=tmp_path / "t.toml",
        id="t-1",
        goal="g",
        kind="bugfix",
        paths=("src/**",),
        expected="e",
        change=None,
        forbidden=(),
        budget={},
        self_check=("python", "-c", "print('visible')"),
        repro=("python", "-c", "print('hidden')"),
    )
    directory = box.root / "tabularium" / "t-1" / "proposal-1"
    directory.mkdir(parents=True)
    request = executor._request(task, box, directory, 1, "m", 60, 2.0)
    seen = cast(dict[str, Any], request["task"])
    assert seen["self_check"] == ["python", "-c", "print('visible')"]
    assert "repro" not in seen

    prompt = deepseek._prompt(seen, project)
    assert "THE CHECK YOU CAN SEE" in prompt.text
    assert "print('visible')" in prompt.text
    assert "print('hidden')" not in prompt.text
    assert "you do not see it" in prompt.text


def test_a_narrow_scope_gets_the_layout_not_the_file_inventory(tmp_path: Path) -> None:
    """The names of files the task may not touch are the box's chatter, not the model's work.

    The pilot measured that inventory at ~1 200 tokens — more than the task and its check
    together — while the model's answers started looping just above 9 000. A scope that covers
    the project still gets the list: there the names are the work.
    """
    project = tmp_path / "clickish"
    (project / "src" / "pkg").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "docs").mkdir()
    (project / "src" / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / "tests" / "test_app.py").write_text("pass\n", encoding="utf-8")
    (project / "docs" / "index.md").write_text("# docs\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)

    narrow = deepseek._prompt({"id": "t", "paths": ["src/pkg/app.py"]}, project)
    assert "THE PROJECT: 3 files under docs, src, tests" in narrow.text
    assert "FILES IN THE PROJECT" not in narrow.text
    assert "test_app.py" not in narrow.text

    whole = deepseek._prompt({"id": "t", "paths": ["**"]}, project)
    assert "FILES IN THE PROJECT (relative to its root):" in whole.text
    assert "src/pkg/app.py" in whole.text


def test_the_box_sizes_the_answer_by_the_shape_of_the_change(tmp_path: Path) -> None:
    """A patch gets a patch's room, a whole file gets more: one cap for all is waste or a cut.

    The shape is declared by the task and priced by the box, like the budget: a task may ask
    for a smaller shape, never for a bigger one than the box allows for it.
    """
    path = tmp_path / "executor.toml"
    path.write_text(
        'schema_version = 1\ncommand = "/bin/true"\n'
        "[shapes.patch]\nmax_answer_tokens = 4096\nmax_cents = 2\n"
        "[shapes.file]\nmax_answer_tokens = 16384\nmax_cents = 5\n",
        encoding="utf-8",
    )
    config = executor.read_config(path)
    patch, reason = executor.shape_for(config, "patch")
    assert patch is not None and patch.max_answer_tokens == 4096 and patch.max_cents == 2
    assert "4096 answer tokens" in reason
    assert executor.shape_for(config, None) == (
        None,
        "the task declared no shape: the box's own cap applies",
    )
    missing, why = executor.shape_for(config, "multi")
    assert missing is None and "declares no limits for it" in why

    for text, match in (
        ('schema_version = 1\ncommand = "/bin/true"\n[shapes.huge]\nmax_answer_tokens = 1\n',
         "is not one of"),
        ('schema_version = 1\ncommand = "/bin/true"\n[shapes.patch]\nmax_cents = 2\n',
         "max_answer_tokens"),
        ('schema_version = 1\ncommand = "/bin/true"\n'
         "[shapes.patch]\nmax_answer_tokens = 1\nprice = 2\n", "unknown key"),
    ):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(boxes.HarnessError, match=match):
            executor.read_config(path)


def test_the_request_carries_the_answer_room_and_the_adapter_uses_it(tmp_path: Path) -> None:
    project, box, _script, _task = probe(tmp_path, ANSWER)
    task = augustus.Task(
        path=tmp_path / "t.toml",
        id="t-1",
        goal="g",
        kind="bugfix",
        paths=("src/**",),
        expected="e",
        change=None,
        forbidden=(),
        budget={},
        shape="file",
    )
    directory = box.root / "tabularium" / "t-1" / "proposal-1"
    directory.mkdir(parents=True)
    shape = executor.Shape("file", 16384, 5.0)
    request = executor._request(task, box, directory, 1, "m", 60, 2.0, shape=shape)
    seen = cast(dict[str, Any], request["task"])
    assert seen["shape"] == "file"
    limits = cast(dict[str, Any], request["limits"])
    assert limits["max_answer_tokens"] == 16384
    assert deepseek._max_tokens(request) == 16384
    assert deepseek._max_tokens({"limits": {"max_answer_tokens": None}}) == 4096

    prompt = deepseek._prompt(seen, project)
    assert "THE SHAPE OF THIS CHANGE: file" in prompt.text
    assert "one file may be rewritten" in prompt.text
    assert "ERROR: <what the change really needs>" in prompt.text


def test_a_task_declaring_a_shape_the_box_does_not_price_is_refused_before_the_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unpriced shape is a wrong plan, not a default: the model is not asked and not paid."""
    _project, box, _script, task = probe(tmp_path, ANSWER)
    text = task.read_text(encoding="utf-8")
    task.write_text(text.replace("kind = ", 'shape = "multi"\nkind = '), encoding="utf-8")
    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "declares no limits for it" in out
    assert not list(box.root.glob("tabularium/t-1/proposal-*"))

    (box.root / "persona" / "executor.toml").write_text(
        (box.root / "persona" / "executor.toml").read_text(encoding="utf-8")
        + '\n[shapes.multi]\nmax_answer_tokens = 32768\nmax_cents = 6\n',
        encoding="utf-8",
    )
    assert dispatch(box, task) == 0, "the fake change passes its checks: the run closes"
    out = capsys.readouterr().out
    assert "shape      multi — 32768 answer tokens, 6 cents" in out


BIG_ANSWER = """\
request = json.load(sys.stdin)
print(json.dumps({
    "patch": (
        "--- a/src/app.py\\n+++ b/src/app.py\\n"
        "@@ -1,3 +1,3 @@\\n-one\\n-two\\n-three\\n+one!\\n+two!\\n+three!\\n"
    ),
    "model": "fake-model",
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost_cents": 0.5},
}))
"""


def _longer_project(box: boxes.Box) -> None:
    (box.project / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(box.project), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qam", "longer"],
        check=True,
    )


def test_an_answer_out_of_shape_is_not_applied_and_the_retry_is_told(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shape is a control on the answer, not a wish: a patch that rewrites the file is out.

    A patch-sized task answered with a whole-file restatement is the pilot's c-d6/c-d7 failure
    (33 copies of one hunk, cut at the cap). Applying such an answer would teach the box to
    accept the wrong scheme, so it is refused with both numbers, and the retry gets the fact.
    """
    _project, box, _script, task = probe(tmp_path, BIG_ANSWER)
    _longer_project(box)
    (box.root / "persona" / "executor.toml").write_text(
        (box.root / "persona" / "executor.toml").read_text(encoding="utf-8")
        + "\n[shapes.patch]\nmax_answer_tokens = 4096\nmax_changed_lines = 1\nmax_files = 1\n",
        encoding="utf-8",
    )
    text = task.read_text(encoding="utf-8")
    task.write_text(text.replace("kind = ", 'shape = "patch"\nkind = '), encoding="utf-8")

    assert dispatch(box, task) == 1
    out = capsys.readouterr().out
    assert "out of shape: patch" in out
    assert "6 changed line(s) for at most 1" in out
    assert (box.project / "src" / "app.py").read_text() == "one\ntwo\nthree\n"

    record = json.loads(next((box.root / "tabularium" / "t-1").glob("dispatch-*.json")).read_text())
    assert record["shape"]["max_changed_lines"] == 1
    facts = augustus.previous_failure(record)
    assert facts is not None and "out of shape" in str(facts["reason"])


def test_an_answer_inside_the_shape_goes_through_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same answer, a shape that allows it: the gate decides, and the run closes."""
    _project, box, _script, task = probe(tmp_path, BIG_ANSWER)
    _longer_project(box)
    (box.root / "persona" / "executor.toml").write_text(
        (box.root / "persona" / "executor.toml").read_text(encoding="utf-8")
        + "\n[shapes.file]\nmax_answer_tokens = 16384\nmax_changed_lines = 100\nmax_files = 1\n",
        encoding="utf-8",
    )
    text = task.read_text(encoding="utf-8")
    task.write_text(text.replace("kind = ", 'shape = "file"\nkind = '), encoding="utf-8")
    assert dispatch(box, task) == 0
    out = capsys.readouterr().out
    assert "shape      file — 16384 answer tokens" in out
    assert (box.project / "src" / "app.py").read_text() == "one!\ntwo!\nthree!\n"
