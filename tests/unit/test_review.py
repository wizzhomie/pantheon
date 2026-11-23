"""The reviewer: a model proposes, the repository decides.

Two families of tests, on purpose. The parser and the prompt are tested as data — a diff is
a document, and its line numbers are the only thing a citation can rest on. The reviewer is
tested as a pipeline: every proposal is put against a real file on disk, and the interesting
cases are the ones where the model is wrong.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from qagent.cli import main
from qagent.contracts.findings import Finding
from qagent.llm.base import LLMResponse
from qagent.llm.errors import LLMError
from qagent.llm.fake import FakeClient
from qagent.review import (
    Diff,
    ProposedFinding,
    ReviewerConfig,
    ReviewOutcome,
    anchor,
    build_user_message,
    parse_diff,
    review_diff,
)
from qagent.verify import VerificationRequest
from qagent.verify.report import _request_section, _review_section

MODIFIED_DIFF = (
    "diff --git a/app.py b/app.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -18,4 +18,4 @@ class Item:\n"
    " \n"
    " def total(items):\n"
    '     """Sum the prices."""\n'
    "-    return sum(item.price * item.quantity for item in items)\n"
    "+    return sum(item.price for item in items)\n"
)

APP = '''\
"""A tiny invoice total, with one planted defect for the verification demo.

The defect is the kind an AI agent ships confidently: the happy path (one of each item) is
right, the arithmetic for quantities is not, and nothing in the code *looks* wrong.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    name: str
    price: float
    quantity: int = 1


def total(items: list[Item]) -> float:
    """Sum the prices of the items on an invoice."""
    return sum(item.price for item in items)
