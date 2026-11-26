"""The contract against the archive: a reversed decision stops before anything is paid.

A repair touches old decisions all the time, and that is not a contradiction. The difference is
the reader's to name and the archive's to prove: the answer only counts when it cites an address
the archive holds and a clause that is really there. These tests hold both halves, and one run
where the second task would reverse the first task's decision.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness import archivum, contour, contradiction
from harness import box as boxes
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "value"
argv = ["grep", "-qx", "value = 2", "src/app.py"]
purpose = "the value is two"
"""

FIRST = """\
schema_version = 1
id = "t-1"
goal = "make the value two"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is two"

[contract]
symptom = "the value is one"
visible = "src/app.py holds value = 2"
boundaries = "nothing outside src/"
missing = ""

[budget]
max_cents = 5
max_seconds = 60
"""

SECOND = """\
schema_version = 1
id = "t-2"
goal = "make the value three"
kind = "bugfix"
paths = ["src/**"]
expected = "the value is three"

[contract]
symptom = "the value is two"
visible = "src/app.py holds value = 3"
boundaries = "src/app.py only"
missing = ""

[budget]
max_cents = 5
max_seconds = 60
"""

GOOD = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""

THIRD = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-value = 2
+value = 3
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


def write_executor(tmp_path: Path, patch: str) -> Path:
    script = tmp_path / "answers.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        'with open(os.environ["FAKE_EXECUTOR_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write("called\\n")\n'
        f'print(json.dumps({{"patch": {json.dumps(patch)}, "model": "fake-model", '
        '"usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_cents": 0.1}}))\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_validator(tmp_path: Path, *, contradict: bool = True) -> Path:
    """A reader that cites what it was given: the address and the first clause in the history."""
    script = tmp_path / "validator.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, re, sys\n"
        "request = json.load(sys.stdin)\n"
        "prompt = request['messages'][1]['content']\n"
        "address = re.search(r'^- (\\S+)', prompt, re.M)\n"
        "old = re.search(r'^  ожидаемое: (.+)$', prompt, re.M)\n"
        f"contradict = {contradict!r}\n"
        "answer = None\n"
        "if contradict and address and old:\n"
        "    answer = {'address': address.group(1), 'old': old.group(1).strip(),\n"
        "              'new': 'the value is three', 'why': 'the new task asks for the opposite'}\n"
        "print(json.dumps({'text': json.dumps({'contradiction': answer}), 'cost_cents': 0.3}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def write_profile(box: boxes.Box, command: Path) -> None:
    (box.root / "persona" / "validator.toml").write_text(
        f'schema_version = 1\nenabled = true\ncommand = "{command}"\nmax_cents = 1\n',
        encoding="utf-8",
    )


def calls(tmp_path: Path) -> int:
    log = tmp_path / "executor.log"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


def entries(box: boxes.Box) -> tuple[archivum.Entry, ...]:
    return archivum.entries(box.root)


def test_a_profile_that_is_absent_means_no_comparison(tmp_path: Path) -> None:
    config = contradiction.read_config(tmp_path / "persona" / "validator.toml")
    assert not config.declared and not config.enabled


def test_a_profile_with_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "validator.toml"
    path.write_text("schema_version = 1\nenabled = true\nmode = \"fast\"\n", encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="unknown key"):
        contradiction.read_config(path)


def test_nothing_to_compare_costs_nothing(tmp_path: Path) -> None:
    compared = contradiction.compare(
        tmp_path,
        ("src/**",),
        lambda history: "unused",
        ask=lambda prompt: pytest.fail("a reader was asked with no history"),
    )
    assert compared.history == () and compared.skipped
    assert "no closed change" in str(compared.skipped)


def test_history_without_a_declared_reader_is_named_not_guessed(tmp_path: Path) -> None:
    box = boxes.new_box(tmp_path / "castra", make_project(tmp_path))
    _archive_one(box)
    compared = contradiction.compare(
        box.root, ("src/**",), lambda history: "unused", ask=None
    )
    assert compared.history and not compared.checked
    assert "not compared" in contradiction.note(compared)


def test_a_citation_that_the_archive_holds_is_an_escalation(tmp_path: Path) -> None:
    box = boxes.new_box(tmp_path / "castra", make_project(tmp_path))
    entry = _archive_one(box)

    def ask(prompt: str) -> tuple[str, float | None]:
        assert entry.address in prompt, "the reader is given the addresses, not asked to invent"
        answer = {
            "contradiction": {
                "address": entry.address,
                "old": "the value is two",
                "new": "the value is three",
                "why": "the task reverses the decision",
            }
        }
        return json.dumps(answer), 0.3

    compared = contradiction.compare(
        box.root,
        ("src/**",),
        lambda history: contradiction.prompt("make the value two", "the value is two", {}, history),
        ask=ask,
    )
    assert compared.escalated and compared.citation is not None
    sentence = contradiction.reason(compared)
    assert sentence is not None and entry.address in sentence and "value is three" in sentence
    assert compared.cost_cents == pytest.approx(0.3)


