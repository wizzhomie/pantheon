"""The orchestrator: a request, a run, and an explanation that does not run anything.

The last property is the one worth testing hardest. "Show me what happened" is a read; if it
silently executes a check or a model call, the explanation is a new experiment and the old
verdict is not what a reader gets.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from trajan.cli import main
from trajan.orchestrator import (
    _look_summary,
    execute,
    plan_lines,
    read_request,
    render_show,
    show,
)
from trajan.review.contracts import ReviewOutcome
from trajan.verify import VerificationRequest


def request_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "request.toml"
    path.write_text(body, encoding="utf-8")
    return path


def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".trajan").mkdir()
    (root / ".trajan" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "probe"\n'
        f'argv = {json.dumps([sys.executable, "-c", "print(1)"])}\n',
        encoding="utf-8",
    )
    (root / "diff.patch").write_text(
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")
    return root


def test_a_request_is_read_from_toml(tmp_path: Path) -> None:
    path = request_file(
        tmp_path,
        'name = "review the change"\n'
        'policy = ".trajan/policy.toml"\n'
        'diff = "diff.patch"\n'
        'reviewer = "trajan.review.scripted:from_env"\n',
    )

    request = read_request(path)

    assert request.name == "review the change"
    assert request.needs_review
    assert request.describe().startswith(
        "review the change · .trajan/policy.toml · diff diff.patch"
    )


def test_a_missing_request_file_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"request\.toml"):
        read_request(tmp_path / "request.toml")


def test_a_request_cannot_review_two_changes_at_once() -> None:
    with pytest.raises(ValidationError, match="not both"):
        VerificationRequest(diff="a.patch", since="HEAD", reviewer="m:f")


def test_a_request_without_a_reviewer_cannot_read_a_change() -> None:
    with pytest.raises(ValidationError, match="needs a reviewer"):
        VerificationRequest(diff="a.patch")


def test_a_reviewer_without_a_change_has_nothing_to_read() -> None:
    with pytest.raises(ValidationError, match="nothing to read"):
        VerificationRequest(reviewer="m:f")


def test_the_dry_run_shows_the_plan_and_the_review(tmp_path: Path) -> None:
    root = project(tmp_path)
    request = VerificationRequest(
        policy=".trajan/policy.toml", diff="diff.patch", reviewer="trajan.review.scripted:from_env"
    )

    lines = plan_lines(request, root)

    assert any("probe" in line for line in lines)
    assert any("review: 1 file(s)" in line for line in lines)


def test_the_dry_run_says_the_second_look_costs_another_call(tmp_path: Path) -> None:
    root = project(tmp_path)
    request = VerificationRequest(
        policy=".trajan/policy.toml",
        diff="diff.patch",
        reviewer="trajan.review.scripted:from_env",
        second_look=True,
    )

    lines = plan_lines(request, root)

    assert any("second look: on" in line for line in lines)
    assert not any("second look: on" in line for line in plan_lines(request.model_copy(
        update={"second_look": False}
    ), root))


def test_show_says_what_the_second_reader_did() -> None:
    assert _look_summary(ReviewOutcome(status="ok", second_look="ok", refuted=2)) == (
        ", 2 dropped by the second look"
    )
    assert "failed" in _look_summary(ReviewOutcome(status="ok", second_look="failed"))
    assert _look_summary(ReviewOutcome(status="ok")) == ""


def test_execute_stores_the_request_and_the_report(tmp_path: Path) -> None:
    root = project(tmp_path)
    request = VerificationRequest(policy=".trajan/policy.toml", name="probe run")

    report, target = asyncio.run(execute(request, root))

    assert report.verdict.status == "pass"
    stored = json.loads((target / "request.json").read_text(encoding="utf-8"))
    assert stored["name"] == "probe run"
    assert (target / "report.json").is_file()
    assert "## Request" in (target / "report.md").read_text(encoding="utf-8")


def test_show_explains_without_running_anything(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    root = project(tmp_path)
    script = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    (root / ".trajan" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "probe"\n'
        f"argv = {json.dumps([sys.executable, '-c', script])}\n",
        encoding="utf-8",
    )
    _, target = asyncio.run(execute(VerificationRequest(), root))
    marker.unlink()

    report, path = show(target)

    assert path.name == "report.json"
    assert not marker.exists()
    assert "PASS" in render_show(report)


def test_show_accepts_the_report_file_directly(tmp_path: Path) -> None:
    root = project(tmp_path)
    _, target = asyncio.run(execute(VerificationRequest(), root))

    report, path = show(target / "report.json")

    assert report.verdict.status == "pass"
    assert path.name == "report.json"


def test_show_reports_a_missing_run(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no report at"):
        show(tmp_path / "nope")


def test_the_verify_command_accepts_a_request_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    monkeypatch.setenv("TRAJAN_REVIEW_RESPONSE", '{"summary": "ok", "findings": []}')
    path = request_file(
        tmp_path,
        'name = "scripted review"\n'
        'policy = ".trajan/policy.toml"\n'
        'diff = "diff.patch"\n'
        'reviewer = "trajan.review.scripted:from_env"\n',
    )

    code = main(["verify", "--repo", str(root), "--request", str(path), "--out", str(root / "run")])

    assert code == 0
    report = json.loads((root / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["request"]["name"] == "scripted review"
    assert report["review"]["status"] == "ok"


def test_a_request_file_and_flags_cannot_disagree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    path = request_file(tmp_path, 'name = "x"\npolicy = ".trajan/policy.toml"\n')

    code = main(
        [
            "verify",
            "--repo",
            str(root),
            "--request",
            str(path),
            "--diff",
            str(root / "diff.patch"),
        ]
    )

    assert code == 1
    assert "--request already names" in capsys.readouterr().err


def test_the_show_command_prints_the_verdict_and_the_reasons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    _, target = asyncio.run(execute(VerificationRequest(), root))

    code = main(["show", str(target)])

    out = capsys.readouterr().out
    assert code == 0
    assert "PASS:" in out
    assert "checks-passed" in out
    assert "report:" in out


def test_the_show_command_returns_the_verdict_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    (root / ".trajan" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "probe"\n'
        f'argv = {json.dumps([sys.executable, "-c", "raise SystemExit(1)"])}\n',
        encoding="utf-8",
    )
    _, target = asyncio.run(execute(VerificationRequest(), root))

    code = main(["show", str(target)])

    assert code == 1
    assert "HOLD:" in capsys.readouterr().out


def test_the_show_command_can_print_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    _, target = asyncio.run(execute(VerificationRequest(), root))

    code = main(["show", str(target), "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"]["status"] == "pass"


def test_the_show_command_explains_a_missing_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["show", str(tmp_path / "nope")])

    assert code == 1
    assert "no report at" in capsys.readouterr().err