'''

THE_QUOTE = "return sum(item.price for item in items)"


def config(**overrides: object) -> ReviewerConfig:
    base: dict[str, object] = {"model": "reviewer", "max_tokens": 800}
    base.update(overrides)
    return ReviewerConfig(**base)  # type: ignore[arg-type]


def proposal(**overrides: object) -> ProposedFinding:
    base: dict[str, object] = {
        "file": "app.py",
        "line": 21,
        "quote": THE_QUOTE,
        "title": "total ignores quantity",
        "severity": "blocking",
        "kind": "defect",
        "detail": "an item with quantity 3 is billed once",
    }
    base.update(overrides)
    return ProposedFinding(**base)  # type: ignore[arg-type]


def answer(*findings: object, summary: str = "a change") -> str:
    return json.dumps({"summary": summary, "findings": list(findings)})


def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(APP, encoding="utf-8")
    return tmp_path


def the_diff() -> Diff:
    return parse_diff(MODIFIED_DIFF)


def anchored(tmp_path: Path, **overrides: object) -> Finding:
    return anchor(1, proposal(**overrides), repo(tmp_path), the_diff())


# --- the diff parser ---------------------------------------------------------------------


def test_the_parser_numbers_the_new_file_and_marks_added_lines() -> None:
    (change,) = the_diff().files

    assert change.path == "app.py"
    assert change.status == "modified"
    assert change.removed == 1
    assert [(line.number, line.text, line.added) for line in change.lines] == [
        (18, "", False),
        (19, "def total(items):", False),
        (20, '    """Sum the prices."""', False),
        (21, f"    {THE_QUOTE}", True),
    ]


def test_a_new_file_is_marked_as_added() -> None:
    parsed = parse_diff(
        "diff --git a/x.py b/x.py\nnew file mode 100644\n--- /dev/null\n+++ b/x.py\n"
        "@@ -0,0 +1,2 @@\n+one\n+two\n"
    )

    (change,) = parsed.files
    assert change.status == "added"
    assert [line.text for line in change.lines] == ["one", "two"]


def test_a_deleted_file_keeps_the_removed_count_and_no_lines() -> None:
    parsed = parse_diff(
        "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-one\n-two\n"
    )

    (change,) = parsed.files
    assert change.status == "deleted"
    assert change.removed == 2
    assert change.lines == []


def test_a_binary_file_has_no_lines_to_cite() -> None:
    parsed = parse_diff(
        "diff --git a/logo.png b/logo.png\nindex 1..2 100644\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )

    (change,) = parsed.files
    assert change.lines == []


def test_a_path_that_climbs_out_of_the_project_is_dropped() -> None:
    parsed = parse_diff("diff --git a/../secret b/../secret\n--- a/../secret\n+++ b/../secret\n")

    assert parsed.files == []


def test_the_parser_stops_at_the_file_ceiling_and_says_so() -> None:
    text = "".join(
        f"diff --git a/f{index}.py b/f{index}.py\n--- a/f{index}.py\n+++ b/f{index}.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
        for index in range(5)
    )

    parsed = parse_diff(text, max_files=2)

    assert parsed.truncated
    assert len(parsed.files) == 2


def test_the_parser_stops_at_the_byte_ceiling_and_says_so() -> None:
    assert parse_diff(MODIFIED_DIFF, max_bytes=60).truncated


def test_the_diff_digest_is_stable() -> None:
    assert the_diff().digest() == the_diff().digest()


def test_the_prompt_shows_numbered_lines_and_a_marker() -> None:
    text = build_user_message(the_diff(), max_bytes=10_000)

    assert "Changed files: 1" in text
    assert "--- app.py (modified, removed 1)" in text
    assert f"   21 +     {THE_QUOTE}" in text


def test_the_prompt_stops_at_its_byte_budget() -> None:
    text = build_user_message(the_diff(), max_bytes=40)

    assert "not shown, the diff budget ran out" in text


# --- the anchor --------------------------------------------------------------------------


def test_a_quote_on_its_line_is_confirmed(tmp_path: Path) -> None:
    found = anchor(1, proposal(), repo(tmp_path), the_diff())

    assert found.id == "F1"
    assert found.status == "confirmed"
    assert found.source == "reviewer"
    assert found.blocking
    assert found.location is not None
    assert found.location.line == 21


def test_a_quote_with_different_indentation_still_matches(tmp_path: Path) -> None:
    found = anchored(tmp_path, quote="return   sum(item.price for item in items)")

    assert found.status == "confirmed"


def test_a_line_outside_the_file_is_unverified_and_says_why(tmp_path: Path) -> None:
    found = anchored(tmp_path, line=999)

    assert found.status == "unverified"
    assert not found.blocking
    assert "outside the file" in found.detail


def test_a_real_line_with_the_wrong_quote_is_unverified(tmp_path: Path) -> None:
    found = anchored(tmp_path, quote="return 42")

    assert found.status == "unverified"
    assert "not in the file" in found.detail


def test_a_quote_from_another_line_says_which_line(tmp_path: Path) -> None:
    found = anchored(tmp_path, line=19)

    assert found.status == "unverified"
    assert "line 21" in found.detail


def test_a_file_outside_the_change_cannot_be_confirmed(tmp_path: Path) -> None:
    (tmp_path / "other.py").write_text("x = 1\n", encoding="utf-8")

    found = anchored(tmp_path, file="other.py", line=1, quote="x = 1")

    assert found.status == "unverified"
    assert "not part of the change" in found.detail


def test_a_path_that_climbs_cannot_be_confirmed(tmp_path: Path) -> None:
    found = anchored(tmp_path, file="../etc/passwd", line=1, quote="root")

    assert found.status == "unverified"
    assert "outside the project" in found.detail


def test_a_risk_cannot_block_even_when_the_model_says_so(tmp_path: Path) -> None:
    found = anchored(tmp_path, severity="blocking", kind="risk")

    assert found.severity == "major"
    assert "severity lowered" in found.detail


# --- the reviewer ------------------------------------------------------------------------


def run_review(tmp_path: Path, response: str | BaseException, **overrides: object) -> ReviewOutcome:
    client = FakeClient([response])
    return asyncio.run(review_diff(the_diff(), repo(tmp_path), client, config(**overrides)))


def test_an_empty_answer_is_a_valid_review(tmp_path: Path) -> None:
    outcome = run_review(tmp_path, answer())

    assert outcome.status == "ok"
    assert outcome.proposed == 0
    assert outcome.findings == []


def test_a_confirmed_finding_is_counted(tmp_path: Path) -> None:
    outcome = run_review(tmp_path, answer(proposal().model_dump(mode="json")))

    assert outcome.status == "ok"
    assert outcome.proposed == 1
    assert outcome.confirmed == 1
    assert outcome.unverified == 0
    assert outcome.findings[0].status == "confirmed"


def test_a_model_error_is_an_outcome_not_an_exception(tmp_path: Path) -> None:
    outcome = run_review(tmp_path, LLMError("provider is down"))

    assert outcome.status == "failed"
    assert "provider is down" in (outcome.error or "")


def test_an_unparseable_answer_is_a_failed_review(tmp_path: Path) -> None:
    outcome = run_review(tmp_path, "I think the code is fine, actually.")

    assert outcome.status == "failed"
    assert "unparseable" in (outcome.error or "")


def test_fences_around_the_json_are_tolerated(tmp_path: Path) -> None:
    assert run_review(tmp_path, f"```json\n{answer()}\n```").status == "ok"


def test_the_request_carries_the_project_rules(tmp_path: Path) -> None:
    client = FakeClient([answer()])
    asyncio.run(
        review_diff(the_diff(), repo(tmp_path), client, config(instructions="never touch i18n"))
    )

    system = client.requests[0].messages[0]
    assert system.role == "system"
    assert "never touch i18n" in system.content


# --- the second look ---------------------------------------------------------------------


def look(answer_text: str) -> str:
    """The second reader's answer: one verdict per claim."""
    return json.dumps(
        {"verdicts": [{"index": 1, "keep": False, "reason": answer_text}]}, ensure_ascii=False
    )


def run_second_look(tmp_path: Path, first: str, second: str | BaseException) -> ReviewOutcome:
    client = FakeClient([first, second])
    return asyncio.run(
        review_diff(
            the_diff(), repo(tmp_path), client, config(second_look=True)
        )
    )


def test_the_second_look_drops_a_claim_the_code_denies(tmp_path: Path) -> None:
    outcome = run_second_look(
        tmp_path,
        answer(proposal().model_dump(mode="json")),
        look("the assertion holds, so the test is right"),
    )

    assert outcome.status == "ok"
    assert outcome.second_look == "ok"
    assert outcome.refuted == 1
    assert (outcome.proposed, outcome.confirmed) == (1, 0)
    (finding,) = outcome.findings
    assert finding.status == "false-positive"
    assert "second look: the assertion holds" in finding.detail


def test_a_dropped_claim_cannot_hold_a_release(tmp_path: Path) -> None:
    outcome = run_second_look(
        tmp_path,
        answer(proposal().model_dump(mode="json")),
        look("the claim contradicts itself"),
    )

    assert not outcome.findings[0].blocking


def test_a_second_look_that_cannot_answer_leaves_the_claims_standing(tmp_path: Path) -> None:
    outcome = run_second_look(
        tmp_path, answer(proposal().model_dump(mode="json")), LLMError("the reader is down")
    )

    assert outcome.second_look == "failed"
    assert outcome.refuted == 0
    assert outcome.findings[0].status == "confirmed"
    assert outcome.confirmed == 1


def test_the_second_look_is_not_asked_when_there_is_nothing_to_check(tmp_path: Path) -> None:
    client = FakeClient([answer()])
    outcome = asyncio.run(
        review_diff(the_diff(), repo(tmp_path), client, config(second_look=True))
    )

    assert outcome.second_look == "off"
    assert len(client.requests) == 1


def test_the_second_look_is_off_by_default(tmp_path: Path) -> None:
    client = FakeClient([answer(proposal().model_dump(mode="json"))])
    outcome = asyncio.run(review_diff(the_diff(), repo(tmp_path), client, config()))

    assert outcome.second_look == "off"
    assert len(client.requests) == 1
    assert outcome.findings[0].status == "confirmed"


def test_the_report_says_how_many_claims_the_second_look_dropped() -> None:
    outcome = ReviewOutcome(
        status="ok", model="reviewer", second_look="ok", refuted=2, confirmed=1
    )

    section = _review_section(outcome)

    assert "| Second look | 2 claim(s) dropped |" in section
    assert "false-positive" in section


def test_a_failed_second_look_says_the_claims_stand() -> None:
    outcome = ReviewOutcome(status="ok", model="reviewer", second_look="failed")

    assert "the reader did not answer, the claims stand" in _review_section(outcome)


def test_a_review_without_the_second_look_says_nothing_about_it() -> None:
    assert "Second look" not in _review_section(ReviewOutcome(status="ok", model="reviewer"))


def test_the_request_table_records_the_second_look() -> None:
    request = VerificationRequest(
        policy="p.toml", diff="change.diff", reviewer="m:f", second_look=True
    )

    assert "| Second look | on: one more call checks every claim |" in _request_section(request)
    assert "Second look" not in _request_section(request.model_copy(update={"second_look": False}))


def test_the_second_look_pays_for_both_calls(tmp_path: Path) -> None:
    first = LLMResponse(
        text=answer(proposal().model_dump(mode="json")),
        model="reviewer",
        prompt_tokens=100,
        completion_tokens=20,
    )
    second = LLMResponse(
        text=look("dropped"), model="reviewer", prompt_tokens=120, completion_tokens=10
    )
    outcome = asyncio.run(
        review_diff(
            the_diff(), repo(tmp_path), FakeClient([first, second]), config(second_look=True)
        )
    )

    assert (outcome.prompt_tokens, outcome.completion_tokens) == (220, 30)


# --- the command -------------------------------------------------------------------------


def temp_project(tmp_path: Path, response: str | None = None) -> Path:
    """A project whose one check passes, so only the reviewer can move the verdict."""
    (tmp_path / "project").mkdir()
    project = repo(tmp_path / "project")
    (project / ".qagent").mkdir()
    (project / ".qagent" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "probe"\n'
        f'argv = {json.dumps([sys.executable, "-c", "print(1)"])}\n',
        encoding="utf-8",
    )
    (project / "change.diff").write_text(MODIFIED_DIFF, encoding="utf-8")
    if response is not None:
        (project / "answer.json").write_text(response, encoding="utf-8")
    return project


def run_verify(project: Path, capsys: pytest.CaptureFixture[str]) -> int:
    return main(
        [
            "verify",
            "--repo",
            str(project),
            "--diff",
            str(project / "change.diff"),
            "--reviewer",
            "qagent.review.scripted:from_env",
            "--out",
            str(project / "run"),
        ]
    )


def test_a_confirmed_reviewer_finding_holds_a_green_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = temp_project(tmp_path)
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", answer(proposal().model_dump(mode="json")))

    code = run_verify(project, capsys)

    assert code == 1
    assert "review: 1 proposed, 1 confirmed" in capsys.readouterr().out
    report = json.loads((project / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"]["status"] == "hold"
    assert report["verdict"]["blocking_findings"] == ["F1"]
    assert report["review"]["confirmed"] == 1
    markdown = (project / "run" / "report.md").read_text(encoding="utf-8")
    assert "## Review" in markdown
    assert "## Reviewer evidence" in markdown
    assert f"> {THE_QUOTE}" in markdown


def test_a_failed_review_makes_the_run_inconclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = temp_project(tmp_path)
    monkeypatch.delenv("QAGENT_REVIEW_RESPONSE", raising=False)

    code = run_verify(project, capsys)

    assert code == 1  # the factory refuses to build a client; that is a command error
    assert "QAGENT_REVIEW_RESPONSE" in capsys.readouterr().err


def test_a_review_that_returns_garbage_is_inconclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = temp_project(tmp_path)
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", "not json at all")

    code = run_verify(project, capsys)

    assert code == 2
    report = json.loads((project / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"]["status"] == "inconclusive"
    assert report["verdict"]["reasons"][0]["code"] == "review-failed"
    assert "reviewer: failed" in (project / "run" / "report.md").read_text(encoding="utf-8")


def test_an_unverified_finding_is_only_a_condition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = temp_project(tmp_path)
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", answer(proposal(line=999).model_dump(mode="json")))

    code = run_verify(project, capsys)

    assert code == 0
    report = json.loads((project / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"]["status"] == "pass_with_conditions"
    assert report["findings"][0]["status"] == "unverified"


def test_a_diff_without_a_reviewer_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = temp_project(tmp_path)

    code = main(["verify", "--repo", str(project), "--diff", str(project / "change.diff")])

    assert code == 1
    assert "--reviewer" in capsys.readouterr().err


def test_the_command_records_the_second_look_in_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = temp_project(tmp_path)
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", answer(proposal().model_dump(mode="json")))

    code = main(
        [
            "verify",
            "--repo",
            str(project),
            "--diff",
            str(project / "change.diff"),
            "--reviewer",
            "qagent.review.scripted:from_env",
            "--second-look",
            "--out",
            str(project / "run"),
        ]
    )

    report = json.loads((project / "run" / "report.json").read_text(encoding="utf-8"))
    assert code == 1
    assert report["request"]["second_look"] is True
    assert report["review"]["second_look"] == "failed"
    assert report["review"]["refuted"] == 0
    assert report["review"]["findings"][0]["status"] == "confirmed"
    assert "second look failed" in capsys.readouterr().out


def test_the_dry_run_does_not_need_a_key_or_a_model(tmp_path: Path) -> None:
    project = temp_project(tmp_path)

    code = main(
        [
            "verify",
            "--repo",
            str(project),
            "--diff",
            str(project / "change.diff"),
            "--reviewer",
            "qagent.review.factory:from_env",
            "--dry-run",
        ]
    )

    assert code == 0


def test_since_reads_the_change_from_git(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = temp_project(tmp_path)
    git = ["git", "-C", str(project)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [*git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], check=True
    )
    (project / "app.py").write_text(APP + "\nEXTRA = 1\n", encoding="utf-8")
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", answer())

    code = main(
        [
            "verify",
            "--repo",
            str(project),
            "--since",
            "HEAD",
            "--reviewer",
            "qagent.review.scripted:from_env",
            "--out",
            str(project / "run"),
        ]
    )

    assert code == 0
    report = json.loads((project / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["review"]["files_reviewed"] == 1


def test_the_byte_ceiling_counts_bytes_not_characters() -> None:
    """A UTF-8 diff is bigger on the wire than ``len(text)``; the prompt budget is bytes."""
    text = "diff --git a/ф.py b/ф.py\n--- a/ф.py\n+++ b/ф.py\n@@ -1 +1 @@\n-я\n+я\n" + "я" * 200

    assert parse_diff(text, max_bytes=300).truncated


def test_a_path_with_a_dot_prefix_still_anchors(tmp_path: Path) -> None:
    found = anchored(tmp_path, file="./app.py")

    assert found.status == "confirmed"


def test_a_line_the_diff_never_showed_cannot_be_confirmed(tmp_path: Path) -> None:
    """The reviewer may only cite what it was shown; the docstring is not in the diff."""
    found = anchored(tmp_path, line=1, quote='"""A tiny invoice total, with one planted defect')

    assert found.status == "unverified"
    assert "not part of the change" in found.detail
