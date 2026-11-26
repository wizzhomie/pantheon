"""Augustus: a task goes through its contours, and the record says what actually ran.

The interesting cases are the ones where "nothing happened" must not look like "all good": a
task without a scope is refused before anything runs, a task without a change says so in the
outbox, and a broken architecture stops the change before the write gate.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness import augustus, aurelius, contour
from harness import box as boxes
from harness.cli import main

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
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path, policy: str = PASSING) -> Path:
    project = tmp_path / "probe"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(policy, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_patch(tmp_path: Path, project: Path, content: str) -> Path:
    (project / "src" / "app.py").write_text(content, encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def write_task(tmp_path: Path, text: str = TASK, name: str = "t-1.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    return project, boxes.new_box(tmp_path / "castra", project)


def trajan_runnable() -> bool:
    try:
        root = contour.trajan_root()
    except boxes.HarnessError:
        return False
    python = contour.trajan_python(root)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    done = subprocess.run(
        [str(python), "-m", "trajan", "--help"], capture_output=True, check=False, env=env
    )
    return done.returncode == 0


requires_trajan = pytest.mark.skipif(not trajan_runnable(), reason="trajan cannot run here")


def test_a_task_is_read_strictly(tmp_path: Path) -> None:
    with pytest.raises(boxes.HarnessError, match="no task"):
        augustus.read_task(tmp_path / "none.toml")
    with pytest.raises(boxes.HarnessError, match="unknown key"):
        augustus.read_task(write_task(tmp_path, TASK + 'surprise = "x"\n'))
    with pytest.raises(boxes.HarnessError, match="budget"):
        augustus.read_task(write_task(tmp_path, TASK + "\n[budget]\nmax_hours = 1\n"))
    with pytest.raises(boxes.HarnessError, match="not a path"):
        augustus.read_task(write_task(tmp_path, TASK.replace('id = "t-1"', 'id = "../x"')))
    task = augustus.read_task(write_task(tmp_path, TASK + 'records = ["t-old/20261124T120000Z"]\n'))
    assert task.records == ("t-old/20261124T120000Z",)
    with pytest.raises(boxes.HarnessError, match="not a record address"):
        augustus.read_task(write_task(tmp_path, TASK + 'records = ["t-old"]\n'))


def test_a_task_contract_is_read_and_frozen_with_the_task(tmp_path: Path) -> None:
    text = TASK + (
        "\n[contract]\n"
        'symptom = "the table says 042, the file has 043"\n'
        'visible = "docs/README.md, the 05-adr row"\n'
        'boundaries = "do not edit 05-adr.md"\n'
        'missing = "none"\n'
    )
    task = augustus.read_task(write_task(tmp_path, text))
    assert task.contract is not None
    assert task.contract.symptom == "the table says 042, the file has 043"
    assert task.contract.boundaries == "do not edit 05-adr.md"
    frozen = augustus.task_text(task)
    again = augustus.read_task(write_task(tmp_path, frozen, name="frozen.toml"))
    assert again.contract == task.contract
    with pytest.raises(boxes.HarnessError, match="contract does not take"):
        augustus.read_task(
            write_task(tmp_path, TASK + '\n[contract]\nwrong = "x"\n', name="a.toml")
        )
    with pytest.raises(boxes.HarnessError, match="contract is empty"):
        augustus.read_task(write_task(tmp_path, TASK + "\n[contract]\n", name="b.toml"))
    with pytest.raises(boxes.HarnessError, match="must be a string"):
        augustus.read_task(
            write_task(tmp_path, TASK + "\n[contract]\nsymptom = 3\n", name="c.toml")
        )


def test_the_routing_table_is_data_and_a_kind_without_a_route_is_a_refusal(
    tmp_path: Path,
) -> None:
    routes = augustus.read_routes(None)
    task = augustus.read_task(write_task(tmp_path))
    assert routes[task.kind or ""] == ("run", "verify")
    custom = write_task(tmp_path, '[route]\ndocs = ["verify"]\n', name="routes.toml")
    assert augustus.read_routes(custom) == {"docs": ("verify",)}
    unknown = dataclasses.replace(task, kind="refactoring")
    assert augustus.refusal(unknown, routes) is not None


def test_a_task_without_a_scope_is_refused_before_anything_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    task = write_task(tmp_path, TASK.replace('paths = ["src/**"]\n', ""))
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 1
    out = capsys.readouterr().out
    assert "state      refused" in out and "nothing is in scope" in out
    assert not list((box.root / "acta").glob("*.md"))
    events = (box.root / "fasti" / f"{_today()}.jsonl").read_text()
    assert "task.refused" in events
    assert (project / "src" / "app.py").read_text() == "value = 1\n"


@requires_trajan
def test_a_task_inside_its_scope_is_closed_with_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project, "value = 2\n")
    task = write_task(tmp_path, TASK + f'change = "{patch}"\n')
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out and "verify     pass" in out
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    outbox = next((box.root / "acta").glob("*.md"))
    text = outbox.read_text()
    assert text.splitlines()[0].endswith("closed")
    assert "## what ran" in text and "verification: pass" in text
    events = (box.root / "fasti" / f"{_today()}.jsonl").read_text()
    assert "task.finished" in events and "acta.written" in events
    assert (box.root / "mandata" / "t-1.contract.toml").is_file()


@requires_trajan
def test_a_change_outside_the_scope_returns_the_task_before_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    (project / "README.md").write_text("new\n", encoding="utf-8")
    git(project, "add", "-A")
    patch = tmp_path / "outside.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    task = write_task(tmp_path, TASK + f'change = "{patch}"\n')
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 1
    out = capsys.readouterr().out
    assert "state      returned" in out and "outside-scope" in out
    assert not list((box.root / "tabularium" / "t-1").glob("verify-*"))


@requires_trajan
def test_a_docs_task_does_not_reach_the_write_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "docs"').replace(
            'paths = ["src/**"]', 'paths = ["docs/**"]'
        ),
    )
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "attempt    not run" in out
    outbox = next((box.root / "acta").glob("*.md"))
    assert "write gate: not run" in outbox.read_text()


@requires_trajan
def test_a_broken_architecture_stops_the_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project, box = make_box(tmp_path)
    (project / ".pantheon").mkdir()
    (project / ".pantheon" / "architecture.toml").write_text(
        'schema_version = 1\n[project]\nroot_packages = ["probe"]\n\n'
        '[[layer]]\nname = "only"\nmodules = ["probe"]\n',
        encoding="utf-8",
    )
    tool = tmp_path / "lint-imports"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('fake 1')\n"
        "    sys.exit(0)\n"
        "print('Contracts: 0 kept, 1 broken.')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    monkeypatch.setenv(aurelius.TOOL_ENV, str(tool))
    task = write_task(tmp_path, TASK.replace('kind = "bugfix"', 'kind = "feature"'))
    assert main(
        [
            "--castra",
            str(box.root.parent),
            "dispatch",
            "probe",
            "--task",
            str(task),
            "--tool",
            str(tool),
        ]
    ) == 1
    out = capsys.readouterr().out
    assert "arch       broken" in out and "state      returned" in out
    assert "attempt    not run" in out


@requires_trajan
def test_a_task_without_a_change_says_so_in_the_outbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(tmp_path, TASK.replace('kind = "bugfix"', 'kind = "feature"'))
    code = main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)])
    assert code == 0
    out = capsys.readouterr().out
    assert "attempt    not run" in out
    outbox = next((box.root / "acta").glob("*.md"))
    text = outbox.read_text()
    assert "the box declares no executor" in text
    assert "cost: unknown" not in text and "no executor was asked" in text


def test_the_cli_answers_a_task_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(tmp_path, TASK.replace('paths = ["src/**"]\n', ""))
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task),
                 "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert (payload["state"], payload["exit_code"]) == ("refused", 1)
    assert payload["stages"] == []


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@requires_trajan
def test_status_shows_the_tasks_newest_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(tmp_path, TASK.replace('kind = "bugfix"', 'kind = "docs"'))
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    capsys.readouterr()
    assert main(["--castra", str(box.root.parent), "status", "probe"]) == 0
    out = capsys.readouterr().out
    assert "task       t-1  closed" in out
    assert main(["--castra", str(box.root.parent), "status", "probe", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tasks"][0]["id"] == "t-1"
    assert payload["tasks"][0]["state"] == "closed"


def test_no_dispatched_tasks_is_said_out_loud(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    assert main(["--castra", str(box.root.parent), "status", "probe"]) == 0
    assert "tasks      none dispatched yet" in capsys.readouterr().out


@requires_trajan
def test_a_declared_change_is_gated_even_when_the_route_has_no_write_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A docs task carrying a patch must not close on a tree the patch never touched."""
    project, box = make_box(tmp_path)
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("one\n", encoding="utf-8")
    git(project, "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "docs")
    (project / "docs" / "note.md").write_text("two\n", encoding="utf-8")
    git(project, "add", "-A")
    patch = tmp_path / "docs.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "docs"')
        .replace('paths = ["src/**"]', 'paths = ["docs/**"]')
        + f'change = "{patch}"\n',
    )
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "stages     run -> verify" in out
    assert "attempt    applied" in out
    assert (project / "docs" / "note.md").read_text() == "two\n"


