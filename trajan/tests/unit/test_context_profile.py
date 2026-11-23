"""The project profile: what a repository is, read from disk without a model.

The profile is committed inside the project and tied to a verification run by its digest, so
these tests care about two things beyond the obvious detection: it must be deterministic, and
it must not leak the machine it was taken on (absolute paths, timestamps).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from qagent import ProjectProfile, detect_profile
from qagent.cli import main
from qagent.context import profile_path, read_profile, write_profile


def make_repo(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


PYTHON_REPO = {
    "pyproject.toml": (
        '[project]\nname = "mini"\nrequires-python = ">=3.12"\n\n'
        "[tool.ruff]\nline-length = 100\n\n"
        "[tool.mypy]\nstrict = true\n\n"
        '[tool.pytest.ini_options]\naddopts = "-q"\n'
    ),
    "Makefile": "check: lint test\n\ntest:\n\tpytest\n\nlint:\n\truff check .\n",
    "src/mini/__main__.py": "print('hi')\n",
    "tests/test_mini.py": "def test_it():\n    assert True\n",
    "README.md": "# mini\n",
    ".venv/lib/ignored.py": "raise SystemExit('never read')\n",
    ".git/ignored.py": "raise SystemExit('never read')\n",
}


def test_a_python_repository_is_described_truthfully(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    profile = detect_profile(root)

    assert profile.name == "mini"
    assert profile.languages == {"python": 2}  # .venv and .git are not part of the project
    assert set(profile.package_managers) == {"pip"}
    assert {tool.name for tool in profile.tools} >= {"pytest", "ruff", "mypy", "make-check"}
    assert profile.test_paths == ["tests"]
    assert "src/mini/__main__.py" in profile.entry_points
    assert profile.docs == ["README.md"]
    assert profile.python_version == ">=3.12"


def test_the_same_tree_always_produces_the_same_digest(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    first = detect_profile(root)
    second = detect_profile(root)

    assert first == second
    assert first.digest() == second.digest()
    assert len(first.digest()) == 64


def test_a_changed_tree_changes_the_digest(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)
    before = detect_profile(root).digest()

    (root / "tests" / "test_more.py").write_text("def test_more():\n    pass\n", encoding="utf-8")

    assert detect_profile(root).digest() != before


def test_a_node_repository_reports_its_tooling(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "web",
        {
            "package.json": json.dumps(
                {
                    "name": "web",
                    "scripts": {"test": "vitest run"},
                    "devDependencies": {"eslint": "^9", "typescript": "^5", "vitest": "^2"},
                }
            ),
            "tsconfig.json": "{}",
            "src/app.ts": "export const a = 1;\n",
            "src/app.test.ts": "import { a } from './app';\n",
        },
    )

    profile = detect_profile(root)

    assert profile.languages == {"typescript": 2}
    assert profile.package_managers == ["npm"]
    names = {tool.name for tool in profile.tools}
    assert {"eslint", "tsc", "vitest"} <= names
    assert profile.tools[0].source  # every tool says where it was found


def test_an_empty_directory_is_a_valid_empty_profile(tmp_path: Path) -> None:
    profile = detect_profile(tmp_path)

    assert profile.languages == {}
    assert profile.tools == []
    assert profile.file_count == 0


def test_a_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        detect_profile(tmp_path / "nope")


def test_the_profile_contains_no_absolute_paths(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    rendered = detect_profile(root).model_dump_json()

    assert str(tmp_path) not in rendered


def test_the_profile_round_trips_through_its_file(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)
    profile = detect_profile(root)

    target = write_profile(profile, root)

    assert target == profile_path(root) == root / ".qagent" / "profile.json"
    assert read_profile(root) == profile
    assert json.loads(target.read_text(encoding="utf-8"))["name"] == "mini"


def test_the_profile_is_optional_until_written(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    with pytest.raises(FileNotFoundError, match="inspect"):
        read_profile(root)


def test_a_new_commit_does_not_change_the_profile(tmp_path: Path) -> None:
    """The profile describes the project, not the moment it was read."""
    root = tmp_path / "tracked"
    root.mkdir()
    (root / "main.py").write_text("print(1)\n", encoding="utf-8")
    git = ["git", "-C", str(root)]

    def run(*args: str) -> None:
        completed = subprocess.run(
            [*git, *args], capture_output=True, text=True, check=False, timeout=30
        )
        assert completed.returncode == 0, completed.stderr

    run("init", "-q", "-b", "main")
    run("-c", "user.name=test", "-c", "user.email=test@example.com", "add", "main.py")
    run("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "first")
    before = detect_profile(root).digest()

    run(
        "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-qm", "second", "--allow-empty",
    )

    assert detect_profile(root).digest() == before


def test_inspect_writes_the_profile_inside_the_project(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    assert main(["inspect", "--repo", str(root), "--write"]) == 0

    stored = read_profile(root)
    assert stored == detect_profile(root)


def test_inspect_can_print_the_profile_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    assert main(["inspect", "--repo", str(root), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert ProjectProfile.model_validate(payload) == detect_profile(root)


def test_inspect_says_what_it_found_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path / "mini", PYTHON_REPO)

    assert main(["inspect", "--repo", str(root)]) == 0

    out = capsys.readouterr().out
    assert "mini" in out
    assert "pytest" in out
    assert "not written" in out
    assert not (root / ".qagent" / "profile.json").exists()
