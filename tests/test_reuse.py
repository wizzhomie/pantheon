"""Reuse: a settled verdict about an unchanged tree is not computed twice.

The cases that matter are the ones where reuse could lie: the tree changed, the policy
changed, the earlier verdict was not settled, or the earlier run is old enough that the world
may have moved under it. Each of them must run the checks again, and the record must say when
an answer was reused and where it came from.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour
from harness.cli import main

PASSING = """\
name = "probe"

[[checks]]
id = "smoke"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that always passes"
"""

FAILING = """\
name = "probe"

[[checks]]
id = "broken"
argv = ["python", "-c", "raise SystemExit(1)"]
purpose = "a check that always fails"
"""

MISSING_TOOL = """\
name = "probe"

[[checks]]
id = "needs-a-tool"
argv = ["python", "-c", "print('ok')"]
requires = "a-tool-that-is-not-installed"
purpose = "a required check whose tool is absent"
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path, policy: str = PASSING, name: str = "probe") -> Path:
    project = tmp_path / name
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(policy, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_box(tmp_path: Path, policy: str = PASSING) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path, policy)
    return project, boxes.new_box(tmp_path / "castra", project)


def verify(
    box: boxes.Box, capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, dict[str, object]]:
    """The exit code and the answer: a hold exits 1 and an inconclusive run exits 2."""
    code = main(["--castra", str(box.root.parent), "verify", box.name, "--json", *extra])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, dict)
    return code, {str(key): value for key, value in payload.items()}


def records(box: boxes.Box) -> list[Path]:
    return sorted((box.root / "tabularium").rglob(contour.RECORD_FILE))


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
def test_a_settled_verdict_is_reused_when_nothing_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    _, first = verify(box, capsys)
    assert first["verdict"] == "pass" and first["reused_from"] is None
    assert (project / "src" / "app.py").read_text() == "value = 1\n"
    _, second = verify(box, capsys)
    assert second["verdict"] == "pass"
    assert second["reused_from"] is not None
    assert second["report"] == first["report"], "the answer is the earlier report, not a new one"
    reused = json.loads(Path(str(second["report"])).read_text())
    assert reused["verdict"]["status"] == "pass"
    earlier = Path(str(second["reused_from"]))
    assert (earlier / contour.RECORD_FILE).is_file()


@requires_trajan
def test_a_changed_tree_runs_the_checks_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    _, first = verify(box, capsys)
    (project / "src" / "app.py").write_text("value = 1\n# touched\n", encoding="utf-8")
    _, second = verify(box, capsys)
    assert second["reused_from"] is None
    assert second["report"] != first["report"]


@requires_trajan
def test_a_project_inside_a_repository_digests_its_own_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A project in a subdirectory must digest its files, not a list of missing paths.

    Found on the demo box: ``ls-files`` named paths relative to the project while the files
    were read from the repository root, so every path digested as absent, an edited model left
    the digest unchanged, and the verdict about the old tree was reused for the new one.
    """
    project = tmp_path / "repo" / "sub"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text(PASSING, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    box = boxes.new_box(tmp_path / "castra", project)
    _, first = verify(box, capsys)
    assert first["reused_from"] is None
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    _, second = verify(box, capsys)
    assert second["reused_from"] is None, "the tree changed: the old verdict is not the answer"
    _, third = verify(box, capsys)
    assert third["reused_from"] is not None, "the same tree twice is still reused"


@requires_trajan
def test_a_commit_does_not_invalidate_reuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    verify(box, capsys)
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "empty",
        "--allow-empty")
    _, second = verify(box, capsys)
    assert second["reused_from"] is not None, "a commit moves the bytes nowhere"


@requires_trajan
def test_an_untracked_file_changes_the_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    verify(box, capsys)
    (project / "src" / "new.py").write_text("print('new')\n", encoding="utf-8")
    _, second = verify(box, capsys)
    assert second["reused_from"] is None


@requires_trajan
def test_a_changed_policy_runs_the_checks_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    verify(box, capsys)
    (project / ".trajan" / "policy.toml").write_text(
        PASSING + '\n[[checks]]\nid = "second"\nargv = ["python", "-c", "print(1)"]\n'
        'purpose = "a second check"\n',
        encoding="utf-8",
    )
    _, second = verify(box, capsys)
    assert second["reused_from"] is None


@requires_trajan
def test_a_hold_is_not_reused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project, box = make_box(tmp_path, FAILING)
    _, first = verify(box, capsys)
    assert first["verdict"] == "hold"
    _, second = verify(box, capsys)
    assert second["reused_from"] is None, "a hold is what a person fixes and looks at again"
    assert second["report"] != first["report"]


@requires_trajan
def test_an_inconclusive_verdict_is_not_reused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path, MISSING_TOOL)
    _, first = verify(box, capsys)
    assert first["verdict"] == "inconclusive"
    _, second = verify(box, capsys)
    assert second["reused_from"] is None, "the environment may have changed, not the tree"


@requires_trajan
def test_an_old_record_is_not_reused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project, box = make_box(tmp_path)
    verify(box, capsys)
    record = records(box)[-1]
    data = json.loads(record.read_text(encoding="utf-8"))
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    data["at"] = old
    record.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _, second = verify(box, capsys)
    assert second["reused_from"] is None, "a day-old verdict is a claim about an older world"


@requires_trajan
def test_a_dispatch_reuses_a_verdict_about_the_same_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    verify(box, capsys)
    (box.root / "mandata").mkdir(exist_ok=True)
    task = box.root / "mandata" / "t-docs.toml"
    task.write_text(
        'schema_version = 1\nid = "t-docs"\ngoal = "look at the project"\nkind = "docs"\n'
        'paths = ["src/**"]\nexpected = "the checks still pass"\n',
        encoding="utf-8",
    )
    code = main(
        ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "state      closed" in out and "reused from" in out
