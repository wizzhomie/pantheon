"""Traceability: which approved promise does this evidence belong to?

The states are the test. A promise is "covered" only when its checks ran and passed; a failed
check or a confirmed finding that cites it makes it "failed"; a check that did not run is
"not-run", not "covered"; and a promise with no check is "unchecked" — the gap the whole
project exists to make visible.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trajan.cli import main
from trajan.context import ContextItem, ProjectContext
from trajan.contracts.findings import Finding
from trajan.review import ProposedFinding, anchor, build_user_message, parse_diff
from trajan.verify import (
    CheckRun,
    VerificationRun,
    build_report,
    decide,
    render_markdown,
)
from trajan.verify.trace import build_trace, summarise, unlinked_findings

NOW = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)


def check(check_id: str = "tests", status: str = "passed") -> CheckRun:
    return CheckRun(check_id=check_id, name=check_id, status=status)  # type: ignore[arg-type]


def run(*checks: CheckRun) -> VerificationRun:
    return VerificationRun(
        run_id="20261123T060000-t",
        policy_name="t",
        policy_digest="d",
        started_at=NOW,
        checks=list(checks),
        planned=len(checks),
    )


def context(**items: list[ContextItem]) -> ProjectContext:
    return ProjectContext(**items)  # type: ignore[arg-type]


def finding(
    finding_id: str = "R1",
    *,
    status: str = "confirmed",
    severity: str = "major",
    requirement: str | None = None,
) -> Finding:
    return Finding(
        id=finding_id,
        title=finding_id,
        kind="defect",
        status=status,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        source="reviewer",
        requirement=requirement,
    )


def test_a_promise_with_passing_checks_is_covered() -> None:
    ctx = context(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    (row,) = build_trace(ctx, run(check()), [])

    assert row.status == "covered"
    assert row.checks == ["tests"]


def test_a_failed_check_fails_the_promise() -> None:
    ctx = context(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    (row,) = build_trace(ctx, run(check(status="failed")), [])

    assert row.status == "failed"


def test_a_confirmed_finding_fails_the_promise_it_cites() -> None:
    ctx = context(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    (row,) = build_trace(ctx, run(check()), [finding(requirement="R1")])

    assert row.status == "failed"
    assert row.findings == ["R1"]


def test_an_unconfirmed_link_does_not_fail_a_promise() -> None:
    ctx = context(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    (row,) = build_trace(ctx, run(check()), [finding(status="unverified", requirement="R1")])

    assert row.status == "covered"


def test_checks_that_did_not_run_are_not_a_pass() -> None:
    ctx = context(
        requirements=[ContextItem(id="R1", title="t", status="approved", checks=["tests"])]
    )

    (row,) = build_trace(ctx, run(check(status="skipped")), [])

    assert row.status == "not-run"


def test_a_promise_without_a_check_is_unchecked() -> None:
    ctx = context(requirements=[ContextItem(id="R1", title="t", status="approved")])

    (row,) = build_trace(ctx, run(check()), [])

    assert row.status == "unchecked"


def test_only_approved_promises_are_traced() -> None:
    ctx = context(
        requirements=[
            ContextItem(id="R1", title="agreed", status="approved", checks=["tests"]),
            ContextItem(id="R2", title="later", status="proposed", checks=["tests"]),
            ContextItem(id="R3", title="gone", status="superseded", checks=["tests"]),
        ]
    )

    rows = build_trace(ctx, run(check()), [])

    assert [row.id for row in rows] == ["R1"]


def test_invariants_are_traced_too() -> None:
    ctx = context(invariants=[ContextItem(id="I1", title="no shell", status="approved", checks=[])])

    (row,) = build_trace(ctx, run(check()), [])

    assert row.id == "I1"
    assert row.status == "unchecked"


def test_a_confirmed_finding_without_a_requirement_is_named() -> None:
    findings = [finding("R1"), finding("R2", requirement="R1")]

    assert [item.id for item in unlinked_findings(findings)] == ["R1"]


def test_the_summary_counts_each_state() -> None:
    ctx = context(
        requirements=[
            ContextItem(id="R1", title="a", status="approved", checks=["tests"]),
            ContextItem(id="R2", title="b", status="approved"),
        ]
    )

    rows = build_trace(ctx, run(check()), findings=[finding(requirement="R2")])

    assert summarise(rows) == "2 approved, 1 covered, 1 failed"


# --- the report --------------------------------------------------------------------------


def test_the_report_traces_promises_when_context_is_present() -> None:
    ctx = context(
        requirements=[
            ContextItem(id="R1", title="the verdict is code", status="approved", checks=["tests"]),
            ContextItem(id="R2", title="docs are honest", status="approved"),
        ]
    )
    record = run(check())
    verdict = decide(record)
    report = build_report(record, [], verdict, context=ctx)

    text = render_markdown(report)

    assert "## Traceability" in text
    assert "2 approved" in text
    assert "| **R1** — the verdict is code | covered | `tests` | — |" in text
    assert "| **R2** — docs are honest | unchecked | — | — |" in text


def test_a_report_without_context_has_no_traceability_section() -> None:
    report = build_report(run(check()), [], decide(run(check())))

    assert "## Traceability" not in render_markdown(report)


def test_a_verdict_reason_names_the_requirement() -> None:
    record = run(check())
    verdict = decide(record, [finding(severity="blocking", requirement="R1")])

    assert verdict.reasons[-1].detail == "R1: R1 (requirement R1)"


def test_confirmed_findings_without_a_link_are_named_in_the_report() -> None:
    ctx = context(requirements=[ContextItem(id="R1", title="t", status="approved")])
    record = run(check())
    verdict = decide(record, [finding()])
    report = build_report(record, [finding()], verdict, context=ctx)

    assert "Confirmed findings with no approved requirement: **R1**." in render_markdown(report)


# --- the reviewer ------------------------------------------------------------------------


DIFF = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1 +1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
)


def proposal(**overrides: object) -> ProposedFinding:
    base: dict[str, object] = {
        "file": "app.py",
        "line": 1,
        "quote": "x = 2",
        "title": "x is wrong",
        "severity": "major",
        "kind": "defect",
    }
    base.update(overrides)
    return ProposedFinding(**base)  # type: ignore[arg-type]


def test_the_prompt_lists_approved_requirements() -> None:
    text = build_user_message(
        parse_diff(DIFF), max_bytes=10_000, requirements=[("R1", "the verdict is code")]
    )

    assert "Approved requirements" in text
    assert "- R1: the verdict is code" in text


def test_a_link_to_an_approved_requirement_is_kept(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

    found = anchor(
        1, proposal(requirement="R1"), tmp_path, parse_diff(DIFF), approved={"R1"}
    )

    assert found.requirement == "R1"


def test_a_link_to_a_proposed_requirement_is_dropped(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

    found = anchor(
        1, proposal(requirement="R9"), tmp_path, parse_diff(DIFF), approved={"R1"}
    )

    assert found.requirement is None
    assert "not approved" in found.detail


def test_a_link_without_context_is_dropped(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

    found = anchor(1, proposal(requirement="R1"), tmp_path, parse_diff(DIFF))

    assert found.requirement is None
    assert "no project context" in found.detail


# --- the command -------------------------------------------------------------------------


def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".trajan").mkdir(parents=True)
    (root / ".trajan" / "policy.toml").write_text(
        'name = "p"\n[[checks]]\nid = "tests"\nargv = ["true"]\n', encoding="utf-8"
    )
    (root / ".trajan" / "context.toml").write_text(
        'schema_version = 1\n[[requirements]]\nid = "R1"\ntitle = "the verdict is code"\n'
        'status = "approved"\nchecks = ["tests"]\n',
        encoding="utf-8",
    )
    return root


def test_the_verify_command_prints_the_trace_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)

    code = main(["verify", "--repo", str(root), "--out", str(tmp_path / "run")])

    assert code == 0
    assert "traceability: 1 approved, 1 covered" in capsys.readouterr().out


def test_the_report_carries_the_context_it_used(tmp_path: Path) -> None:
    root = project(tmp_path)

    main(["verify", "--repo", str(root), "--out", str(tmp_path / "run")])

    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["context"]["requirements"][0]["id"] == "R1"
    assert "## Traceability" in (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
