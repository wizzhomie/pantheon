"""Caesar: a change outside the contract is refused before it is written, and the refusal is kept.

The acceptance criterion of stage 1.3 is the fourth test here: the patch that reaches outside
the declared paths changes nothing on disk, exits 1 and leaves an attempt record and a ledger
event behind — an auditable refusal, not a printed warning.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from harness import box as boxes
from harness import caesar
from harness.cli import main

CONTRACT = """\
schema_version = 1
task = "T1"
kind = "feature"
goal = "add a smoke check"
expected = "the new check runs"
allowed_paths = ["src/**", "tests/**"]
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
    (project / "README.md").write_text("readme\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def make_patch(tmp_path: Path, project: Path, edit: dict[str, str | None]) -> Path:
    """Write ``edit`` into the project, capture it as a patch, then put the project back."""
    for name, content in edit.items():
        target = project / name
        if content is None:
            target.unlink()
        else:
            target.write_text(content, encoding="utf-8")
    # `--cached` because a new file is untracked and a plain `git diff` would not show it:
    # a patch that silently loses the added files would be the wrong thing to gate.
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    git(project, "clean", "-qfd")
    return patch


def write_contract(tmp_path: Path, text: str = CONTRACT) -> Path:
    path = tmp_path / "T1.contract.toml"
    path.write_text(text, encoding="utf-8")
    return path


def make_box(tmp_path: Path) -> tuple[Path, Path]:
    project = make_project(tmp_path)
    boxes_root = tmp_path / "castra"
    box = boxes.new_box(boxes_root, project)
    return project, box.root


def test_globs_cross_directories_only_where_told() -> None:
    assert caesar.matches("src/**", "src/a/b.py")
    assert caesar.matches("src/**", "src/app.py")
    assert not caesar.matches("src/*", "src/a/b.py")
    # A pattern is read as written: `*.py` is the top level, `**/*.py` is every level.
    assert not caesar.matches("*.py", "src/a/b.py")
    assert caesar.matches("**/*.py", "src/a/b.py")
    assert not caesar.matches("src/**", "srcx/app.py")
    assert not caesar.matches("tests/**", "src/app.py")


def test_a_diff_names_the_paths_it_touches(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    modified = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    assert caesar.touched_paths(modified.read_text()) == ("src/app.py",)
    added = make_patch(tmp_path, project, {"src/new.py": "x = 1\n"})
    assert caesar.touched_paths(added.read_text()) == ("src/new.py",)
    deleted = make_patch(tmp_path, project, {"README.md": None})
    assert caesar.touched_paths(deleted.read_text()) == ("README.md",)


def test_the_contract_is_strict_about_what_it_does_not_know(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(CONTRACT + 'surprise = "x"\n', encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="unknown key"):
        caesar.read_contract(path)
    path.write_text(CONTRACT.replace('task = "T1"', 'task = "../escape"'), encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="not a path"):
        caesar.read_contract(path)
    path.write_text(CONTRACT + 'forbidden = ["rm -rf"]\n', encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="not enforced"):
        caesar.read_contract(path)
    path.write_text(CONTRACT.replace('schema_version = 1', "schema_version = 9"), encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="schema_version"):
        caesar.read_contract(path)


def test_a_change_outside_the_scope_is_refused_and_written_down(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box_root = make_box(tmp_path)
    patch = make_patch(tmp_path, project, {"README.md": "changed\n"})
    contract = write_contract(tmp_path)
    assert main(["--castra", str(box_root.parent), "run", "probe", "--contract", str(contract),
                 "--patch", str(patch)]) == 1
    out = capsys.readouterr().out
    assert "outside-scope" in out and "result     refused" in out
    assert (project / "README.md").read_text() == "readme\n"
    attempt_dir = box_root / "tabularium" / "T1" / "1"
    record = json.loads((attempt_dir / "attempt.json").read_text())
    assert (record["status"], record["violations"][0]["code"]) == ("refused", "outside-scope")
    assert record["paths"] == ["README.md"]
    assert (attempt_dir / "change.diff").is_file()
    events = (box_root / "fasti" / f"{record['started_at'][:10]}.jsonl").read_text().splitlines()
    assert json.loads(events[-1])["type"] == "attempt.refused"


def test_a_change_inside_the_scope_is_applied_and_written_down(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project, box_root = make_box(tmp_path)
    patch = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    contract = write_contract(tmp_path)
    assert main(["--castra", str(box_root.parent), "run", "probe", "--contract", str(contract),
                 "--patch", str(patch)]) == 0
    assert "result     applied" in capsys.readouterr().out
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    record = json.loads((box_root / "tabularium" / "T1" / "1" / "attempt.json").read_text())
    assert record["status"] == "applied"
    change = json.loads((box_root / "tabularium" / "T1" / "1" / "change.json").read_text())
    assert change["paths"] == ["src/app.py"] and change["digest"].startswith("sha256:")
    assert change["base"]


def test_deleting_a_file_can_be_forbidden(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    patch = make_patch(tmp_path, project, {"src/app.py": None})
    contract = write_contract(tmp_path, CONTRACT + 'forbidden = ["delete"]\n')
    attempt = caesar.run_attempt(box_root, project, caesar.read_contract(contract), patch)
    assert not attempt.applied
    assert [item.code for item in attempt.violations] == ["forbidden-delete"]
    assert (project / "src" / "app.py").is_file()


def test_nothing_authorises_writing_into_git(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    patch = tmp_path / "git.diff"
    patch.write_text(
        "diff --git a/.git/config b/.git/config\n"
        "--- a/.git/config\n"
        "+++ b/.git/config\n"
        "@@ -1 +1 @@\n"
        "-[core]\n"
        "+[core]\n"
        "\\ No newline at end of file\n",
        encoding="utf-8",
    )
    contract = write_contract(tmp_path, CONTRACT.replace('["src/**", "tests/**"]', '["**"]'))
    attempt = caesar.run_attempt(box_root, project, caesar.read_contract(contract), patch)
    assert not attempt.applied
    assert [item.code for item in attempt.violations] == ["git-internals"]


def test_a_contract_without_scope_starts_nothing(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    patch = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    text = CONTRACT.replace('allowed_paths = ["src/**", "tests/**"]\n', "").replace(
        'kind = "feature"\n', ""
    )
    contract = caesar.read_contract(write_contract(tmp_path, text))
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert {item.code for item in attempt.violations} == {"no-kind", "no-scope"}
    assert (project / "src" / "app.py").read_text() == "value = 1\n"


def test_attempts_are_numbered_and_never_overwritten(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    good = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    first = caesar.run_attempt(box_root, project, contract, good)
    second = caesar.run_attempt(box_root, project, contract, good)
    assert (first.number, second.number) == (1, 2)
    assert first.directory != second.directory
    assert (second.directory / "attempt.json").is_file()


def test_the_cli_refuses_a_contract_that_does_not_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    boxes_root = tmp_path / "castra"
    boxes.new_box(boxes_root, project)
    capsys.readouterr()
    patch = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    assert main(["--castra", str(boxes_root), "run", "probe", "--contract",
                 str(tmp_path / "none.toml"), "--patch", str(patch)]) == 2
    assert "no contract" in capsys.readouterr().err
    assert (project / "src" / "app.py").read_text() == "value = 1\n"


def test_the_refusal_explains_every_path_it_saw(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    patch = make_patch(
        tmp_path, project, {"README.md": "x\n", "src/app.py": "value = 3\n", "other.txt": "o\n"}
    )
    attempt = caesar.run_attempt(
        box_root, project, caesar.read_contract(write_contract(tmp_path)), patch
    )
    offenders = sorted(item.detail.split(":")[0] for item in attempt.violations)
    assert offenders == ["README.md", "other.txt"]
    assert (project / "src" / "app.py").read_text() == "value = 1\n"


def test_a_declared_budget_is_recorded_and_unknown_budget_keys_are_errors(
    tmp_path: Path,
) -> None:
    path = tmp_path / "c.toml"
    path.write_text(CONTRACT + "\n[budget]\nmax_seconds = 600\nmax_cents = 5\n", encoding="utf-8")
    contract = caesar.read_contract(path)
    assert contract.budget == {"max_seconds": 600, "max_cents": 5}
    path.write_text(CONTRACT + "\n[budget]\nmax_hours = 1\n", encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="unknown budget key"):
        caesar.read_contract(path)


def make_subproject(tmp_path: Path) -> Path:
    """A project that is a directory inside a larger repository — the real box's shape."""
    repo = tmp_path / "repo"
    (repo / "sub" / "src").mkdir(parents=True)
    (repo / "sub" / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return repo / "sub"


def test_a_project_inside_a_larger_repository_is_written_where_it_lives(
    tmp_path: Path,
) -> None:
    """`git diff` in a subdirectory names paths from the root; the gate must translate them."""
    project = make_subproject(tmp_path)
    patch = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    assert "a/sub/src/app.py" in patch.read_text()
    contract = caesar.read_contract(write_contract(tmp_path))
    box = boxes.new_box(tmp_path / "castra", project)
    attempt = caesar.run_attempt(box.root, project, contract, patch)
    assert attempt.applied, attempt.reason
    assert (project / "src" / "app.py").read_text() == "value = 2\n"
    assert attempt.paths == ("src/app.py",)
    assert (attempt.directory / "change.source.diff").is_file()
    change = json.loads((attempt.directory / "change.json").read_text())
    assert change["paths"] == ["src/app.py"]


def test_a_model_style_diff_lands_in_a_project_inside_a_repository(tmp_path: Path) -> None:
    project = make_subproject(tmp_path)
    patch = tmp_path / "model.diff"
    patch.write_text(
        "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
        encoding="utf-8",
    )
    contract = caesar.read_contract(write_contract(tmp_path))
    box = boxes.new_box(tmp_path / "castra", project)
    attempt = caesar.run_attempt(box.root, project, contract, patch)
    assert attempt.applied, attempt.reason
    assert (project / "src" / "app.py").read_text() == "value = 2\n"


def test_a_patch_that_changes_nothing_is_refused_not_applied(tmp_path: Path) -> None:
    """`git apply` exits zero when a hunk rewrites a line with itself; "applied" means changed."""
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    patch = tmp_path / "same.diff"
    patch.write_text(
        "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-value = 1\n+value = 1\n",
        encoding="utf-8",
    )
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert "changed nothing" in attempt.reason
    assert (project / "src" / "app.py").read_text() == "value = 1\n"
    assert (attempt.directory / "attempt.json").is_file()


def test_an_empty_patch_is_refused_by_the_project(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    patch = tmp_path / "empty.diff"
    patch.write_text("", encoding="utf-8")
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert "would not take the patch" in attempt.reason


def test_a_mistyped_hunk_header_does_not_drop_the_last_line(tmp_path: Path) -> None:
    """`git apply` trusts the header count; an undercount silently loses the extra lines."""
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    patch = tmp_path / "short.diff"
    patch.write_text(
        "diff --git a/src/new.py b/src/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+a\n+b\n+c\n+d\n",
        encoding="utf-8",
    )
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert attempt.applied
    assert (project / "src" / "new.py").read_text() == "a\nb\nc\nd\n"
    assert "header said -0/+3" in attempt.reason
    assert "body carried -0/+4" in attempt.reason
    assert (attempt.directory / "change.source.diff").is_file()


def test_a_correct_hunk_header_is_left_alone(tmp_path: Path) -> None:
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    patch = make_patch(tmp_path, project, {"src/new.py": "a\nb\n"})
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert attempt.applied
    assert attempt.reason == ""
    assert not (attempt.directory / "change.source.diff").exists()


def test_a_hunk_with_context_on_one_side_is_applied_and_said_out_loud(tmp_path: Path) -> None:
    """Models write hunks with context only above the change; git refuses them by default."""
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    (project / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "longer")
    patch = tmp_path / "one-sided.diff"
    patch.write_text(
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -2,1 +2,2 @@\n"
        " two\n"
        "+two and a half\n",
        encoding="utf-8",
    )
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert attempt.applied
    assert (project / "src" / "app.py").read_text() == "one\ntwo\ntwo and a half\nthree\n"
    assert "context on one side only" in attempt.reason
    assert "--unidiff-zero" in attempt.reason


def test_a_one_sided_change_is_committed_after_the_gate_accepted_it(tmp_path: Path) -> None:
    """The commit rebuilds the change from the same patch: it must accept what the gate did."""
    project, box_root = make_box(tmp_path)
    contract = caesar.read_contract(write_contract(tmp_path))
    (project / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "longer")
    patch = tmp_path / "one-sided.diff"
    patch.write_text(
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -2,1 +2,2 @@\n"
        " two\n"
        "+two and a half\n",
        encoding="utf-8",
    )
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert attempt.applied
    commit = caesar.commit_change(
        project,
        contract,
        attempt,
        verdict="pass",
        report=str(tmp_path / "report.md"),
        box_root=box_root,
    )
    assert commit.status == "made", commit.reason
    assert git(project, "show", "--stat", "--oneline", "HEAD").splitlines()[0]
    assert "two and a half" in git(project, "show", "HEAD:src/app.py")


def test_a_task_with_a_reproduction_is_read_and_written_back(tmp_path: Path) -> None:
    """The repro is task data: it must survive the snapshot a dispatch freezes."""
    from harness import augustus

    path = tmp_path / "T2.toml"
    path.write_text(
        'schema_version = 1\nid = "T2"\npaths = ["src/**"]\n'
        'repro = ["python", "-c", "raise SystemExit(1)"]\n',
        encoding="utf-8",
    )
    task = augustus.read_task(path)
    assert task.repro == ("python", "-c", "raise SystemExit(1)")
    text = augustus.task_text(task)
    assert 'repro = ["python", "-c", "raise SystemExit(1)"]' in text

    # A command with newlines is the pilot's shape: the frozen file must parse back.
    command = 'import sys\nprint("say hi")\nraise SystemExit(1)'
    source = tmp_path / "T3.toml"
    source.write_text(
        'schema_version = 1\nid = "T3"\npaths = ["src/**"]\n'
        "repro = ['python', '-c', '''" + command + "''']\n",
        encoding="utf-8",
    )
    loaded = augustus.read_task(source)
    assert loaded.repro == ("python", "-c", command)
    frozen = tmp_path / "T3-frozen.toml"
    frozen.write_text(augustus.task_text(loaded), encoding="utf-8")
    assert augustus.read_task(frozen).repro == loaded.repro

    path.write_text(
        'schema_version = 1\nid = "T2"\npaths = ["src/**"]\nrepro = []\n', encoding="utf-8"
    )
    assert augustus.read_task(path).repro == ()
    path.write_text(
        'schema_version = 1\nid = "T2"\npaths = ["src/**"]\nrepro = ["python"]\n',
        encoding="utf-8",
    )
    with pytest.raises(boxes.HarnessError, match="at least the executable"):
        augustus.read_task(path)


def test_the_reproduction_becomes_a_check_beside_the_policy(tmp_path: Path) -> None:
    """A defect task's input runs as a check: the policy stays data, the check is appended."""
    from harness import augustus

    project = tmp_path / "proj"
    (project / ".trajan").mkdir(parents=True)
    (project / ".trajan" / "policy.toml").write_text(
        'name = "probe"\n\n[budget]\nmax_checks = 3\n', encoding="utf-8"
    )
    policy = augustus._policy_with_repro(
        project, ("python", "-c", "print(1)"), tmp_path / "out"
    )
    data = tomllib.loads(policy.read_text(encoding="utf-8"))
    assert data["name"] == "probe"
    assert data["budget"]["max_checks"] == 3
    assert [check["id"] for check in data["checks"]] == ["repro"]
    assert data["checks"][0]["argv"] == ["python", "-c", "print(1)"]
    assert "when_paths" not in data["checks"][0]
