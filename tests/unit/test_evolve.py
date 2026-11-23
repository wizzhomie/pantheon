"""Evolution: a rule mined from recorded runs, replayed against past verdicts, decided by hand.

The tests are built around the two claims a proposal makes, because they are the ones that
can be wrong: "this check kept failing" (a fact about the history) and "requiring it would
have changed these verdicts" (a counterfactual that has to go through the real ``decide``).
Everything else — the file, the merge, the CLI — only has to not lose the decision.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qagent.cli import main
from qagent.evolve import (
    PROPOSAL_DIR,
    Audit,
    History,
    Proposal,
    ProposalError,
    Retrospective,
    applicability,
    apply_proposals,
    audit,
    check_digest,
    load_history,
    load_proposals,
    mine,
    proposal_id,
    proposals_dir,
    replay,
    retrospective,
    save_proposal,
)
from qagent.evolve.history import RunFacts
from qagent.evolve.rules import gaps_from, promote
from qagent.verify import (
    Budget,
    CheckRun,
    CommandSpec,
    Finding,
    VerificationPolicy,
    VerificationRun,
    build_report,
    decide,
    read_policy,
    render_markdown,
    write_report,
)

NOW = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)


def check(
    check_id: str = "tests",
    status: str = "passed",
    *,
    optional: bool = False,
    reason: str | None = None,
) -> CheckRun:
    return CheckRun(
        check_id=check_id,
        name=check_id,
        status=status,  # type: ignore[arg-type]
        optional=optional,
        exit_code=None if status == "timeout" else 0,
        reason=reason,
    )


def record(
    *checks: CheckRun,
    run_id: str = "run-1",
    started_at: datetime | None = None,
) -> VerificationRun:
    started = started_at or NOW
    return VerificationRun(
        run_id=run_id,
        policy_name="demo",
        policy_digest="0" * 64,
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        checks=list(checks),
        planned=len(checks),
        spent_seconds=0.5,
    )


def facts(run: VerificationRun, findings: list[Finding] | None = None) -> RunFacts:
    found = list(findings or [])
    return RunFacts(run=run, verdict=decide(run, found), findings=found)


def spec(
    check_id: str = "types",
    *,
    optional: bool = True,
    argv: list[str] | None = None,
    timeout_s: float = 60.0,
) -> CommandSpec:
    return CommandSpec(
        id=check_id,
        name=check_id,
        argv=argv or ["python", "-m", "mypy"],
        optional=optional,
        timeout_s=timeout_s,
    )


def policy(*checks: CommandSpec, name: str = "demo") -> VerificationPolicy:
    return VerificationPolicy(name=name, checks=list(checks), budget=Budget())


def history(*runs: RunFacts) -> History:
    return History(runs=list(runs))


def shipped_with_type_failure(
    run_id: str = "run-1", started_at: datetime | None = None
) -> RunFacts:
    """A run that shipped: the suite passed, the optional type gate failed, verdict allows it."""
    run = record(
        check("tests"),
        check("types", "failed", optional=True),
        run_id=run_id,
        started_at=started_at,
    )
    return facts(run, _findings(run))


def held_after_adoption(run_id: str, started_at: datetime | None = None) -> RunFacts:
    """A run under the approved rule: the type gate is required, so it stops the change."""
    run = record(
        check("tests"),
        check("types", "failed", reason="exit 1"),
        run_id=run_id,
        started_at=started_at,
    )
    return facts(run, _findings(run))


def _findings(run: VerificationRun) -> list[Finding]:
    from qagent.verify import findings_from_run

    return findings_from_run(run)


def approved(proposal: Proposal, at: datetime | None = None) -> Proposal:
    return proposal.decided("approved", note="agreed", at=at or NOW)


# --- the history -------------------------------------------------------------------------


def test_history_reads_reports_and_names_undecided_runs(tmp_path: Path) -> None:
    decided = tmp_path / "decided"
    undecided = tmp_path / "undecided"
    run = record(check("tests"))
    write_report(build_report(run, [], decide(run, [])), decided)
    undecided.mkdir()
    (undecided / "run.json").write_text(run.model_dump_json(), encoding="utf-8")

    history = load_history([tmp_path])

    assert [item.run_id for item in history.runs] == ["run-1"]
    assert [Path(item.path).name for item in history.skipped] == ["undecided"]
    assert "never decided" in history.skipped[0].reason


def test_history_skips_broken_reports_by_path(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "report.json").write_text("{not json", encoding="utf-8")
    (broken / "run.json").write_text("{}", encoding="utf-8")

    history = load_history([tmp_path])

    assert history.runs == []
    assert "unreadable report" in history.skipped[0].reason


def test_history_keeps_one_copy_of_a_run_and_orders_oldest_first(tmp_path: Path) -> None:
    early = record(check("tests"), run_id="early", started_at=NOW - timedelta(days=1))
    late = record(check("tests"), run_id="late", started_at=NOW)
    for name, run in (("b-late", late), ("a-early", early)):
        write_report(build_report(run, [], decide(run, [])), tmp_path / name)

    history = load_history([tmp_path, tmp_path / "a-early"])

    assert [item.run_id for item in history.runs] == ["early", "late"]
    assert any("already loaded" in item.reason for item in history.skipped)
    assert history.span() == (early.started_at, late.started_at)
    assert history.verdicts() == {"pass": 2}


def test_history_digest_changes_only_with_runs(tmp_path: Path) -> None:
    first = record(check("tests"), run_id="a")
    second = record(check("tests", "failed"), run_id="b")
    one = history(facts(first))
    assert one.digest() != history(facts(first), facts(second)).digest()
    assert one.digest() == history(facts(first)).digest()


# --- the counterfactual ------------------------------------------------------------------


def test_promotion_flips_only_the_named_check() -> None:
    run = record(check("tests"), check("types", "failed", optional=True))
    promoted = promote(run, "types")

    assert [item.optional for item in promoted.checks] == [False, False]
    assert [item.status for item in promoted.checks] == ["passed", "failed"]
    assert [item.optional for item in run.checks] == [False, True], "the original is untouched"


def test_replay_holds_a_run_that_shipped() -> None:
    facts = shipped_with_type_failure()

    assert facts.verdict.status == "pass_with_conditions"
    assert replay(facts, "types").status == "hold"


def test_replay_keeps_the_gaps_the_run_had() -> None:
    run = record(check("tests"))
    verdict = decide(run, [], gaps=["the reviewer did not run: no key"])
    assert verdict.status == "inconclusive"
    facts = RunFacts(run=run, verdict=verdict, findings=[])

    assert gaps_from(verdict) == ["the reviewer did not run: no key"]
    assert replay(facts, "types").status == "inconclusive", "a blind spot is not fixed by a rule"


def test_retrospective_counts_caught_blocked_and_passed() -> None:
    shipped = shipped_with_type_failure("shipped")
    held = facts(
        record(
            check("tests", "failed"),
            check("types", "failed", optional=True),
            run_id="held",
        )
    )
    green = facts(record(check("tests"), check("types"), run_id="green"))

    counts = retrospective(history(shipped, held, green), "types")

    assert (counts.runs, counts.failed) == (3, 2)
    assert (counts.caught, counts.already_blocked) == (1, 1)
    assert counts.flips == ["shipped"]
    assert counts.summary().startswith("2 of 3 recorded runs failed it")


def test_retrospective_numbers_must_add_up() -> None:
    with pytest.raises(ValueError):
        Retrospective(runs=2, failed=2, caught=1, already_blocked=0, unchanged=0, passed=0)
    with pytest.raises(ValueError):
        Retrospective(
            runs=1, failed=1, caught=1, already_blocked=0, unchanged=0, passed=0, flips=[]
        )


# --- the proposal ------------------------------------------------------------------------


def test_mine_needs_the_same_failure_twice() -> None:
    one = shipped_with_type_failure("one")

    assert mine(history(one), policy(spec())) == []
    assert len(mine(history(one, shipped_with_type_failure("two")), policy(spec()))) == 1


def test_mine_does_not_propose_a_rule_that_caught_nothing() -> None:
    blocked = [
        facts(
            record(
                check("tests", "failed"),
                check("types", "failed", optional=True),
                run_id=f"held-{index}",
            )
        )
        for index in (1, 2)
    ]

    assert mine(history(*blocked), policy(spec())) == []


def test_proposal_records_the_change_and_the_evidence() -> None:
    run_history = history(
        shipped_with_type_failure("a"),
        shipped_with_type_failure("b"),
        facts(record(check("tests"), check("types"), run_id="c")),
    )
    (proposal,) = mine(run_history, policy(spec()))

    assert proposal.id == proposal_id("types", check_digest(spec()))
    assert proposal.status == "proposed"
    assert proposal.change.check_id == "types"
    assert (proposal.change.before_optional, proposal.change.after_optional) == (True, False)
    assert proposal.policy_digest == policy(spec()).digest()
    assert proposal.retrospective.caught == 2
    assert {item.run_id for item in proposal.evidence} == {"a", "b"}
    assert all(item.would_be == "hold" for item in proposal.evidence)
    assert "requiring it would have held" in proposal.rationale


def test_mine_skips_checks_that_are_already_required() -> None:
    run_history = history(shipped_with_type_failure("a"), shipped_with_type_failure("b"))

    assert mine(run_history, policy(spec(optional=False))) == []


def test_mine_skips_a_rule_that_was_already_decided_for_this_command() -> None:
    run_history = history(shipped_with_type_failure("a"), shipped_with_type_failure("b"))
    (proposal,) = mine(run_history, policy(spec()))

    assert mine(run_history, policy(spec()), existing=[approved(proposal)]) == []


def test_mine_reopens_the_question_when_the_command_changed() -> None:
    run_history = history(shipped_with_type_failure("a"), shipped_with_type_failure("b"))
    (proposal,) = mine(run_history, policy(spec()))
    changed = spec(argv=["python", "-m", "mypy", "--strict"])

    (again,) = mine(run_history, policy(changed), existing=[approved(proposal)])

    assert again.id != proposal.id
    assert applicability(approved(proposal), policy(changed)) == "stale"


def test_proposal_id_must_name_its_change() -> None:
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), policy(spec())
    )
    with pytest.raises(ValueError):
        Proposal.model_validate({**proposal.model_dump(), "id": "p-promote-other"})


def test_a_decision_records_when_it_was_made() -> None:
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), policy(spec())
    )
    decided = proposal.decided("rejected", note=" flaky on windows ", at=NOW)

    assert (decided.status, decided.note) == ("rejected", "flaky on windows")
    assert decided.decided_at == NOW
    with pytest.raises(ValueError):
        proposal.decided("proposed")


# --- the store ---------------------------------------------------------------------------


def test_a_decision_survives_a_round_trip(tmp_path: Path) -> None:
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), policy(spec())
    )
    path = save_proposal(tmp_path, approved(proposal))

    assert path.parent == proposals_dir(tmp_path)
    (loaded,) = load_proposals(tmp_path)
    assert loaded.status == "approved"
    assert loaded.note == "agreed"
    assert loaded.change.check_digest == proposal.change.check_digest


def test_store_has_nothing_to_load_before_a_decision(tmp_path: Path) -> None:
    assert load_proposals(tmp_path) == []
    assert not proposals_dir(tmp_path).exists()


def test_store_refuses_an_id_that_escapes_the_directory(tmp_path: Path) -> None:
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), policy(spec())
    )
    escaped = proposal.model_copy(update={"id": "../../escape"})

    with pytest.raises(ProposalError):
        save_proposal(tmp_path, escaped)


def test_store_refuses_a_broken_decision_file(tmp_path: Path) -> None:
    directory = proposals_dir(tmp_path)
    directory.mkdir(parents=True)
    (directory / "p-promote-types-000000.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProposalError):
        load_proposals(tmp_path)


# --- the merge ---------------------------------------------------------------------------


def test_approval_is_merged_over_the_policy_without_editing_it() -> None:
    declared = policy(spec(), spec("tests", optional=False))
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), declared
    )
    before = declared.digest()

    applied = apply_proposals(declared, [approved(proposal)])

    assert applied.adopted == [proposal.id]
    assert [item.optional for item in applied.policy.checks] == [False, False]
    assert declared.digest() == before
    assert [item.optional for item in declared.checks] == [True, False]
    assert applied.policy.digest() != before


def test_an_undecided_or_rejected_proposal_changes_nothing() -> None:
    declared = policy(spec())
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), declared
    )

    pending = apply_proposals(declared, [proposal])
    rejected = apply_proposals(declared, [proposal.decided("rejected", note="no")])

    assert pending.adopted == [] and pending.policy.digest() == declared.digest()
    assert pending.ignored[0].applicability == "pending"
    assert rejected.adopted == []
    assert rejected.ignored[0].applicability == "rejected"


def test_a_satisfied_or_obsolete_proposal_is_reported_not_applied() -> None:
    declared = policy(spec())
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), declared
    )
    already_required = policy(spec(optional=False))
    gone = policy(spec("tests", optional=False))

    assert applicability(approved(proposal), already_required) == "satisfied"
    assert applicability(approved(proposal), gone) == "obsolete"
    assert apply_proposals(already_required, [approved(proposal)]).adopted == []


def test_audit_counts_only_runs_after_the_decision() -> None:
    (proposal,) = mine(
        history(shipped_with_type_failure("a"), shipped_with_type_failure("b")), policy(spec())
    )
    decided = approved(proposal, at=NOW + timedelta(hours=1))
    later = held_after_adoption("later", started_at=NOW + timedelta(hours=2))

    counts = audit(decided, history(shipped_with_type_failure("a"), later))

    assert counts.runs == 1
    assert (counts.failed, counts.held) == (1, 1)
    assert audit(proposal, history(later)) == Audit()


# --- the report --------------------------------------------------------------------------


def test_report_says_which_accepted_rules_were_in_force() -> None:
    run = record(check("tests"))
    run = run.model_copy(update={"adopted": ["p-promote-types-63beb7"]})
    text = render_markdown(build_report(run, [], decide(run, [])))

    assert "| Accepted rules | `p-promote-types-63beb7` |" in text
    assert "rules the project accepted from its own history" in text


def test_report_without_accepted_rules_stays_quiet() -> None:
    run = record(check("tests"))
    text = render_markdown(build_report(run, [], decide(run, [])))

    assert "Accepted rules" not in text


# --- the committed demo ------------------------------------------------------------------


def test_the_committed_demo_still_shows_the_loop_end_to_end() -> None:
    """The demo is evidence; if it rots, the claim in the README rots with it."""
    demo = Path(__file__).resolve().parents[2] / "examples" / "evolve_demo"
    (proposal,) = load_proposals(demo)
    declared = read_policy(demo / "policy.toml")
    history = load_history([demo / "history"])

    assert proposal.status == "approved"
    assert proposal.note, "a decision carries the human's reason"
    assert [item.verdict.status for item in history.runs] == [
        "pass_with_conditions",
        "pass_with_conditions",
        "pass_with_conditions",
        "hold",
    ]
    applied = apply_proposals(declared, load_proposals(demo))
    assert applied.adopted == [proposal.id]
    assert audit(proposal, history).held == 1


# --- the command line, end to end --------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "policy.toml").write_text(
        "\n".join(
            [
                'name = "demo"',
                "",
                "[budget]",
                "max_seconds = 30",
                "max_checks = 4",
                "",
                "[[checks]]",
                'id = "tests"',
                'argv = ["' + sys.executable + '", "-c", "pass"]',
                "",
                "[[checks]]",
                'id = "types"',
                'argv = ["' + sys.executable + '", "-c", "import sys; sys.exit(1)"]',
                "optional = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo


def _verify(repo: Path, out: Path) -> int:
    return main(
        ["verify", "--repo", str(repo), "--policy", "policy.toml", "--out", str(out)]
    )


def _learn(repo: Path, *extra: str) -> int:
    return main(
        [
            "learn",
            "--repo",
            str(repo),
            "--policy",
            str(repo / "policy.toml"),
            "--history",
            str(repo),
            *extra,
        ]
    )


def test_the_whole_loop_from_history_to_a_stricter_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _project(tmp_path)
    assert _verify(repo, repo / "run-1") == 0  # PASS WITH CONDITIONS still ships
    assert _verify(repo, repo / "run-2") == 0
    capsys.readouterr()

    assert _learn(repo, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    (proposal,) = payload["proposals"]
    assert proposal["applicability"] == "pending"
    assert proposal["retrospective"]["caught"] == 2

    assert _learn(repo, "--approve", proposal["id"], "--note", "shipped twice") == 0
    assert (repo / ".qagent" / PROPOSAL_DIR / f'{proposal["id"]}.json').is_file()
    capsys.readouterr()

    assert _verify(repo, repo / "run-3") == 1  # the same evidence, now a gate
    stored = json.loads((repo / "run-3" / "report.json").read_text(encoding="utf-8"))
    assert stored["run"]["adopted"] == [proposal["id"]]
    assert stored["verdict"]["status"] == "hold"

    assert _learn(repo) == 0
    output = capsys.readouterr().out
    assert "nothing new" in output
    assert "[approved, applied]" in output
    assert "since the decision: 1 run, 1 failed, 1 held" in output


def test_learn_writes_nothing_and_says_so_when_the_history_is_thin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _project(tmp_path)
    assert _verify(repo, repo / "run-1") == 0
    capsys.readouterr()

    assert _learn(repo) == 0

    output = capsys.readouterr().out
    assert "no rule earned a proposal" in output
    assert not proposals_dir(repo).exists()


def test_learn_refuses_to_decide_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _project(tmp_path)
    _verify(repo, repo / "run-1")
    _verify(repo, repo / "run-2")
    capsys.readouterr()
    _learn(repo, "--json")
    (proposal,) = json.loads(capsys.readouterr().out)["proposals"]
    assert _learn(repo, "--reject", proposal["id"]) == 0
    capsys.readouterr()

    assert _learn(repo, "--reject", proposal["id"]) == 1

    assert "already rejected" in capsys.readouterr().err


def test_learn_refuses_an_unknown_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _project(tmp_path)

    assert _learn(repo, "--approve", "p-nothing") == 1

    assert "no proposal p-nothing" in capsys.readouterr().err


def test_learn_needs_a_policy_to_compare_against(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["learn", "--repo", str(tmp_path)]) == 1

    assert "no policy at" in capsys.readouterr().err
