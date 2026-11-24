"""The plan decides which checks a change needs — and says which ones it passed over.

A check declares the paths it guards (`when_paths`); with a change in hand, a check none of
whose paths are touched is *not selected*, and the report says so instead of quietly running
for a minute. Two things keep that honest:

* the reading is what makes the plan possible: trajan refuses a change nobody read, so a
  checks-only run has no change and plans every check;
* a verdict about a diff is only reusable for the same diff, and a reviewed verdict is not
  the same answer as a checks-only one — the change and the reading are part of the evidence.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour
from harness.cli import main

POLICY = """\
name = "probe"

[[checks]]
id = "always"
argv = ["python", "-c", "print('ok')"]
purpose = "a check that guards the whole project"

[[checks]]
id = "src-only"
argv = ["python", "-c", "print('src')"]
purpose = "a check that only guards the source"
when_paths = ["src/**"]
"""

REVIEW = '{"summary": "a note", "findings": []}'


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
    (project / ".trajan" / "policy.toml").write_text(POLICY, encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def docs_change(tmp_path: Path, project: Path) -> Path:
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "note.md").write_text("# note\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "docs.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    return patch


def src_change(tmp_path: Path, project: Path) -> Path:
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "src.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def reviewed(
    tmp_path: Path, project: Path, patch: Path, box: boxes.Box | None = None
) -> contour.VerifyResult:
    """Run trajan with a scripted reader: the plan is what is under test, not the model."""
    runs = (box.root / "artifacts") if box is not None else (tmp_path / "runs")
    runs.mkdir(parents=True, exist_ok=True)
    return contour.run_verify(
        project,
        root=contour.trajan_root(),
        python=contour.trajan_python(contour.trajan_root()),
        out=contour.run_directory(runs, datetime.now(UTC)),
        diff=patch,
        reviewer="trajan.review.scripted:from_env",
        reviewer_env={"TRAJAN_REVIEW_RESPONSE": '{"summary": "s", "findings": []}'},
        box_root=None if box is None else box.root,
    )


requires_trajan = pytest.mark.skipif(
    not (Path(contour.REPO_ROOT) / "trajan" / "src" / "trajan" / "__init__.py").is_file(),
    reason="trajan is not next to the harness",
)


@requires_trajan
def test_a_docs_change_does_not_run_the_check_it_cannot_break(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = reviewed(tmp_path, project, docs_change(tmp_path, project))
    assert result.skipped == ("src-only",)
    assert result.checks == 2, "the check is in the report, marked as not selected"
    assert result.passed, "a plan that skips a check it does not need is still a pass"


@requires_trajan
def test_a_source_change_selects_every_check(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = reviewed(tmp_path, project, src_change(tmp_path, project))
    assert result.skipped == ()


@requires_trajan
def test_the_same_change_is_not_computed_twice(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    patch = docs_change(tmp_path, project)
    box = boxes.new_box(tmp_path / "boxes", project)
    first = reviewed(tmp_path, project, patch, box)
    assert first.reused_from is None
    second = reviewed(tmp_path, project, patch, box)
    assert second.reused_from is not None
    assert second.status == first.status
    assert second.skipped == first.skipped


@requires_trajan
def test_a_different_change_over_the_same_tree_is_not_reused(tmp_path: Path) -> None:
    """Same files, same bytes, another diff: the plan answered a different question."""
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    patch = docs_change(tmp_path, project)
    first = reviewed(tmp_path, project, patch, box)
    assert first.reused_from is None
    other = tmp_path / "docs-2.diff"
    other.write_text(patch.read_text(encoding="utf-8").replace("# note", "# note two"))
    second = reviewed(tmp_path, project, other, box)
    assert second.reused_from is None


@requires_trajan
def test_a_checks_only_answer_is_not_reused_for_a_change(tmp_path: Path) -> None:
    """The same tree, a different question: nobody read that change, so this answer is new."""
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    checks_only = contour.run_verify(
        project,
        root=contour.trajan_root(),
        python=contour.trajan_python(contour.trajan_root()),
        out=contour.run_directory(box.root / "artifacts", datetime.now(UTC)),
        box_root=box.root,
    )
    assert checks_only.passed and checks_only.skipped == ()
    patch = src_change(tmp_path, project)
    after = reviewed(tmp_path, project, patch, box)
    assert after.reused_from is None


@requires_trajan
def test_a_reused_reading_keeps_its_bill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reuse must not make the dispatch look cheaper than the run that paid for the review."""
    project = make_project(tmp_path)
    patch = docs_change(tmp_path, project)
    box = boxes.new_box(tmp_path / "boxes", project)
    box.compose.write_text(
        "services:\n  augustus:\n    image: test\n", encoding="utf-8"
    )
    bill = box.root / "state" / ".cache" / "review-usage.json"
    bill.parent.mkdir(parents=True, exist_ok=True)
    bill.write_text(
        json.dumps({"input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.003}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    reader = contour.box_reviewer(box.root)
    first = contour.run_verify(
        project,
        root=contour.trajan_root(),
        python=contour.trajan_python(contour.trajan_root()),
        out=contour.run_directory(box.root / "artifacts", datetime.now(UTC)),
        diff=patch,
        reviewer=reader.spec,
        reviewer_env=reader.env,
        box_root=box.root,
    )
    assert first.review is not None and first.review.cost_cents == 0.3
    second = contour.run_verify(
        project,
        root=contour.trajan_root(),
        python=contour.trajan_python(contour.trajan_root()),
        out=contour.run_directory(box.root / "artifacts", datetime.now(UTC)),
        diff=patch,
        reviewer=reader.spec,
        reviewer_env=reader.env,
        box_root=box.root,
    )
    assert second.reused_from is not None
    assert second.review is not None and second.review.cost_cents == 0.3


@requires_trajan
def test_the_outbox_names_the_checks_the_plan_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "boxes", project)
    box.compose.write_text(
        "services:\n  augustus:\n    image: test\n", encoding="utf-8"
    )
    (box.root / "profile" / "reviewer.toml").write_text(
        "schema_version = 1\nenabled = true\n", encoding="utf-8"
    )
    patch = docs_change(tmp_path, project)
    task = tmp_path / "t-docs.toml"
    task.write_text(
        'schema_version = 1\nid = "t-docs"\ngoal = "add a note"\nkind = "docs"\n'
        'paths = ["docs/**"]\nexpected = "the note exists"\n'
        f'change = "{patch}"\n\n[budget]\nmax_cents = 2\nmax_seconds = 60\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert main(["--boxes", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "not selected: src-only" in out
    latest = max((box.root / "outbox").glob("*.md"), key=lambda item: item.stat().st_mtime)
    assert "not selected: src-only" in latest.read_text(encoding="utf-8")


@requires_trajan
def test_a_reading_served_from_the_cache_says_it_costs_nothing_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trajan caches a review of the same diff; the box must not call that a missing bill."""
    project = make_project(tmp_path)
    patch = docs_change(tmp_path, project)
    box = boxes.new_box(tmp_path / "boxes", project)
    box.compose.write_text("services:\n  augustus:\n    image: test\n", encoding="utf-8")
    bill = box.root / "state" / ".cache" / "review-usage.json"
    bill.parent.mkdir(parents=True, exist_ok=True)
    bill.write_text(
        json.dumps({"input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.002}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    reader = contour.box_reviewer(box.root)

    def once() -> contour.VerifyResult:
        bill.write_text(
            json.dumps({"input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.002}),
            encoding="utf-8",
        )
        return contour.run_verify(
            project,
            root=contour.trajan_root(),
            python=contour.trajan_python(contour.trajan_root()),
            out=contour.run_directory(tmp_path / "runs", datetime.now(UTC)),
            diff=patch,
            reviewer=reader.spec,
            reviewer_env=reader.env,
            box_root=None,
        )

    first = once()
    assert first.review is not None and not first.review.cached
    second = once()
    assert second.review is not None and second.review.cached
    assert "review cache" in second.review.line()
    assert second.review.cost_cents is None
