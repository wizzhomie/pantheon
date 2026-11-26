"""The check against its own change: reverse one piece and see whether it notices.

A green check proves the result looks right; it does not prove the check could have turned red.
These tests hold the kinds of pieces (a text block, a config value, a new file, a deleted file),
the pieces that must not be tested (code, YAML, a rename), the lie that is refused (a revert that
breaks the format instead of the meaning) and the guard: a check that is already red on the
change cannot notice anything.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour, sensitivity
from harness.cli import main

CHECK = (
    "grep -q 'Подробности: MARKER' docs/guide.md && grep -q 'value = 2' conf/app.toml "
    "&& test -f docs/notes.md"
)

TASK = """\
schema_version = 1
id = "t-1"
goal = "write the rule down"
kind = "docs"
paths = ["docs/**", "conf/**", "src/**"]
expected = "the guide carries the rule and the marker"

[contract]
symptom = "the rule is not written"
visible = "docs/guide.md carries the marker"
boundaries = "nothing outside docs, conf and src"
missing = ""

[budget]
max_cents = 2
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    (project / "docs").mkdir(parents=True)
    (project / "conf").mkdir()
    (project / "src").mkdir()
    (project / "docs" / "guide.md").write_text(
        "# Guide\n\n## Rule\n\nOld line.\nKeep this.\n", encoding="utf-8"
    )
    (project / "conf" / "app.toml").write_text('value = 1\n', encoding="utf-8")
    (project / "conf" / "pipe.yaml").write_text("name: probe\n", encoding="utf-8")
    (project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text('name = "probe"\n', encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_patch(tmp_path: Path, project: Path) -> Path:
    """One change of every kind, so the report has something to say about each."""
    (project / "docs" / "guide.md").write_text(
        "# Guide\n\n## Rule\n\nOld line.\nПравило: сроки.\nKeep this.\nПодробности: MARKER.\n",
        encoding="utf-8",
    )
    (project / "conf" / "app.toml").write_text("value = 2\n", encoding="utf-8")
    (project / "conf" / "pipe.yaml").write_text("name: probe\nmode: fast\n", encoding="utf-8")
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    (project / "docs" / "notes.md").write_text("Marker lives here.\n", encoding="utf-8")
    git(project, "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q", "--hard", "HEAD")
    return patch


def write_task(tmp_path: Path, patch: Path, check: str = CHECK) -> Path:
    extra = f'change = "{patch}"\nrepro = ["sh", "-c", "{check}"]\n'
    task = tmp_path / "t-1.toml"
    task.write_text(TASK.replace("[contract]", extra + "\n[contract]"), encoding="utf-8")
    return task


def run(
    project: Path, patch: Path, check: str = CHECK, limit: int = sensitivity.LIMIT_PIECES
) -> sensitivity.Report:
    root = contour.trajan_root()
    return sensitivity.check(
        project,
        task="t-1",
        patch=patch,
        repro=("sh", "-c", check),
        root=root,
        python=contour.trajan_python(root),
        limit=limit,
    )


def test_every_kind_of_piece_is_classified(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    report = run(project, make_patch(tmp_path, project))
    kinds = {item.piece.kind for item in report.attempts}
    assert "a text block" in kinds and "a config value" in kinds and "a new file" in kinds
    assert len([item for item in report.attempts if item.status == sensitivity.SKIPPED]) == 2
    assert {item.piece.path for item in report.skipped} == {"conf/pipe.yaml", "src/app.py"}


def test_a_piece_the_check_does_not_see_is_blind(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    report = run(project, make_patch(tmp_path, project))
    blind = report.blind
    assert len(blind) == 1, "the rule paragraph is reversed but no check needs it"
    assert "Правило: сроки." in " ".join(blind[0].piece.new)
    assert "still passed" in blind[0].why


def test_the_marker_the_check_looks_for_is_noticed(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    report = run(project, make_patch(tmp_path, project))
    noticed = " ".join(item.piece.where for item in report.noticed)
    assert "Подробности: MARKER." in " ".join(
        text for item in report.noticed for text in item.piece.new
    )
    assert "app.toml" in noticed, "the config value the check reads was reversed and caught"
    assert "notes.md" in noticed, "the new file the check needs was removed and caught"


def test_a_check_that_is_already_red_is_not_a_signal(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with pytest.raises(boxes.HarnessError, match="already red on the change"):
        run(project, make_patch(tmp_path, project), check="exit 1")


def test_a_broken_revert_is_refused_not_counted(tmp_path: Path) -> None:
    """A revert that breaks the format turns the check red for the wrong reason."""
    broken = tmp_path / "broken.toml"
    broken.write_text("value =\n", encoding="utf-8")
    assert sensitivity._valid(broken) is not None
    fine = tmp_path / "fine.toml"
    fine.write_text("value = 1\n", encoding="utf-8")
    assert sensitivity._valid(fine) is None


def test_the_limit_is_named_not_hidden(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    report = run(project, make_patch(tmp_path, project), limit=2)
    assert len([item for item in report.attempts if item.status != sensitivity.SKIPPED]) == 2
    assert report.left_out == 2
    assert any("left out" in line for line in sensitivity.render(report))


def test_the_command_reads_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, patch)
    before = git(project, "status", "--porcelain")
    assert main(
        ["--castra", str(box.root.parent), "sensitivity", box.name, "--task", str(task), "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"] == "t-1"
    assert payload["blind"] == 1 and payload["skipped"] == 2
    assert git(project, "status", "--porcelain") == before, "the project is left as it was"


def test_a_project_inside_a_bigger_repository_reads_its_own_paths(tmp_path: Path) -> None:
    """The box may serve a subdirectory: the patch and the check speak that directory's paths.

    A live run found this: the trajan box serves ``trajan/`` inside a bigger repository, and the
    patch, written against the project, was applied at the repository root, where the file at
    ``docs/guide.md`` does not exist. The worktree is still built from the repository; the project
    inside it is what the change and the check talk about.
    """
    repo = tmp_path / "monorepo"
    app = repo / "app"
    (app / "docs").mkdir(parents=True)
    (app / "docs" / "guide.md").write_text("# Guide\n\nOld line.\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    patch = tmp_path / "change.diff"
    patch.write_text(
        "--- a/docs/guide.md\n"
        "+++ b/docs/guide.md\n"
        "@@ -1,3 +1,4 @@\n"
        " # Guide\n"
        " \n"
        " Old line.\n"
        "+Rule: the check notices.\n",
        encoding="utf-8",
    )
    report = run(app, patch, check="grep -q 'Rule: the check notices' docs/guide.md")
    assert [item.status for item in report.attempts] == [sensitivity.NOTICED]
    assert report.attempts[0].piece.path == "docs/guide.md"
