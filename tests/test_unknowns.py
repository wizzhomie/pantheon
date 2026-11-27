"""What a dispatch did not check, said out loud before anyone reads the word "closed".

The cases here are the ways a green answer can be narrower than it looks: the verdict came from
a checks-only run, from an earlier run of the same tree, from a task whose architecture was
never checked, or from a run nothing limited. Each one must appear in the outbox, in the record
and in `status` — a closed task that quietly forgets one of them is the failure this file is
about.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from harness import augustus, caesar, container, contour, spec
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

[contract]
symptom = "the value is one"
visible = "src/app.py says value = 2"
boundaries = "src/**"
missing = ""

[budget]
max_cents = 2
max_seconds = 60
"""


SPEC_PACKAGE = """\
schema_version = 1
id = "pkg-t-1"
milestone = "M1"
source = "the test"
why = "the feature test needs a package the owner accepted"
spec = "spec.md"

[[task]]
id = "t-1"
kind = "feature"
goal = "change the app value"
paths = ["src/**"]
expected = "the value is two"
contract = { symptom = "the value is one", visible = "app says two", boundaries = "src/**" }
repro = ["python", "-c", "assert True"]
"""


def accept_spec(box: boxes.Box, tmp_path: Path) -> str:
    """A real imported and accepted package: the basis a feature task now needs to start."""
    source = tmp_path / "spec-source"
    source.mkdir()
    (source / "manifest.toml").write_text(SPEC_PACKAGE, encoding="utf-8")
    (source / "spec.md").write_text("# the intent\n", encoding="utf-8")
    package = spec.read_package(source)
    spec.import_package(box, package)
    spec.accept(box, package.id, note="the test accepts a real package")
    return package.id


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


def make_box(tmp_path: Path) -> tuple[Path, boxes.Box]:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    box.compose.write_text(
        container.render_compose(box, "nousresearch/hermes-agent@sha256:" + "3e9f" * 16),
        encoding="utf-8",
    )
    return project, box


