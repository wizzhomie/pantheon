"""The plan under a change: which checks this change needs, and why the rest were dropped.

Risk here is not a score a model invents. A check declares the paths it guards, the change
declares the paths it touches, and the plan is the intersection — deterministic, reviewable,
and explainable to the reader who asks why the migration gate did not run. The dangerous
direction is silence: a gate that disappears must be visible as a decision, never as a gap.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qagent.context import ProjectContext
from qagent.context.knowledge import ContextItem
from qagent.orchestrator import changed_paths, plan_lines
from qagent.review import Diff, parse_diff
from qagent.verify import (
    Budget,
    CheckRun,
    CommandSpec,
    VerificationPolicy,
    VerificationRequest,
    VerificationRun,
    build_plan,
    build_report,
    decide,
    render_markdown,
    run_policy,
)
from qagent.verify.trace import build_trace, summarise

NOW = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)


def code(source: str = "pass") -> list[str]:
    return [sys.executable, "-c", source]


def spec(
    check_id: str = "tests", *, paths: list[str] | None = None, **kwargs: object
) -> CommandSpec:
    return CommandSpec(
        id=check_id,
        argv=code(),
        when_paths=paths or [],
        **kwargs,  # type: ignore[arg-type]
    )


def policy(*checks: CommandSpec) -> VerificationPolicy:
    return VerificationPolicy(name="demo", checks=list(checks), budget=Budget())


def reason(policy_: VerificationPolicy, changed: list[str] | None, check_id: str = "sql") -> str:
    entries = build_plan(policy_, changed=changed)
    (entry,) = [item for item in entries if item.check.id == check_id]
    return entry.reason


# --- selection ---------------------------------------------------------------------------


def test_a_check_without_selectors_is_needed_every_time() -> None:
    assert reason(policy(spec()), None, "tests") == "required"
    assert reason(policy(spec()), ["docs/readme.md"], "tests") == "required"


def test_a_path_scoped_gate_is_selected_by_a_matching_change() -> None:
    plan = policy(spec("sql", paths=["db/**"]))

    assert reason(plan, ["db/migrations/001.sql"], "sql") == "required"
    assert reason(plan, ["docs/readme.md"], "sql") == "not-selected"


def test_no_change_plans_everything() -> None:
    """An unknown change may touch anything: a cheap plan that drops a gate is not cheap."""
    assert reason(policy(spec("sql", paths=["db/**"])), None, "sql") == "required"


def test_a_star_stays_inside_one_segment() -> None:
    plan = policy(spec("docs", paths=["docs/*.md"]))

    assert reason(plan, ["docs/readme.md"], "docs") == "required"
    assert reason(plan, ["docs/deep/readme.md"], "docs") == "not-selected"


def test_a_double_star_crosses_segments() -> None:
    plan = policy(spec("sql", paths=["db/**"]))

    assert reason(plan, ["db/2026/11/001.sql"], "sql") == "required"


def test_a_selector_without_a_slash_matches_the_file_name() -> None:
    plan = policy(spec("sql", paths=["*.sql"]))

    assert reason(plan, ["db/migrations/001.sql"], "sql") == "required"
    assert reason(plan, ["db/README.md"], "sql") == "not-selected"


def test_windows_separators_do_not_hide_a_match() -> None:
    plan = policy(spec("sql", paths=["db/**"]))

    assert reason(plan, ["db\\\\001.sql"], "sql") == "required"


def test_a_selector_cannot_climb_out_of_the_project() -> None:
    with pytest.raises(ValueError):
        spec("sql", paths=["../secrets/*"])
    with pytest.raises(ValueError):
        spec("sql", paths=["/etc/**"])


def test_a_missing_tool_is_still_not_applicable_before_selection() -> None:
    from qagent.context import ProjectProfile

    plan = build_plan(
        policy(spec("sql", paths=["db/**"], requires="psql")),
        ProjectProfile(name="demo"),
        changed=["docs/readme.md"],
    )

    (entry,) = plan
    assert entry.reason == "tool-missing"


# --- the run -----------------------------------------------------------------------------


def run_with(*checks: CheckRun) -> VerificationRun:
    return VerificationRun(
        run_id="r1",
        policy_name="demo",
        policy_digest="0" * 64,
        started_at=NOW,
        finished_at=NOW,
        checks=list(checks),
        planned=len(checks),
    )


def check(
    check_id: str, status: str = "passed", *, optional: bool = False, selected: bool = True
) -> CheckRun:
    return CheckRun(
        check_id=check_id,
        name=check_id,
        status=status,  # type: ignore[arg-type]
        optional=optional,
        selected=selected,
        reason=None if selected else "no changed path matches db/**",
    )


def test_the_run_records_why_a_check_was_not_selected(tmp_path: Path) -> None:
    plan = policy(spec("sql", paths=["db/**"]), spec("tests"))

    run, _ = asyncio.run(run_policy(plan, tmp_path, changed=["src/app.py"]))

    sql = next(item for item in run.checks if item.check_id == "sql")
    assert (sql.status, sql.selected) == ("skipped", False)
    assert "db/**" in (sql.reason or "")
    assert run.blocking() == []


def test_an_unselected_gate_does_not_make_the_verdict_inconclusive() -> None:
    verdict = decide(
        run_with(check("sql", "skipped", selected=False), check("tests")), []
    )

    assert verdict.status == "pass"


def test_a_run_whose_every_gate_was_not_selected_cannot_pass() -> None:
    """No gate applied at all is not a green run: the change is outside the net."""
    verdict = decide(run_with(check("sql", "skipped", selected=False)), [])

    assert verdict.status == "inconclusive"
    assert verdict.reasons[0].code == "nothing-to-verify"


def test_a_selected_but_skipped_gate_still_makes_the_run_inconclusive() -> None:
    verdict = decide(
        run_with(check("sql", "skipped", selected=True), check("tests")), []
    )

    assert verdict.status == "inconclusive"


# --- the trace and the report ------------------------------------------------------------


def context_with(requirement_checks: list[str]) -> ProjectContext:
    return ProjectContext(
        requirements=[
            ContextItem(
                id="R1",
                title="Migrations are safe",
                status="approved",
                checks=requirement_checks,
            )
        ]
    )


def test_trace_distinguishes_not_selected_from_not_run() -> None:
    run = run_with(check("sql", "skipped", selected=False))
    rows = build_trace(context_with(["sql"]), run, [])

    assert rows[0].status == "not-selected"
    assert "1 not-selected" in summarise(rows)
    not_run = run_with(check("sql", "skipped", selected=True))
    assert build_trace(context_with(["sql"]), not_run, [])[0].status == "not-run"


def test_the_report_explains_the_plan_and_does_not_call_it_a_gap() -> None:
    run = run_with(check("sql", "skipped", selected=False), check("tests"))
    text = render_markdown(build_report(run, [], decide(run, [])))

    assert "## Plan" in text
    assert "no changed path matches db/**" in text
    assert "## Not checked" not in text


def test_the_report_stays_quiet_when_every_check_was_needed() -> None:
    run = run_with(check("tests"))
    text = render_markdown(build_report(run, [], decide(run, [])))

    assert "## Plan" not in text


# --- the change reaches the plan ---------------------------------------------------------

DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def total(x):
-    return x
+    return x + 1
"""


