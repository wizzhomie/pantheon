"""The test floor: a green suite that got smaller is not a green suite.

The cases are the ways an agent makes red tests disappear — delete the file, delete the
function, mark it skipped — and the ways a legitimate change must not be punished: a test
moved to another file, and a test added.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trajan.guards.floor import compare, is_test_file, main, scan_source, snapshot


def test_is_test_file_recognises_the_usual_layouts() -> None:
    assert is_test_file("tests/unit/test_a.py")
    assert is_test_file("pkg/a_test.py")
    assert is_test_file("tests/helpers.py")
    assert not is_test_file("src/pkg/a.py")
    assert not is_test_file(".venv/lib/site-packages/tests/test_x.py")
    assert not is_test_file("tests/data.json")


def test_scan_source_counts_functions_methods_and_skip_markers() -> None:
    names, skips = scan_source(
        "import pytest\n"
        "def test_a(): pass\n"
        "async def test_b(): pass\n"
        "def helper(): pass\n"
        "class TestK:\n"
        "    def test_c(self): pass\n"
        "@pytest.mark.skip\n"
        "def test_d(): pass\n"
        "@pytest.mark.xfail\n"
        "def test_e(): pass\n"
    )
    assert names == {"test_a", "test_b", "TestK.test_c", "test_d", "test_e"}
    assert skips == 2


def test_unchanged_suite_passes() -> None:
    body = "def test_a(): pass\n"
    report = compare(snapshot({"tests/test_x.py": body}), snapshot({"tests/test_x.py": body}))
    assert report.passed and not report.removed


def test_adding_a_test_is_fine() -> None:
    base = snapshot({"tests/test_x.py": "def test_a(): pass\n"})
    now = snapshot({"tests/test_x.py": "def test_a(): pass\ndef test_b(): pass\n"})
    assert compare(base, now).passed


def test_deleting_a_test_file_is_caught() -> None:
    base = snapshot({"tests/test_x.py": "def test_a(): pass\ndef test_b(): pass\n"})
    report = compare(base, snapshot({}))
    assert not report.passed
    assert {name for _, name in report.removed} == {"test_a", "test_b"}


def test_deleting_one_function_is_caught() -> None:
    base = snapshot({"tests/test_x.py": "def test_a(): pass\ndef test_b(): pass\n"})
    now = snapshot({"tests/test_x.py": "def test_a(): pass\n"})
    report = compare(base, now)
    assert report.removed == (("tests/test_x.py", "test_b"),)


def test_a_moved_test_is_not_a_removal() -> None:
    base = snapshot({"tests/test_x.py": "def test_a(): pass\n"})
    now = snapshot({"tests/test_y.py": "def test_a(): pass\n"})
    report = compare(base, now)
    assert report.passed
    assert report.moved == (("tests/test_x.py", "test_a"),)


def test_a_new_skip_marker_is_caught() -> None:
    base = snapshot({"tests/test_x.py": "def test_a(): pass\n"})
    now = snapshot({"tests/test_x.py": "import pytest\n@pytest.mark.skip\ndef test_a(): pass\n"})
    report = compare(base, now)
    assert not report.passed and report.skips_added == 1


def test_a_file_that_stops_parsing_loses_its_tests() -> None:
    base = snapshot({"tests/test_x.py": "def test_a(): pass\n"})
    now = snapshot({"tests/test_x.py": "def test_a(:\n"})
    report = compare(base, now)
    assert not report.passed
    assert report.unreadable == ("tests/test_x.py",)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _project(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(): pass\ndef test_b(): pass\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_cli_passes_on_a_clean_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path)
    assert main(["--repo", str(root), "--base", "HEAD"]) == 0
    assert "result: ok" in capsys.readouterr().out


def test_cli_fails_when_the_agent_deletes_the_failing_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    (root / "tests" / "test_x.py").unlink()
    assert main(["--repo", str(root), "--base", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "removed  tests/test_x.py::test_a" in out
    assert "the suite got smaller" in out


def test_cli_cannot_read_an_unknown_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    assert main(["--repo", str(root), "--base", "no-such-rev"]) == 2
    assert "could not read no-such-rev" in capsys.readouterr().out


def test_a_project_inside_a_bigger_repository_sees_only_its_own_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The monorepo case: ``trajan/`` is tracked from above, and its neighbours are not ours."""
    (tmp_path / "mine" / "tests").mkdir(parents=True)
    (tmp_path / "other" / "tests").mkdir(parents=True)
    (tmp_path / "mine" / "tests" / "test_x.py").write_text("def test_a(): pass\n")
    (tmp_path / "other" / "tests" / "test_y.py").write_text("def test_b(): pass\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")

    mine = tmp_path / "mine"
    assert main(["--repo", str(mine), "--base", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "1 tests then, 1 now" in out

    (mine / "tests" / "test_x.py").unlink()
    assert main(["--repo", str(mine), "--base", "HEAD"]) == 1
    assert "removed  tests/test_x.py::test_a" in capsys.readouterr().out


def test_deleting_one_of_two_same_named_tests_is_caught() -> None:
    """A twin with the same name elsewhere hides a removal from a name-only comparison."""
    base = snapshot(
        {"tests/test_x.py": "def test_a(): pass\n", "tests/test_y.py": "def test_a(): pass\n"}
    )
    now = snapshot({"tests/test_x.py": "def test_a(): pass\n"})
    report = compare(base, now)
    assert not report.passed
    assert report.removed == (("tests/test_y.py", "test_a"),)


def test_a_base_that_does_not_parse_is_not_a_quiet_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(:\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(): pass\n")

    assert main(["--repo", str(tmp_path), "--base", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "unreadable  tests/test_x.py" in out
    assert "result: the suite got smaller" in out