def make_patch(tmp_path: Path, project: Path) -> Path:
    (project / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    return patch


def with_change(patch: Path, text: str = TASK) -> str:
    """A task that carries its own patch: `change` belongs before the tables, not inside one."""
    marker = "\n[contract]" if "\n[contract]" in text else "\n[budget]"
    head, tail = text.split(marker, 1)
    return head + f'change = "{patch}"\n' + marker + tail


def write_task(tmp_path: Path, text: str, name: str = "t-1.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def outbox(box: boxes.Box, capsys: pytest.CaptureFixture[str]) -> str:
    capsys.readouterr()
    latest = max((box.root / "acta").glob("*.md"), key=lambda item: item.stat().st_mtime)
    return latest.read_text(encoding="utf-8")


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
def test_a_checks_only_verdict_says_nobody_read_the_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    code = main(
        ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["state"] == "closed"
    assert any("no reviewer read the change" in item for item in payload["unknowns"])
    text = outbox(box, capsys)
    assert "## what we did not check" in text
    assert "no reviewer read the change" in text


@requires_trajan
def test_a_reused_verdict_says_the_checks_did_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(
        tmp_path,
        TASK.replace('kind = "bugfix"', 'kind = "docs"').replace(
            'paths = ["src/**"]', 'paths = ["src/**"]'
        ),
    )
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    capsys.readouterr()
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    out = capsys.readouterr().out
    assert "reused from" in out
    text = outbox(box, capsys)
    assert "the checks did not run in this dispatch" in text


@requires_trajan
def test_an_unchecked_architecture_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    package_id = accept_spec(box, tmp_path)
    text = with_change(
        patch,
        TASK.replace(
            'kind = "bugfix"', f'kind = "feature"\nspec = "{package_id}"'
        ),
    )
    task = write_task(tmp_path, text)
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    body = outbox(box, capsys)
    assert "the architecture was not confirmed (unchecked)" in body


@requires_trajan
def test_a_task_without_a_budget_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project, box = make_box(tmp_path)
    task = write_task(
        tmp_path,
        'schema_version = 1\nid = "t-1"\ngoal = "look"\nkind = "docs"\npaths = ["src/**"]\n'
        'expected = "the checks still pass"\n[contract]\nsymptom = "the checks are not run"\n'
        'visible = "the policy checks pass"\nboundaries = "src/**"\nmissing = ""\n',
    )
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    body = outbox(box, capsys)
    assert "the task declared no budget" in body


@requires_trajan
def test_status_shows_what_the_newest_dispatch_did_not_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    capsys.readouterr()
    assert main(["--castra", str(box.root.parent), "status", box.name]) == 0
    out = capsys.readouterr().out
    assert "unknown    no reviewer read the change" in out


REVIEW = '{"summary": "adds a constant", "findings": []}'


@requires_trajan
def test_a_reviewed_dispatch_names_the_reader_and_drops_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    code = main(
        ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--review"]
    )
    capsys.readouterr()
    assert code == 0
    records = (box.root / "tabularium" / "t-1").glob("dispatch-*.json")
    payload = json.loads(max(records, key=lambda item: item.name).read_text(encoding="utf-8"))
    assert payload["verify"]["reviewed"] is True
    assert payload["verify"]["review"]["status"] == "ok"
    assert payload["verify"]["review"]["cached"] is False, "a paid reading is not a cached one"
    assert not any("no reviewer read the change" in item for item in payload["unknowns"])
    body = outbox(box, capsys)
    assert "read by deepseek-flash" in body
    assert "review: deepseek-flash" in body, "the reading has its own line in the cost section"


@requires_trajan
def test_a_reading_from_the_review_cache_is_free_not_unaccounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Trajan answers the same diff from its own cache: no call, no bill, and no gap.

    The first dispatch pays and its record says so. The second one cannot reuse the verdict —
    the tree moved — so the checks run, and the reader is asked for the very diff that was read
    before. Trajan serves that from its review cache: nothing was spent, and a box that calls
    this "the reviewer did not report what it cost" accuses the reader of hiding a bill it
    never ran up.
    """
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    argv = ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]
    assert main([*argv, "--review"]) == 0
    capsys.readouterr()
    (project / "notes.txt").write_text("unrelated to the change\n", encoding="utf-8")
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--review",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    review = payload["verify"]["review"]
    assert review["cached"] is True
    assert review["cost_cents"] is None
    assert not any("did not report what it cost" in item for item in payload["unknowns"])
    record = json.loads(
        (Path(payload["verify"]["report"]).parent / "box-verify.json").read_text(encoding="utf-8")
    )
    assert record["review"]["cached"] is True, "the record keeps why there is no bill"
    body = outbox(box, capsys)
    assert "paid nothing" in body
    assert "did not report what it cost" not in body


@requires_trajan
def test_a_review_without_a_bill_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The agent answered but reported no cost: the reading happened, its price is unknown."""
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--review",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert any("the reviewer did not report what it cost" in item for item in payload["unknowns"])


@requires_trajan
def test_a_failed_review_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reader was asked and could not answer: that is not the same as nobody reading."""
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    monkeypatch.setenv("FAKE_DOCKER_FAIL", "1")
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--review",
                "--json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert any("the reviewer was asked and failed" in item for item in payload["unknowns"])


def declare_reader(box: boxes.Box, text: str = "schema_version = 1\nenabled = true\n") -> None:
    path = box.root / "persona" / "reviewer.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@requires_trajan
def test_a_box_that_declares_reading_reads_without_a_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    declare_reader(box)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert main(["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task)]) == 0
    body = outbox(box, capsys)
    assert "read by deepseek-flash" in body
    assert "no reviewer read the change" not in body


@requires_trajan
def test_no_review_wins_over_the_declared_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    declare_reader(box)
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "dispatch",
                box.name,
                "--task",
                str(task),
                "--no-review",
            ]
        )
        == 0
    )
    body = outbox(box, capsys)
    assert "no reviewer read the change" in body
    assert "read by" not in body


@requires_trajan
def test_a_reading_above_the_declared_ceiling_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The call already happened; pretending the ceiling held would be the quiet pass."""
    project, box = make_box(tmp_path)
    patch = make_patch(tmp_path, project)
    task = write_task(tmp_path, with_change(patch))
    declare_reader(box, 'schema_version = 1\nenabled = true\nmax_cents = 0.01\n')
    bill = box.root / "cella" / ".cache" / "review-usage.json"
    bill.parent.mkdir(parents=True, exist_ok=True)
    bill.write_text(
        json.dumps({"input_tokens": 900, "output_tokens": 60, "estimated_cost_usd": 0.004}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_DOCKER_AGENT", REVIEW)
    assert (
        main(
            ["--castra", str(box.root.parent), "dispatch", box.name, "--task", str(task), "--json"]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["review_cap"] == 0.01
    assert any("more than the 0.01 the box declared" in item for item in payload["unknowns"])


def _reviewed(
    tmp_path: Path,
    *,
    files_reviewed: int | None,
    truncated: bool = False,
    attempt_paths: tuple[str, ...] | None = None,
    cost_cents: float | None = 0.1,
) -> augustus.Dispatch:
    task = augustus.Task(
        path=tmp_path / "t.toml",
        id="t",
        goal=None,
        kind="bugfix",
        paths=("src/**",),
        expected=None,
        change=None,
        forbidden=(),
        budget={},
    )
    review = contour.Review(
        model="m",
        status="ok",
        proposed=0,
        confirmed=0,
        prompt_tokens=10,
        completion_tokens=1,
        calls=1,
        cost_cents=cost_cents,
        files_reviewed=files_reviewed,
        truncated=truncated,
    )
    verify = contour.VerifyResult(
        run=tmp_path,
        report=tmp_path / "report.json",
        run_id="r",
        status="pass",
        reasons=(),
        findings=0,
        blocking=0,
        checks=1,
        exit_code=0,
        reviewed=True,
        review=review,
    )
    attempt = (
        None
        if attempt_paths is None
        else caesar.Attempt(
            task="t", number=1, status="applied", reason="", paths=attempt_paths
        )
    )
    return augustus.Dispatch(
        task=task, state="closed", stages=("verify",), attempt=attempt, verify=verify
    )


def test_a_reading_that_saw_one_file_of_four_names_the_rest(tmp_path: Path) -> None:
    """A reviewer shown a part of the change must not read as "found nothing in it"."""
    result = _reviewed(
        tmp_path,
        files_reviewed=1,
        attempt_paths=("src/a.py", "src/b.py", "src/c.py", "src/d.py"),
    )

    gaps = augustus._unknowns(result)

    assert any("shown 1 of the 4 changed files" in item for item in gaps)
    assert any("did not fit the reading budget" in item for item in gaps)


def test_a_cut_reading_over_the_ceiling_names_both(tmp_path: Path) -> None:
    """Two different gaps: the reading was cut and it cost more than the box declared."""
    from dataclasses import replace

    result = _reviewed(
        tmp_path,
        files_reviewed=0,
        truncated=True,
        attempt_paths=("src/a.py",),
        cost_cents=2.0,
    )

    gaps = augustus._unknowns(replace(result, review_cap=1.0))

    assert any("cut before the reviewer saw it" in item for item in gaps)
    assert any("more than the 1 the box declared" in item for item in gaps)


def test_a_cut_change_is_named_even_without_a_file_count(tmp_path: Path) -> None:
    result = _reviewed(
        tmp_path, files_reviewed=0, truncated=True, attempt_paths=("src/a.py",)
    )

    gaps = augustus._unknowns(result)

    assert any("cut before the reviewer saw it" in item for item in gaps)
    assert not any("changed files" in item for item in gaps), "a cut count means nothing"