@requires_trajan
def test_a_gated_change_outside_the_docs_scope_returns_the_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("one\n", encoding="utf-8")
    git(project, "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "docs")
    (project / "outside.md").write_text("no\n", encoding="utf-8")
    git(project, "add", "-A")
    patch = tmp_path / "outside2.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    (project / "outside.md").unlink()
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "docs"')
        .replace('paths = ["src/**"]', 'paths = ["docs/**"]')
        + f'change = "{patch}"\n',
    )
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 1
    out = capsys.readouterr().out
    assert "state      returned" in out and "outside-scope" in out
    assert not list((box.root / "tabularium" / "t-1").glob("verify-*"))


@requires_trajan
def test_a_declared_path_decides_whether_the_change_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("one\n", encoding="utf-8")
    git(project, "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "docs")
    (project / "docs" / "note.md").write_text("two\n", encoding="utf-8")
    git(project, "add", "-A")
    patch = tmp_path / "docs.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    (box.root / "persona").mkdir(parents=True, exist_ok=True)
    (box.root / "persona" / "reviewer.toml").write_text(
        "schema_version = 1\nenabled = true\nmax_cents = 1\nwhen_paths = [\"src/**\"]\n",
        encoding="utf-8",
    )
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "docs"')
        .replace('paths = ["src/**"]', 'paths = ["docs/**"]')
        + f'change = "{patch}"\n',
    )
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "not read by declaration" in out
    assert "the change touches no path this box reads (declared: src/**)" in out

    record = json.loads(
        next((box.root / "tabularium" / "t-1").glob("dispatch-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert record["verify"]["review"] is None
    assert record["review_cap"] is None
    assert "src/**" in record["review_skipped"]
    outbox = next((box.root / "acta").glob("*.md")).read_text(encoding="utf-8")
    assert "not read by declaration" in outbox
    assert "not read by declaration" in "\n".join(
        line for line in outbox.splitlines() if line.startswith("- ")
    )


@requires_trajan
def test_a_dispatch_records_where_its_time_went(tmp_path: Path) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project, "value = 2\n")
    task = write_task(tmp_path, TASK + f'change = "{patch}"\n')
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    record = json.loads(
        next((box.root / "tabularium" / "t-1").glob("dispatch-*.json")).read_text(
            encoding="utf-8"
        )
    )
    timings = record["timings"]
    assert timings["total"] >= 0.0 and "verify" in timings and "other" in timings
    assert timings["verify"] >= 0.0, "the checks report their own seconds"
    outbox = next((box.root / "acta").glob("*.md")).read_text(encoding="utf-8")
    assert "## time" in outbox and "total:" in outbox
    attempt = json.loads(
        (box.root / "tabularium" / "t-1" / "1" / "attempt.json").read_text(encoding="utf-8")
    )
    assert "seconds" in attempt, "the gate records what its own work took"


def test_a_hotfix_refuses_a_pattern_instead_of_exact_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(tmp_path, TASK.replace('kind = "bugfix"', 'kind = "hotfix"'))
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 1
    out = capsys.readouterr().out
    assert "state      refused" in out and "exact files" in out


@requires_trajan
def test_a_hotfix_with_exact_files_is_closed_like_any_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project, "value = 2\n")
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "hotfix"')
        .replace('paths = ["src/**"]', 'paths = ["src/app.py"]')
        + f'change = "{patch}"\n',
    )
    assert main(["--castra", str(box.root.parent), "dispatch", "probe", "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "state      closed" in out and "time       " in out
