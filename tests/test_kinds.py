"""What each kind of task owns: code never writes the frame, architecture never writes code.

The box's roles are only as real as the gate's refusal: if any task could declare the project's
own architecture model or requirements into its scope, "the architect owns the frame" would be a
sentence in a document. These tests make the refusal mechanical and recorded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import caesar

FEATURE = """\
schema_version = 1
task = "T1"
kind = "feature"
goal = "change the app value"
expected = "the value is two"
allowed_paths = [{paths}]
"""

ARCHITECTURE = """\
schema_version = 1
task = "T1"
kind = "architecture"
goal = "write down the model"
expected = "the model exists"
allowed_paths = [{paths}]
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


def make_patch(tmp_path: Path, project: Path, edit: dict[str, str]) -> Path:
    """Write ``edit``, capture it as a patch, then put the project back."""
    for name, content in edit.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    patch = tmp_path / "change.diff"
    patch.write_text(git(project, "diff", "--cached"), encoding="utf-8")
    git(project, "reset", "-q")
    git(project, "checkout", "--", ".")
    git(project, "clean", "-qfd")
    return patch


def write_contract(tmp_path: Path, text: str, paths: str) -> Path:
    path = tmp_path / "T1.contract.toml"
    path.write_text(text.format(paths=paths), encoding="utf-8")
    return path


def make_box(tmp_path: Path, project: Path) -> Path:
    return boxes.new_box(tmp_path / "boxes", project).root


def test_code_cannot_write_the_architecture_model(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(
        tmp_path, project, {".pantheon/architecture.toml": "schema_version = 1\n"}
    )
    contract = caesar.read_contract(write_contract(tmp_path, FEATURE, '".pantheon/**"'))
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert [item.code for item in attempt.violations] == ["kind-forbidden"]
    assert not (project / ".pantheon" / "architecture.toml").exists()


def test_code_cannot_write_the_requirements(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project, {".trajan/context.toml": "schema_version = 1\n"})
    contract = caesar.read_contract(write_contract(tmp_path, FEATURE, '".trajan/context.toml"'))
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert [item.code for item in attempt.violations] == ["kind-forbidden"]


def test_architecture_cannot_write_code(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    contract = caesar.read_contract(write_contract(tmp_path, ARCHITECTURE, '"src/**"'))
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert [item.code for item in attempt.violations] == ["outside-kind"]
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_architecture_may_write_the_frame_and_the_documents(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(
        tmp_path,
        project,
        {
            ".pantheon/architecture.toml": "schema_version = 1\n",
            "docs/adr.md": "# ADR\n",
        },
    )
    contract = caesar.read_contract(
        write_contract(tmp_path, ARCHITECTURE, '".pantheon/**", "docs/**"')
    )
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert attempt.applied
    assert (project / ".pantheon" / "architecture.toml").is_file()
    assert (project / "docs" / "adr.md").is_file()


def test_a_project_policy_can_narrow_its_own_kind(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(
        tmp_path, project, {".pantheon/architecture.toml": "schema_version = 1\n"}
    )
    policy = tmp_path / "kinds.toml"
    policy.write_text(
        'schema_version = 1\n[[kind]]\nname = "architecture"\nallow = ["docs/**"]\n',
        encoding="utf-8",
    )
    (project / ".pantheon").mkdir(parents=True)
    (project / ".pantheon" / "kinds.toml").write_text(
        policy.read_text(encoding="utf-8"), encoding="utf-8"
    )
    contract = caesar.read_contract(write_contract(tmp_path, ARCHITECTURE, '".pantheon/**"'))
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert not attempt.applied
    assert [item.code for item in attempt.violations] == ["outside-kind"]


def test_a_docs_task_is_not_restricted_by_the_frame_rule(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project, {"docs/note.md": "# note\n", "README.md": "new\n"})
    contract = caesar.read_contract(
        write_contract(tmp_path, FEATURE.replace('"feature"', '"docs"'), '"docs/**", "README.md"')
    )
    attempt = caesar.run_attempt(box_root, project, contract, patch)
    assert attempt.applied


def test_a_broken_policy_stops_the_gate(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    box_root = make_box(tmp_path, project)
    patch = make_patch(tmp_path, project, {"src/app.py": "value = 2\n"})
    (project / ".pantheon").mkdir()
    (project / ".pantheon" / "kinds.toml").write_text("schema_version = 2\n", encoding="utf-8")
    contract = caesar.read_contract(write_contract(tmp_path, FEATURE, '"src/**"'))
    with pytest.raises(boxes.HarnessError):
        caesar.run_attempt(box_root, project, contract, patch)
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
