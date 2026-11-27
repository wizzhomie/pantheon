"""The named break: does the check notice the behaviour it is supposed to protect?

A green check proves the result looks right; these tests hold the other half — the break is
applied on purpose, and the check either catches it or is blind to it. Also held: a break that no
longer applies or does not compile is refused instead of counted, a red check cannot answer the
question at all, the audit sees the working tree (not only the commit), and the project is left
exactly as it was.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import box as boxes
from harness import contour, mutation
from harness.cli import main

CHECK = ["python", "-c", "from calc import add; assert add(2, 2) == 4"]

CALC = '''"""Small arithmetic."""

# helpers below


def add(a, b):
    return a + b


def describe(value):
    return f"value={value}"
'''


AUDIT = """\
schema_version = 1
task = "t-mut"
check = ["python", "-c", "from calc import add; assert add(2, 2) == 4"]
why = "the sum is the behaviour this check exists for"

[[break]]
path = "calc.py"
old = "return a + b"
new = "return a - b"
why = "addition becomes subtraction"

[[break]]
path = "calc.py"
old = "# helpers below"
new = "# helpers, renamed"
why = "a comment the test does not read"

[[break]]
path = "calc.py"
old = "return a * b"
new = "return a / b"
why = "a line that is not in the tree"

[[break]]
path = "calc.py"
old = "return a + b"
new = "return a +"
why = "a break that does not compile"
"""


def git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "probe"
    project.mkdir()
    (project / "calc.py").write_text(CALC, encoding="utf-8")
    (project / ".trajan").mkdir()
    (project / ".trajan" / "policy.toml").write_text('name = "probe"\n', encoding="utf-8")
    git(project, "init", "-q")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return project


def write_audit(tmp_path: Path, body: str = AUDIT, name: str = "audit.toml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def make_report(project: Path, audit: Path, **kwargs: object) -> mutation.Report:
    root = contour.trajan_root()
    return mutation.run(
        project,
        mutation.read_audit(audit),
        root=root,
        python=contour.trajan_python(root),
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_break_is_noticed_and_a_comment_is_not(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    report = make_report(project, write_audit(tmp_path))
    assert [item.status for item in report.attempts] == [
        mutation.NOTICED,
        mutation.BLIND,
        mutation.NOT_APPLIED,
        mutation.INVALID,
    ], [(item.status, item.note) for item in report.attempts]
    assert "still passed" in report.attempts[1].note
    assert "does not compile" in report.attempts[3].note
    assert report.blind[0].break_.why == "a comment the test does not read"


def test_the_project_is_left_as_it_was(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = git(project, "status", "--porcelain")
    calc = (project / "calc.py").read_text(encoding="utf-8")
    make_report(project, write_audit(tmp_path))
    assert git(project, "status", "--porcelain") == before
    assert (project / "calc.py").read_text(encoding="utf-8") == calc
    assert len(git(project, "worktree", "list").splitlines()) == 1, "a worktree was left behind"


def test_a_check_that_is_already_red_cannot_answer(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "calc.py").write_text(CALC.replace("return a + b", "return a - b"), encoding="utf-8")
    with pytest.raises(boxes.HarnessError, match="red on the tree as it is"):
        make_report(project, write_audit(tmp_path))


def test_the_working_tree_is_what_is_audited(tmp_path: Path) -> None:
    """A clean commit is not the tree a dispatch judges: an untracked check must be seen."""
    project = make_project(tmp_path)
    (project / "check_sum.py").write_text(
        "from calc import add\n\nassert add(2, 2) == 4\n", encoding="utf-8"
    )
    audit = write_audit(
        tmp_path,
        AUDIT.replace(
            'check = ["python", "-c", "from calc import add; assert add(2, 2) == 4"]',
            'check = ["python", "check_sum.py"]',
        ),
    )
    report = make_report(project, audit)
    assert report.attempts[0].status == mutation.NOTICED


def test_the_limit_is_named_not_hidden(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    report = make_report(project, write_audit(tmp_path), limit=1)
    assert len(report.attempts) == 1
    assert report.left_out == 3


def test_the_command_reads_and_writes_nothing_but_the_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_project(tmp_path)
    box = boxes.new_box(tmp_path / "castra", project)
    audit = write_audit(tmp_path)
    before = git(project, "status", "--porcelain")
    assert (
        main(
            [
                "--castra",
                str(box.root.parent),
                "mutate",
                box.name,
                "--file",
                str(audit),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["noticed"] == 1 and payload["blind"] == 1
    assert git(project, "status", "--porcelain") == before
    record = Path(payload["record"])
    assert record.is_file()
    assert record.parent == box.root / "tabularium" / "mutatio"
    stored = json.loads(record.read_text(encoding="utf-8"))
    assert stored["break"][0]["status"] == mutation.NOTICED


def test_the_declaration_refuses_what_it_cannot_read(tmp_path: Path) -> None:
    with pytest.raises(boxes.HarnessError, match=r"no \[\[break\]\]"):
        mutation.read_audit(write_audit(tmp_path, 'schema_version = 1\ncheck = ["python", "-V"]\n'))
    with pytest.raises(boxes.HarnessError, match="unknown key"):
        mutation.read_audit(
            write_audit(
                tmp_path,
                'schema_version = 1\ncheck = ["python", "-V"]\ncolour = "red"\n'
                '[[break]]\npath = "calc.py"\nold = "a"\nnew = "b"\n',
            )
        )
    with pytest.raises(boxes.HarnessError, match="schema_version"):
        mutation.read_audit(write_audit(tmp_path, "schema_version = 9\ncheck = []\n"))
