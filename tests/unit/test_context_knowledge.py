"""Project knowledge: what a project promises, and which promises bind.

The tests are about the boundary ADR-004 draws: an approved promise is an obligation, a
proposed one is a direction, and a promise that names a check nobody has is a broken link
rather than a checked requirement. The last point is the one that matters in practice — a
declared requirement with no check is exactly the silent gap the whole project exists to make
visible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from qagent.cli import main
from qagent.context import (
    ContextItem,
    ProjectContext,
    check_context,
    read_context,
)
from qagent.verify import CommandSpec, VerificationPolicy

ROOT = Path(__file__).resolve().parents[2]

MINIMAL = '''\
schema_version = 1

[architecture]
baseline = "what exists"
target = "where it goes"

[[requirements]]
id = "R1"
title = "the verdict is code"
status = "approved"
checks = ["tests"]

[[invariants]]
id = "I1"
title = "no shell"
status = "proposed"

[[milestones]]
id = "M1"
title = "context in .qagent"
status = "proposed"
'''


def project(tmp_path: Path, body: str = MINIMAL) -> Path:
    (tmp_path / ".qagent").mkdir(exist_ok=True)
    (tmp_path / ".qagent" / "context.toml").write_text(body, encoding="utf-8")
    return tmp_path


def policy(*check_ids: str) -> VerificationPolicy:
    return VerificationPolicy(
        name="p",
        checks=[CommandSpec(id=check_id, argv=["true"]) for check_id in check_ids],
    )


def test_the_context_loads_and_counts_what_it_has(tmp_path: Path) -> None:
    context = read_context(project(tmp_path))

    assert context.summary() == "1 requirements, 1 invariants, 1 milestones (1 approved)"
    assert [item.id for item in context.approved()] == ["R1"]
    assert len(context.digest()) == 64


def test_the_architecture_baseline_and_target_are_kept(tmp_path: Path) -> None:
    context = read_context(project(tmp_path))

    assert context.architecture.baseline == "what exists"
    assert context.architecture.target == "where it goes"


def test_the_digest_changes_when_a_promise_changes(tmp_path: Path) -> None:
    first = read_context(project(tmp_path))
    changed = MINIMAL.replace('status = "approved"', 'status = "proposed"')
    second = read_context(project(tmp_path, changed))

    assert first.digest() != second.digest()


def test_the_digest_is_stable_for_the_same_file(tmp_path: Path) -> None:
    assert read_context(project(tmp_path)).digest() == read_context(project(tmp_path)).digest()


def test_a_duplicate_id_across_sections_is_refused(tmp_path: Path) -> None:
    body = MINIMAL + '\n[[invariants]]\nid = "R1"\ntitle = "same id"\nstatus = "proposed"\n'

    with pytest.raises(ValidationError, match="duplicate context ids"):
        read_context(project(tmp_path, body))


def test_a_milestone_with_a_check_is_refused(tmp_path: Path) -> None:
    body = MINIMAL.replace('id = "M1"\ntitle = "context in .qagent"',
                           'id = "M1"\ntitle = "context in .qagent"\nchecks = ["tests"]')

    with pytest.raises(ValidationError, match="milestone"):
        read_context(project(tmp_path, body))


def test_an_unknown_status_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        read_context(project(tmp_path, MINIMAL.replace('status = "approved"', 'status = "maybe"')))


def test_a_missing_context_file_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"context\.toml"):
        read_context(tmp_path)


def test_a_check_that_does_not_exist_is_an_error() -> None:
    context = ProjectContext(
        requirements=[
            ContextItem(id="R1", title="t", status="approved", checks=["nope"]),
        ]
    )

    (problem,) = check_context(context, policy())

    assert problem.level == "error"
    assert problem.code == "unknown-check"
    assert "nope" in problem.detail


def test_an_approved_promise_without_a_check_is_a_note() -> None:
    context = ProjectContext(
        requirements=[ContextItem(id="R1", title="docs are honest", status="approved")]
    )

    (problem,) = check_context(context, policy())

    assert problem.level == "note"
    assert problem.code == "approved-without-check"
    assert "R1" in problem.detail


def test_a_check_without_a_promise_is_a_note() -> None:
    context = ProjectContext(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    problems = check_context(context, policy("tests", "lint"))

    assert [problem.code for problem in problems] == ["check-without-item"]
    assert "lint" in problems[0].detail


def test_a_proposed_promise_does_not_need_a_check() -> None:
    context = ProjectContext(
        requirements=[ContextItem(id="R1", title="later", status="proposed", checks=[])]
    )

    assert check_context(context, policy()) == []


def test_without_a_policy_the_links_are_not_judged() -> None:
    context = ProjectContext(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    (problem,) = check_context(context, None)

    assert problem.code == "no-policy"
    assert problem.level == "note"


def test_this_repository_context_is_valid() -> None:
    """The project's own promises must load and must not point at checks that do not exist."""
    from qagent.verify import read_policy

    context = read_context(ROOT)
    problems = check_context(context, read_policy(ROOT / ".qagent" / "policy.toml"))

    assert context.requirements
    assert [problem for problem in problems if problem.level == "error"] == []


# --- the command -------------------------------------------------------------------------


def test_the_context_command_prints_the_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["context", "--repo", str(project(tmp_path))])

    out = capsys.readouterr().out
    assert code == 0
    assert "1 requirements, 1 invariants, 1 milestones" in out
    assert "R1 (approved) the verdict is code" in out


def test_the_context_command_can_print_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["context", "--repo", str(project(tmp_path)), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requirements"][0]["id"] == "R1"


def test_the_context_check_returns_zero_for_notes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = project(tmp_path, MINIMAL.replace('checks = ["tests"]\n', ""))
    (target / ".qagent" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "tests"\nargv = ["true"]\n', encoding="utf-8"
    )

    code = main(["context", "--repo", str(target), "--check"])

    assert code == 0
    assert "note: R1 is approved but nothing checks it" in capsys.readouterr().out


def test_the_context_check_returns_one_for_a_broken_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = MINIMAL.replace('checks = ["tests"]', 'checks = ["nope"]')
    target = project(tmp_path, body)
    (target / ".qagent" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "tests"\nargv = ["true"]\n', encoding="utf-8"
    )

    code = main(["context", "--repo", str(target), "--check"])

    assert code == 1
    assert "error: R1 names check 'nope'" in capsys.readouterr().out


def test_the_context_command_explains_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["context", "--repo", str(tmp_path)])

    assert code == 1
    assert "no project context at" in capsys.readouterr().err
