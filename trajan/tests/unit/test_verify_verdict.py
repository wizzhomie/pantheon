"""The verdict layer: four answers, computed from evidence, never from a claim.

The tests are written as a table of situations because that is what a verdict is: a small,
exhaustive mapping from "what the run saw" to "what may be said". If a situation is missing
here, the verdict is guessing about it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trajan.cli import main
from trajan.verify import (
    Budget,
    CheckRun,
    CommandSpec,
    Finding,
    Location,
    VerificationPolicy,
    VerificationRun,
    build_plan,
    build_report,
    decide,
    findings_from_run,
    read_report,
    render_markdown,
    run_policy,
    write_report,
)

NOW = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)


def check(
    check_id: str = "tests",
    status: str = "passed",
    *,
    optional: bool = False,
    reason: str | None = None,
    exit_code: int | None = 0,
    stdout_path: str | None = None,
) -> CheckRun:
    return CheckRun(
        check_id=check_id,
        name=check_id,
        status=status,  # type: ignore[arg-type]
        optional=optional,
        reason=reason,
        exit_code=exit_code,
        stdout_path=stdout_path,
    )


def run(*checks: CheckRun, error: str | None = None, planned: int | None = None) -> VerificationRun:
    return VerificationRun(
        run_id="20261123T060000-test",
        policy_name="test",
        policy_digest="digest",
        started_at=NOW,
        finished_at=NOW,
        checks=list(checks),
        planned=len(checks) if planned is None else planned,
        error=error,
    )


def finding(
    finding_id: str = "F1",
    *,
    status: str = "confirmed",
    severity: str = "blocking",
    kind: str = "defect",
) -> Finding:
    return Finding(
        id=finding_id,
        title=f"{finding_id} title",
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        source="reviewer",
        location=Location(file="src/app.py", line=3, quote="return x"),
    )


def codes(verdict: object) -> list[str]:
    return [reason.code for reason in verdict.reasons]  # type: ignore[attr-defined]


def test_a_clean_run_passes() -> None:
    verdict = decide(run(check("tests"), check("lint")))

    assert verdict.status == "pass"
    assert verdict.headline == "PASS"
    assert verdict.shippable
    assert codes(verdict) == ["checks-passed"]


def test_a_failed_required_check_holds_the_release() -> None:
    verdict = decide(run(check("tests", "failed", reason="exit 1, expected 0")))

    assert verdict.status == "hold"
    assert verdict.headline == "HOLD"
    assert not verdict.shippable
    assert codes(verdict) == ["check-failed"]
    assert "tests" in verdict.reasons[0].detail


def test_a_timeout_holds_and_says_so() -> None:
    verdict = decide(run(check("slow", "timeout", reason="exceeded 2s")))

    assert verdict.status == "hold"
    assert codes(verdict) == ["check-timed-out"]


def test_a_required_check_that_could_not_run_is_inconclusive_not_a_failure() -> None:
    """'We could not check' is a different answer from 'the project is broken'."""
    verdict = decide(run(check("types", "error", reason="command not found: mypy")))

    assert verdict.status == "inconclusive"
    assert verdict.headline == "INCONCLUSIVE"
    assert not verdict.shippable
    assert codes(verdict) == ["check-could-not-run"]
    assert "mypy" in verdict.reasons[0].detail


def test_a_budget_skip_is_inconclusive() -> None:
    verdict = decide(run(check("tests", "skipped", reason="budget: no time left")))

    assert verdict.status == "inconclusive"
    assert codes(verdict) == ["check-skipped"]


def test_a_missing_tool_on_a_required_check_is_inconclusive() -> None:
    verdict = decide(run(check("types", "not_applicable", reason="mypy not found")))

    assert verdict.status == "inconclusive"
    assert codes(verdict) == ["check-not-applicable"]


def test_a_run_without_checks_is_inconclusive() -> None:
    verdict = decide(run())

    assert verdict.status == "inconclusive"
    assert codes(verdict) == ["nothing-to-verify"]


def test_a_policy_with_only_optional_checks_cannot_pass() -> None:
    """Nothing mandatory ran, so nothing could fail; calling that PASS would be a trick."""
    verdict = decide(run(check("lint", "passed", optional=True)))

    assert verdict.status == "inconclusive"
    assert codes(verdict) == ["nothing-to-verify"]


def test_a_broken_run_record_is_inconclusive() -> None:
    verdict = decide(run(check("tests"), error="could not create the run directory"))

    assert verdict.status == "inconclusive"
    assert codes(verdict) == ["run-failed"]


def test_a_confirmed_blocking_finding_holds_without_any_failed_check() -> None:
    """The reason findings exist separately from checks: a reviewer can hold the release."""
    verdict = decide(run(check("tests")), [finding("F1")])

    assert verdict.status == "hold"
    assert verdict.blocking_findings == ["F1"]
    assert codes(verdict) == ["blocking-finding"]


def test_a_failed_check_outranks_a_missing_one() -> None:
    """Knowing the project is broken is stronger than not knowing whether it is."""
    verdict = decide(
        run(
            check("tests", "failed", reason="exit 1"),
            check("types", "error", reason="command not found"),
        )
    )

    assert verdict.status == "hold"
    assert codes(verdict) == ["check-failed"]


def test_an_unconfirmed_claim_is_a_condition_not_a_hold() -> None:
    verdict = decide(run(check("tests")), [finding("F2", status="unverified")])

    assert verdict.status == "pass_with_conditions"
    assert verdict.headline == "PASS WITH CONDITIONS"
    assert verdict.shippable
    assert codes(verdict) == ["unresolved-finding"]


def test_an_ambiguous_case_is_a_condition() -> None:
    verdict = decide(run(check("tests")), [finding("F3", status="ambiguous")])

    assert verdict.status == "pass_with_conditions"
    assert codes(verdict) == ["unresolved-finding"]


def test_a_refuted_claim_changes_nothing() -> None:
    verdict = decide(run(check("tests")), [finding("F4", status="false-positive")])

    assert verdict.status == "pass"
    assert codes(verdict) == ["checks-passed"]


def test_a_non_blocking_confirmed_finding_is_a_condition() -> None:
    verdict = decide(run(check("tests")), [finding("F5", severity="minor")])

    assert verdict.status == "pass_with_conditions"
    assert codes(verdict) == ["non-blocking-finding"]


def test_a_failed_optional_check_is_a_condition_through_its_finding() -> None:
    failed = check("lint", "failed", optional=True, reason="exit 1")
    verdict = decide(run(check("tests"), failed), findings_from_run(run(check("tests"), failed)))

    assert verdict.status == "pass_with_conditions"
    assert codes(verdict) == ["non-blocking-finding"]


def test_the_verdict_is_deterministic() -> None:
    record = run(check("tests"), check("lint", "failed", optional=True, reason="exit 1"))
    findings = findings_from_run(record)

    assert decide(record, findings) == decide(record, findings)


def test_a_check_failure_becomes_a_confirmed_blocking_finding() -> None:
    record = run(check("tests", "failed", reason="exit 1", stdout_path="checks/tests.stdout.txt"))

    (found,) = findings_from_run(record)

    assert found.id == "check-tests"
    assert found.kind == "check-failure"
    assert found.status == "confirmed"
    assert found.severity == "blocking"
    assert found.source == "check"
    assert found.blocking
    assert found.evidence == ["tests", "checks/tests.stdout.txt"]
    assert "exit 1" in found.detail


def test_an_optional_failure_is_a_major_finding_not_a_blocker() -> None:
    (found,) = findings_from_run(run(check("lint", "failed", optional=True)))

    assert found.severity == "major"
    assert not found.blocking


def test_a_check_that_did_not_run_is_not_a_finding() -> None:
    """A gap says nothing about the project; the verdict reports it from the check status."""
    record = run(
        check("tests", "skipped", reason="budget"),
        check("types", "error", reason="command not found"),
        check("lint", "not_applicable", reason="ruff not found"),
    )

    assert findings_from_run(record) == []


def test_a_finding_without_a_location_is_valid() -> None:
    """A failed command has no line; requiring one would force a fabricated location."""
    found = Finding(
        id="C1", title="t", kind="check-failure", status="confirmed", severity="blocking",
        source="check",
    )

    assert found.location is None


# --- the report -------------------------------------------------------------------------


def report_and_run(tmp_path: Path) -> tuple[Path, VerificationRun, list[Finding], object]:
    record = run(check("tests"), check("lint", "failed", reason="exit 1", optional=True))
    findings = findings_from_run(record)
    verdict = decide(record, findings)
    report = build_report(record, findings, verdict)
    _json_path, md_path = write_report(report, tmp_path / "run")
    return md_path, record, findings, verdict


def test_the_report_is_a_pair_and_the_markdown_renders_the_json(tmp_path: Path) -> None:
    md_path, _, _, _ = report_and_run(tmp_path)

    parsed = read_report(md_path.with_name("report.json"))

    assert md_path.read_text(encoding="utf-8") == render_markdown(parsed)


def test_the_report_leads_with_the_verdict(tmp_path: Path) -> None:
    md_path, _, _, _ = report_and_run(tmp_path)
    text = md_path.read_text(encoding="utf-8")

    assert text.startswith("# Verification report — PASS WITH CONDITIONS")
    assert "| Verdict | **PASS WITH CONDITIONS** |" in text
    assert "## Why" in text
    assert "`non-blocking-finding`" in text


def test_the_report_lists_the_evidence_of_each_check(tmp_path: Path) -> None:
    md_path, _, _, _ = report_and_run(tmp_path)
    text = md_path.read_text(encoding="utf-8")

    assert "| `tests` | pass | 0 |" in text
    assert "| `lint` | fail (exit 1) | 0 |" in text


def test_the_report_says_what_was_not_checked() -> None:
    record = run(
        check("tests"),
        check("types", "error", reason="command not found: mypy"),
    )
    report = build_report(record, [], decide(record, []))

    text = render_markdown(report)

    assert "## Not checked" in text
    assert "`types`: error — command not found: mypy" in text


def test_a_report_with_no_findings_says_none_explicitly() -> None:
    record = run(check("tests"))

    text = render_markdown(build_report(record, [], decide(record, [])))

    assert "## Findings\n\nNone." in text


def test_the_report_round_trips_through_json(tmp_path: Path) -> None:
    record = run(check("tests"), check("lint", "failed", optional=True, reason="exit 1"))
    findings = findings_from_run(record)
    original = build_report(record, findings, decide(record, findings))

    json_path, _ = write_report(original, tmp_path)

    assert read_report(json_path) == original


# --- the command ------------------------------------------------------------------------


def repo_with(tmp_path: Path, source: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    policy = tmp_path / ".trajan" / "policy.toml"
    policy.parent.mkdir()
    policy.write_text(
        'name = "t"\n'
        "[[checks]]\n"
        'id = "probe"\n'
        f"argv = {json.dumps([sys.executable, '-c', source])}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_verify_command_writes_a_report_and_returns_zero_on_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with(tmp_path, "print('ok')")

    code = main(["verify", "--repo", str(repo), "--out", str(repo / "run")])

    assert code == 0
    report = read_report(repo / "run" / "report.json")
    assert report.verdict.status == "pass"
    assert "verdict: PASS" in capsys.readouterr().out


def test_the_verify_command_returns_one_on_hold(tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "raise SystemExit(1)")

    code = main(["verify", "--repo", str(repo), "--out", str(tmp_path / "run")])

    assert code == 1
    assert read_report(tmp_path / "run" / "report.json").verdict.status == "hold"


def test_the_verify_command_returns_two_when_it_cannot_answer(tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "print('ok')")
    policy = repo / ".trajan" / "policy.toml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            'name = "t"', 'name = "t"\nbudget = { max_seconds = 0.2, max_checks = 5 }'
        ),
        encoding="utf-8",
    )

    code = main(["verify", "--repo", str(repo), "--out", str(tmp_path / "run")])

    assert code == 2
    report = read_report(tmp_path / "run" / "report.json")
    assert report.verdict.status == "inconclusive"
    assert report.verdict.reasons[0].code == "check-skipped"


def test_the_verify_command_defaults_to_the_policy_in_the_project(
    tmp_path: Path,
) -> None:
    repo = repo_with(tmp_path, "print('ok')")

    code = main(["verify", "--repo", str(repo), "--out", str(tmp_path / "run")])

    assert code == 0


def test_the_verify_command_says_where_it_looked_for_a_missing_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["verify", "--repo", str(tmp_path)])

    assert code == 1
    assert "no policy at" in capsys.readouterr().err


def test_the_verify_dry_run_plans_without_spending(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with(tmp_path, "raise SystemExit(1)")

    code = main(["verify", "--repo", str(repo), "--dry-run"])

    assert code == 0
    assert "probe" in capsys.readouterr().out
    assert not (repo / ".trajan" / "runs").exists()


def test_a_verified_run_records_the_policy_and_the_profile(tmp_path: Path) -> None:
    repo = repo_with(tmp_path, "print('ok')")

    main(["verify", "--repo", str(repo), "--out", str(tmp_path / "run")])

    report = read_report(tmp_path / "run" / "report.json")
    assert report.run.policy_digest
    assert report.run.profile_digest
    assert report.schema_version == 1


def test_the_plan_sees_a_tool_the_project_does_not_have(tmp_path: Path) -> None:
    policy = VerificationPolicy(
        name="t",
        checks=[
            CommandSpec(
                id="types",
                argv=[sys.executable, "-c", "print('ok')"],
                requires="mypy",
            )
        ],
        budget=Budget(),
    )

    plan = build_plan(policy, None)

    assert plan[0].reason == "required"


def test_a_real_run_of_a_failing_command_is_recorded_as_evidence(tmp_path: Path) -> None:
    """End to end without the CLI: the artifact exists, and the verdict points at it."""
    policy = VerificationPolicy(
        name="t",
        checks=[
            CommandSpec(
                id="boom",
                argv=[sys.executable, "-c", "print('bad'); raise SystemExit(3)"],
            )
        ],
    )

    record, target = asyncio.run(run_policy(policy, tmp_path, out_dir=tmp_path / "run"))
    findings = findings_from_run(record)
    verdict = decide(record, findings)

    assert verdict.status == "hold"
    assert findings[0].evidence[1] == "checks/boom.stdout.txt"
    assert "bad" in (target / "checks" / "boom.stdout.txt").read_text(encoding="utf-8")


# --- the committed demo ------------------------------------------------------------------

REPORTS = Path(__file__).resolve().parents[2] / "reports"


def test_every_committed_verification_report_renders_its_markdown() -> None:
    """The pair rule applies to verification reports too: markdown is a rendering."""
    checked = 0
    for path in sorted(REPORTS.rglob("report.json")):
        assert path.with_suffix(".md").read_text(encoding="utf-8") == render_markdown(
            read_report(path)
        )
        checked += 1
    assert checked >= 1


def test_the_committed_demo_is_a_hold_with_its_evidence() -> None:
    report = read_report(REPORTS / "verification_demo" / "report.json")

    assert report.verdict.status == "hold"
    assert report.verdict.blocking_findings == ["check-tests"]
    (finding,) = report.findings
    assert finding.evidence == ["tests", "checks/tests.stdout.txt"]
    assert (REPORTS / "verification_demo" / "checks" / "tests.stdout.txt").is_file()


def test_a_report_rejects_duplicate_finding_ids(tmp_path: Path) -> None:
    record = run(check("tests"))
    duplicate = finding("F1")

    with pytest.raises(ValueError, match="unique"):
        build_report(record, [duplicate, duplicate], decide(record, [duplicate]))


def test_a_verdict_cannot_point_at_a_finding_the_report_does_not_contain() -> None:
    record = run(check("tests"))
    verdict = decide(record, [finding("F9")])

    with pytest.raises(ValueError, match="unknown findings"):
        build_report(record, [], verdict)