def test_execute_plans_from_the_diff_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qagent.orchestrator import execute

    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "policy.toml").write_text(
        "\n".join(
            [
                'name = "demo"',
                "[budget]",
                "max_seconds = 30",
                "",
                "[[checks]]",
                'id = "tests"',
                'argv = ["' + sys.executable + '", "-c", "pass"]',
                "",
                "[[checks]]",
                'id = "sql"',
                'argv = ["' + sys.executable + '", "-c", "pass"]',
                'when_paths = ["db/**"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "change.diff").write_text(DIFF, encoding="utf-8")
    monkeypatch.setenv("QAGENT_REVIEW_RESPONSE", json.dumps({"findings": []}))
    request = VerificationRequest(
        policy="policy.toml",
        diff="change.diff",
        reviewer="qagent.review.scripted:from_env",
    )

    report, _ = asyncio.run(execute(request, repo))

    sql = next(item for item in report.run.checks if item.check_id == "sql")
    assert (sql.status, sql.selected) == ("skipped", False)
    assert report.verdict.status == "pass"
    assert "## Plan" in render_markdown(report)


def test_the_dry_run_shows_the_same_selection(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "policy.toml").write_text(
        "\n".join(
            [
                'name = "demo"',
                "[budget]",
                "max_seconds = 30",
                "",
                "[[checks]]",
                'id = "sql"',
                'argv = ["' + sys.executable + '", "-c", "pass"]',
                'when_paths = ["db/**"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "change.diff").write_text(DIFF, encoding="utf-8")
    request = VerificationRequest(
        policy="policy.toml", diff="change.diff", reviewer="qagent.review.scripted:from_env"
    )

    lines = plan_lines(request, repo)

    assert any("[not-selected]" in line and "sql" in line for line in lines)


def test_the_parsed_diff_paths_are_what_the_plan_uses() -> None:
    diff: Diff = parse_diff(DIFF)

    assert changed_paths(diff) == ["app.py"]
    assert changed_paths(None) is None, "no change means unknown, not empty"


def test_a_truncated_diff_does_not_narrow_the_plan() -> None:
    """The files after the cut were never seen; a gate scoped to one of them must not vanish."""
    many = "".join(
        f"diff --git a/f{number}.py b/f{number}.py\n"
        f"--- a/f{number}.py\n+++ b/f{number}.py\n"
        f"@@ -1 +1 @@\n-a\n+b\n"
        for number in range(80)
    )
    diff = parse_diff(many)

    assert diff.truncated
    assert changed_paths(diff) is None