def test_a_citation_the_archive_does_not_hold_counts_as_nothing(tmp_path: Path) -> None:
    box = boxes.new_box(tmp_path / "castra", make_project(tmp_path))
    entry = _archive_one(box)

    def wrong_address(prompt: str) -> tuple[str, float | None]:
        answer = {
            "contradiction": {
                "address": "t-9/20260101T000000Z",
                "old": "the value is two",
                "new": "the value is three",
                "why": "invented",
            }
        }
        return json.dumps(answer), None

    compared = contradiction.compare(
        box.root, ("src/**",), lambda history: "prompt", ask=wrong_address
    )
    assert not compared.escalated
    assert compared.error and "not in the history" in compared.error

    def wrong_clause(prompt: str) -> tuple[str, float | None]:
        answer = {
            "contradiction": {
                "address": entry.address,
                "old": "the value is four",
                "new": "the value is three",
                "why": "not a clause of that entry",
            }
        }
        return json.dumps(answer), None

    compared = contradiction.compare(
        box.root, ("src/**",), lambda history: "prompt", ask=wrong_clause
    )
    assert not compared.escalated and compared.error is not None
    assert "not there" in compared.error


def test_a_silent_reader_is_a_named_absence(tmp_path: Path) -> None:
    box = boxes.new_box(tmp_path / "castra", make_project(tmp_path))
    _archive_one(box)
    compared = contradiction.compare(
        box.root, ("src/**",), lambda history: "prompt", ask=lambda prompt: ("{}", None)
    )
    assert compared.checked and not compared.escalated
    assert "no contradiction named" in contradiction.note(compared)


def test_a_reader_that_fails_does_not_stop_the_task(tmp_path: Path) -> None:
    box = boxes.new_box(tmp_path / "castra", make_project(tmp_path))
    _archive_one(box)

    def broken(prompt: str) -> tuple[str, float | None]:
        raise boxes.HarnessError("the container is down")

    compared = contradiction.compare(box.root, ("src/**",), lambda history: "prompt", ask=broken)
    assert compared.error and "container is down" in compared.error
    assert "task goes on" in contradiction.note(compared)


def _archive_one(box: boxes.Box) -> archivum.Entry:
    """One closed change in the archive, written the way a dispatch writes it."""
    attempt = box.root / "tabularium" / "t-1" / "1"
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "change.diff").write_text(GOOD, encoding="utf-8")
    (box.root / "tabularium" / "t-1" / "task.json").write_text(
        FIRST, encoding="utf-8"
    )
    record: dict[str, object] = {
        "task": "t-1",
        "kind": "bugfix",
        "role": "executor",
        "paths": ["src/**"],
        "state": "closed",
        "contract": {
            "symptom": "the value is one",
            "visible": "src/app.py holds value = 2",
            "boundaries": "nothing outside src/",
            "missing": None,
        },
        "self_check": {"status": "passed", "exit_code": 0, "tail": "ok"},
        "verify": {"status": "pass", "report": "", "exit_code": 0},
        "attempt": {"directory": str(attempt), "paths": ["src/app.py"]},
        "commit": {"status": "made", "sha": "abc123", "subject": "bugfix: make the value two"},
        "executor": {"usage": {"cost_cents": 0.4}},
        "timings": {"total": 3.0},
        "notes": [],
    }
    archivum.write(box.root, record, datetime(2026, 11, 26, 8, 0, tzinfo=UTC))
    found = archivum.entries(box.root)
    assert found, "the archive kept nothing: the test has nothing to compare against"
    return found[0]


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
def test_a_task_that_reverses_a_closed_decision_is_refused_before_it_is_paid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_EXECUTOR_LOG", str(tmp_path / "executor.log"))
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{write_executor(tmp_path, GOOD)}"\nmax_seconds = 60\n',
        encoding="utf-8",
    )
    first = tmp_path / "t-1.toml"
    first.write_text(FIRST, encoding="utf-8")
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(first)]) == 0
    capsys.readouterr()
    assert calls(tmp_path) == 1 and entries(box), "the first task closed and was archived"
    write_profile(box, write_validator(tmp_path))
    second = tmp_path / "t-2.toml"
    second.write_text(SECOND, encoding="utf-8")
    refused = main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(second)])
    assert refused == 1
    out = capsys.readouterr().out
    assert "contradicts the decision kept at" in out
    assert "state      refused" in out
    assert calls(tmp_path) == 1, "a reversed decision is not paid for"
    records = sorted((box.root / "tabularium" / "t-2").glob("dispatch-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["state"] == "refused"
    assert any("contradicts the decision" in note for note in record["notes"])
    events = [
        json.loads(line)
        for path in (box.root / "fasti").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(item["type"] == "contract.contradiction" for item in events)


@requires_trajan
def test_a_task_that_touches_history_without_contradicting_it_goes_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_EXECUTOR_LOG", str(tmp_path / "executor.log"))
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    (box.root / "persona" / "executor.toml").write_text(
        f'schema_version = 1\ncommand = "{write_executor(tmp_path, GOOD)}"\nmax_seconds = 60\n',
        encoding="utf-8",
    )
    first = tmp_path / "t-1.toml"
    first.write_text(FIRST, encoding="utf-8")
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(first)]) == 0
    capsys.readouterr()
    write_profile(box, write_validator(tmp_path, contradict=False))
    copy = tmp_path / "t-3.toml"
    copy.write_text(SECOND.replace('id = "t-2"', 'id = "t-3"'), encoding="utf-8")
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(copy)]) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out
    assert "the contract was compared with the archive" in out
