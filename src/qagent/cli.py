"""Command line: init a project file, check it, evaluate a dataset, compare reports.

Two ways in, on purpose:

``--config qagent.toml``
    The recommended path: the cartridge and the run policy come from a file, and only the
    agent and the judge client are code (``module:factory``).
``--spec module:factory``
    The original path: a project that wants to build the whole ``GuardedAgent`` itself.

``record`` and ``replay`` are thin wrappers around ``eval``: they set the environment a
project's factories read (``QAGENT_RECORD=1`` with a live model, or ``QAGENT_LLM=replay``)
and then evaluate. A project whose factories ignore those variables still works — it just
ignores the request, which is why the variables are documented in the example runners.

``--mode`` overrides the configured mode, which is how a baseline and a guarded report are
produced from one setup.

The verification side is separate on purpose: ``inspect`` describes a repository, ``run``
executes its checks, and ``verify`` does both plus findings, a verdict and a report. A diff
on the command line turns the independent reviewer on — opt-in, because it is the only part
that costs money. ``--request`` names the same things in a file, and ``show`` reads a stored
run back without executing anything: an explanation must not be a second experiment.

``learn`` reads the runs that already have a verdict and proposes a rule the history argues
for; the only write it can make is a decision file in ``.qagent/proposals/``. The declared
policy is never rewritten, and a proposal nobody approved changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .bench import run_suite
from .context import (
    ProjectProfile,
    check_context,
    detect_profile,
    read_context,
    read_profile,
    write_profile,
)
from .evals import Dataset, compare, evaluate, write_diff_markdown, write_json, write_markdown
from .evolve import (
    IGNORED_REASON,
    History,
    Proposal,
    ProposalStatus,
    applicability,
    audit,
    load_history,
    load_proposals,
    mine,
    save_proposal,
)
from .orchestrator import (
    effective_policy,
    execute,
    exit_code_for,
    plan_lines,
    read_request,
    render_show,
    trace_line,
)
from .orchestrator import show as read_run
from .project import ProjectConfig, build_guarded, validate
from .runtime.facade import GuardedAgent
from .verify import (
    CheckRun,
    VerificationPolicy,
    VerificationRequest,
    build_plan,
    describe_limits,
    describe_plan,
    describe_sandbox,
    read_policy,
    run_policy,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INCONCLUSIVE = 2
# HOLD is 1 (the project is broken); 2 means the run could not answer.

CONFIG_TEMPLATE = '''\
# One project, one file. Run it with: qagent eval --config qagent.toml
name = "my-project"
dataset = "dataset.jsonl"
# The output schema and the agent are the only two things a file cannot describe.
schema = "myproject.schemas:Ticket"
agent = "myproject.agent:build_agent"
# The judge needs a client; drop this line if the judge is disabled below.
judge_client = "myproject.agent:build_judge_client"

mode = "guarded"
agent_prompt_file = "prompts/agent.txt"
judge_instructions_file = "prompts/judge.txt"

[report]
out = "reports/guarded.json"
markdown = "reports/guarded.md"

[limits]
max_cost_usd = 0.05
max_latency_ms = 30000

[retry]
max_attempts = 2

[judge]
enabled = true
model = "the-judge-model"
max_tokens = 900

[rubric]
pass_score = 0.75

[[rubric.criteria]]
id = "grounding"
title = "Grounding"
description = "5: every filled value is stated in the input. 1: a value is invented."
weight = 0.5

[[rubric.criteria]]
id = "completeness"
title = "Completeness"
description = "5: nothing the input states is missing. 1: half of it is missing."
weight = 0.5

[scoring]
min_field_accuracy = 1.0

[[scoring.fields]]
path = "priority"
comparator = "normalized"

[[scoring.fields]]
path = "summary"
comparator = "fuzzy"

[checks.required]
rules = [{ path = "priority" }, { path = "summary" }]

[checks.formats]
rules = [{ path = "priority", kind = "enum", allowed = ["P1", "P2", "P3", "P4"] }]

[checks.evidence]
required_paths = ["summary"]

[[pricing]]
model = "the-agent-model"
input_usd_per_mtok = 0.30
output_usd_per_mtok = 1.20
checked_at = 2026-11-23
'''

DATASET_TEMPLATE = (
    '{"case_id":"case-01","category":"happy_path","payload":"a customer message goes here",'
    '"expected":{"priority":"P3","summary":"what the message asks for"}}\n'
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in ("eval", "record", "replay"):
            return _run_eval(args)
        if args.command == "check":
            return _run_check(args)
        if args.command == "init":
            return _run_init(args)
        if args.command == "inspect":
            return _run_inspect(args)
        if args.command == "context":
            return _run_context(args)
        if args.command == "run":
            return _run_command(args)
        if args.command == "verify":
            return _run_verify(args)
        if args.command == "show":
            return _run_show(args)
        if args.command == "learn":
            return _run_learn(args)
        if args.command == "bench":
            return _run_bench(args)
        return _run_compare(args)
    except (ValueError, ImportError, AttributeError, OSError) as exc:
        print(f"qagent: {exc}", file=sys.stderr)
        return EXIT_FAILURE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qagent",
        description=(
            "Verify AI-assisted changes: run a project's checks, review a diff, "
            "decide a verdict; also evaluate a dataset against a preset."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("eval", help="run a dataset through a guarded agent")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="qagent.toml describing the project (recommended)",
    )
    source.add_argument(
        "--spec",
        metavar="MODULE:FACTORY",
        help="zero-argument factory returning GuardedAgent, e.g. examples.intake.runner:build",
    )
    run.add_argument("--dataset", type=Path, help="override the dataset from the config")
    run.add_argument("--out", type=Path, help="JSON report to write")
    run.add_argument("--markdown", type=Path, help="Markdown report to write")
    run.add_argument("--mode", choices=["guarded", "baseline", "shadow"])
    run.add_argument("--concurrency", type=int, default=4)

    for name, summary in (
        ("record", "evaluate against a live model and write the answers to the cassette"),
        ("replay", "evaluate from the committed cassette: no key, no network, no bill"),
    ):
        wrapper = subcommands.add_parser(name, help=summary)
        wrapper.add_argument("--config", type=Path, required=True, metavar="PATH")
        wrapper.add_argument("--dataset", type=Path)
        wrapper.add_argument("--out", type=Path, help="JSON report; defaults to the config")
        wrapper.add_argument("--markdown", type=Path)
        wrapper.add_argument("--mode", choices=["guarded", "baseline", "shadow"])
        wrapper.add_argument("--concurrency", type=int, default=4)

    check = subcommands.add_parser("check", help="validate a config and its dataset, run nothing")
    check.add_argument("--config", required=True, type=Path, metavar="PATH")

    init = subcommands.add_parser("init", help="write a starter config and dataset")
    init.add_argument("--dir", type=Path, default=Path("."), help="where to write the files")

    inspect = subcommands.add_parser(
        "inspect", help="describe a repository: stack, checks, layout, entry points"
    )
    inspect.add_argument("--repo", type=Path, default=Path("."), help="repository to read")
    inspect.add_argument(
        "--write", action="store_true", help="write .qagent/profile.json inside the repository"
    )
    inspect.add_argument("--json", action="store_true", help="print the profile as JSON")

    context = subcommands.add_parser(
        "context",
        help="show the project's requirements, invariants and milestones; check their links",
    )
    context.add_argument("--repo", type=Path, default=Path("."), help="project to read")
    context.add_argument("--json", action="store_true", help="print the context as JSON")
    context.add_argument(
        "--check",
        action="store_true",
        help="validate the links between promises and the checks in the policy",
    )

    run = subcommands.add_parser(
        "run", help="run the project's own checks and keep the evidence"
    )
    run.add_argument("--repo", type=Path, default=Path("."), help="repository to check")
    run.add_argument(
        "--policy", type=Path, required=True, help="policy file: which checks, what budget"
    )
    run.add_argument("--out", type=Path, help="run directory; defaults to .qagent/runs/<run id>")
    run.add_argument(
        "--dry-run", action="store_true", help="print the plan and the budget, run nothing"
    )

    verify = subcommands.add_parser(
        "verify", help="run the checks, decide a verdict and write the report"
    )
    verify.add_argument("--repo", type=Path, default=Path("."), help="repository to verify")
    verify.add_argument(
        "--policy",
        type=Path,
        help="policy file; defaults to <repo>/.qagent/policy.toml",
    )
    verify.add_argument("--out", type=Path, help="run directory; defaults to .qagent/runs/<run id>")
    verify.add_argument(
        "--dry-run", action="store_true", help="print the plan and the budget, run nothing"
    )
    change = verify.add_mutually_exclusive_group()
    change.add_argument("--diff", type=Path, metavar="PATH", help="a unified diff to review")
    change.add_argument(
        "--since",
        metavar="REV",
        help="review `git diff REV` instead of a diff file",
    )
    verify.add_argument(
        "--reviewer",
        metavar="MODULE:FACTORY",
        help="zero-argument factory returning an LLMClient, e.g. qagent.review.factory:from_env",
    )
    verify.add_argument(
        "--review-instructions",
        type=Path,
        metavar="PATH",
        help="project rules for the reviewer, appended to the prompt",
    )
    verify.add_argument("--review-model", default=None, help="model name for the review call")
    verify.add_argument(
        "--second-look",
        action="store_true",
        help="a second reader checks every claim before the anchor; one more call per review",
    )
    verify.add_argument("--review-max-tokens", type=int, default=1500)
    verify.add_argument(
        "--no-cache",
        action="store_true",
        help="call the reviewer even when the same question is already cached",
    )
    verify.add_argument(
        "--request",
        type=Path,
        metavar="PATH",
        help="a request file (name, policy, change, reviewer, budget) instead of flags",
    )

    learn = subcommands.add_parser(
        "learn",
        help="propose a rule from the recorded runs; approve or reject it by hand",
    )
    learn.add_argument("--repo", type=Path, default=Path("."), help="repository to learn about")
    learn.add_argument(
        "--policy", type=Path, help="policy file; defaults to .qagent/policy.toml"
    )
    learn.add_argument(
        "--history",
        type=Path,
        action="append",
        help="a run directory or a directory of runs; repeatable, defaults to .qagent/runs",
    )
    decision = learn.add_mutually_exclusive_group()
    decision.add_argument("--approve", metavar="ID", help="accept a proposal and record why")
    decision.add_argument("--reject", metavar="ID", help="refuse a proposal and record why")
    learn.add_argument("--note", default="", help="the reason, stored with the decision")
    learn.add_argument("--json", action="store_true", help="machine-readable output")

    shower = subcommands.add_parser(
        "show", help="read a stored run back and explain it, without running anything"
    )
    shower.add_argument("path", type=Path, help="run directory or report.json")
    shower.add_argument("--json", action="store_true", help="print the report as JSON")

    bench = subcommands.add_parser(
        "bench", help="run the contour over a holdout suite whose answers are known"
    )
    bench.add_argument(
        "--suite",
        type=Path,
        default=Path("examples/holdout"),
        help="directory with `base/` and `cases/`",
    )
    bench.add_argument(
        "--reviewer",
        metavar="MODULE:FACTORY",
        required=True,
        help="the reviewer every case uses, e.g. qagent.review.scripted:from_env",
    )
    bench.add_argument("--review-model", default=None, help="model name recorded in the report")
    bench.add_argument(
        "--second-look",
        action="store_true",
        help="run every case with the second reader, as a user can",
    )
    bench.add_argument(
        "--layer",
        choices=("checks", "reviewer", "all"),
        default="checks",
        help="which half of the contour to measure",
    )
    bench.add_argument("--out", type=Path, help="write bench.json and bench.md here")
    bench.add_argument("--json", action="store_true", help="print the report as JSON only")

    diff = subcommands.add_parser("compare", help="compare two JSON reports")
    diff.add_argument("a", type=Path)
    diff.add_argument("b", type=Path)
    diff.add_argument("--markdown", type=Path)
    return parser


def _run_eval(args: argparse.Namespace) -> int:
    if args.command == "record":
        os.environ["QAGENT_LLM"] = "live"
        os.environ["QAGENT_RECORD"] = "1"
    elif args.command == "replay":
        os.environ["QAGENT_LLM"] = "replay"
    guarded, dataset_path, out, markdown = _resolve_eval(args)
    if args.mode:
        guarded = guarded.with_mode(args.mode)
    dataset = Dataset.load(dataset_path)
    report = asyncio.run(evaluate(guarded, dataset, concurrency=args.concurrency))
    write_json(report, out)
    if markdown:
        write_markdown(report, markdown)
    print(
        f"{report.run_id}: {report.metrics.cases} cases, "
        f"pass {report.metrics.pass_rate_final:.1%} -> {out}"
    )
    return EXIT_OK


def _resolve_eval(
    args: argparse.Namespace,
) -> tuple[GuardedAgent[Any], Path, Path, Path | None]:
    if args.config is not None:
        config = ProjectConfig.load(args.config)
        guarded = build_guarded(config)
        out = args.out or (Path(config.report.out) if config.report.out else None)
        markdown = args.markdown or (
            Path(config.report.markdown) if config.report.markdown else None
        )
        if out is None:
            raise ValueError("--out is required when the config has no [report] out")
        return guarded, Path(config.dataset), out, markdown
    if args.out is None or args.dataset is None:
        raise ValueError("--dataset and --out are required with --spec")
    return load_guarded(args.spec), args.dataset, args.out, args.markdown


def _run_inspect(args: argparse.Namespace) -> int:
    profile = detect_profile(args.repo)
    if args.json:
        print(profile.model_dump_json(indent=2))
        return EXIT_OK
    print(profile.summary())
    for tool in profile.tools:
        print(f"  {tool.name}: {' '.join(tool.command)}  ({tool.source})")
    if profile.test_paths:
        print(f"  tests: {', '.join(profile.test_paths)}")
    if profile.entry_points:
        print(f"  entry points: {', '.join(profile.entry_points)}")
    if args.write:
        target = write_profile(profile, args.repo)
        print(f"profile: {target}")
    else:
        print("profile not written; pass --write to store it in .qagent/profile.json")
    return EXIT_OK


def _run_command(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    applied = effective_policy(repo, args.policy)
    policy = applied.policy
    profile = _profile_or_none(repo)
    plan = build_plan(policy, profile)
    budget = policy.budget
    if args.dry_run:
        print(
            f"{policy.name}: {len(plan)} checks, "
            f"budget {budget.max_seconds:g}s / {budget.max_checks} checks"
        )
        if applied.adopted:
            print(f"  accepted rules: {', '.join(applied.adopted)}")
        sandbox = describe_sandbox(policy.sandbox)
        if sandbox is not None:
            print(f"  sandbox: {sandbox}")
        for line in describe_plan(plan):
            print(f"  {line}")
        return EXIT_OK
    run, target = asyncio.run(
        run_policy(policy, repo, out_dir=args.out, profile=profile, adopted=applied.adopted)
    )
    if applied.adopted:
        print(f"accepted rules: {', '.join(applied.adopted)}")
    if run.sandbox is not None:
        print(f"sandbox: {describe_limits(run.sandbox)}")
    print(f"{run.run_id}: {len(run.checks)} checks in {run.spent_seconds:.1f}s -> {target}")
    for check in run.checks:
        detail = f"  ({check.reason})" if check.reason else ""
        print(f"  {_check_label(check):14} {check.check_id}{detail}")
    blocking = run.blocking()
    if blocking:
        print(f"blocking: {', '.join(check.check_id for check in blocking)}")
        return EXIT_FAILURE
    return EXIT_OK


def _run_context(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    context = read_context(repo)
    if args.json:
        print(context.model_dump_json(indent=2))
        return EXIT_OK
    print(f"context: {context.summary()} · digest {context.digest()[:12]}")
    if context.architecture.baseline:
        print(f"  baseline: {_one_line(context.architecture.baseline)}")
    if context.architecture.target:
        print(f"  target:   {_one_line(context.architecture.target)}")
    for heading, items in (
        ("requirements", context.requirements),
        ("invariants", context.invariants),
        ("milestones", context.milestones),
    ):
        if not items:
            continue
        print(f"  {heading}:")
        for item in items:
            checks = f"  [{', '.join(item.checks)}]" if item.checks else ""
            print(f"    {item.id} ({item.status}) {item.title}{checks}")
    if not args.check:
        return EXIT_OK
    problems = check_context(context, _policy_or_none(repo))
    for problem in problems:
        print(f"  {problem.level}: {problem.detail}")
    if not problems:
        print("  links checked: no problems")
    return EXIT_FAILURE if any(problem.level == "error" for problem in problems) else EXIT_OK


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _policy_or_none(repo: Path) -> VerificationPolicy | None:
    path = repo / ".qagent" / "policy.toml"
    return read_policy(path) if path.is_file() else None


def _run_verify(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    request = _request_from_args(args, repo)
    if args.dry_run:
        for line in plan_lines(request, repo):
            print(line)
        return EXIT_OK

    report, target = asyncio.run(
        execute(request, repo, out_dir=args.out, use_cache=not args.no_cache)
    )
    run = report.run
    if run.adopted:
        print(f"accepted rules: {', '.join(run.adopted)}")
    print(f"{run.run_id}: {len(run.checks)} checks in {run.spent_seconds:.1f}s -> {target}")
    for check in run.checks:
        detail = f"  ({check.reason})" if check.reason else ""
        print(f"  {_check_label(check):14} {check.check_id}{detail}")
    review = report.review
    if review is not None:
        if review.status == "failed":
            print(f"review: failed ({review.error})")
        else:
            print(
                f"review: {review.proposed} proposed, {review.confirmed} confirmed, "
                f"{review.unverified} unverified, "
                f"{review.prompt_tokens}+{review.completion_tokens} tokens"
            )
    traced = trace_line(report)
    if traced is not None:
        print(traced)
    print(f"verdict: {report.verdict.headline}")
    for reason in report.verdict.reasons:
        print(f"  {reason.code}: {reason.detail}")
    print(f"report: {target / 'report.json'} and {target / 'report.md'}")
    return exit_code_for(report)


def _run_learn(args: argparse.Namespace) -> int:
    """Mine the recorded runs for a rule; approving or rejecting one is the only write."""
    repo = Path(args.repo)
    if args.note and not (args.approve or args.reject):
        raise ValueError("--note only means something with --approve or --reject")
    policy_path = args.policy or repo / ".qagent" / "policy.toml"
    if not policy_path.is_file():
        raise ValueError(f"no policy at {policy_path}; point --policy at the project's policy")
    policy = read_policy(policy_path)
    history = load_history(args.history or [repo / ".qagent" / "runs"])
    stored = load_proposals(repo)
    mined = mine(history, policy, existing=stored)

    identifier = args.approve or args.reject
    if identifier is not None:
        status: ProposalStatus = "approved" if args.approve else "rejected"
        known = {proposal.id: proposal for proposal in [*stored, *mined]}
        proposal = known.get(identifier)
        if proposal is None:
            raise ValueError(
                f"no proposal {identifier}; run `qagent learn` to see what the history argues for"
            )
        if proposal.status != "proposed":
            when = f" on {proposal.decided_at:%Y-%m-%d}" if proposal.decided_at else ""
            raise ValueError(f"{identifier} was already {proposal.status}{when}; nothing changed")
        if status == "approved":
            state = applicability(proposal, policy)
            if state not in ("applicable", "pending"):
                raise ValueError(f"{identifier} cannot be approved: {IGNORED_REASON[state]}")
        updated = proposal.decided(status, note=args.note)
        path = save_proposal(repo, updated)
        if args.json:
            print(updated.model_dump_json(indent=2))
        else:
            print(f"{status}: {updated.id} -> {path}")
            if status == "approved":
                print("next: run `qagent verify`; the rule is merged over the policy, unedited")
            else:
                print("the history will not propose this rule again while the command is unchanged")
        return EXIT_OK

    if args.json:
        print(json.dumps(_learn_payload(policy, history, mined, stored), indent=2))
        return EXIT_OK

    _print_learn_header(policy, history)
    if mined:
        print("proposed")
        for proposal in mined:
            _print_proposal(proposal, policy, indent="  ")
    elif stored:
        print("nothing new: every rule the history argues for has already been decided")
    else:
        print(
            "no rule earned a proposal: no optional check failed in at least two recorded runs "
            "that the verdict still let through"
        )
    if stored:
        print("decided")
        for proposal in stored:
            _print_proposal(proposal, policy, indent="  ", with_audit=True, history=history)
    return EXIT_OK


def _learn_payload(
    policy: VerificationPolicy,
    history: History,
    mined: list[Proposal],
    stored: list[Proposal],
) -> dict[str, Any]:
    return {
        "policy": {
            "name": policy.name,
            "checks": len(policy.checks),
            "digest": policy.digest(),
        },
        "history": {
            "runs": len(history.runs),
            "digest": history.digest(),
            "verdicts": history.verdicts(),
            "sources": history.sources,
            "skipped": [item.model_dump() for item in history.skipped],
        },
        "proposals": [_as_json(proposal, policy) for proposal in [*mined, *stored]],
    }


def _count(number: int, singular: str, plural: str | None = None) -> str:
    if number == 1:
        return f"1 {singular}"
    return f"{number} {plural or singular + 's'}"


def _as_json(proposal: Proposal, policy: VerificationPolicy) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(proposal.model_dump_json())
    payload["applicability"] = applicability(proposal, policy)
    return payload


def _print_learn_header(policy: VerificationPolicy, history: History) -> None:
    span = history.span()
    when = f"{span[0]:%Y-%m-%d %H:%M} → {span[1]:%H:%M}" if span else "no decided runs"
    verdicts = ", ".join(f"{status} {count}" for status, count in history.verdicts().items())
    print(f"history: {_count(len(history.runs), 'run')} ({when}); verdicts: {verdicts or 'none'}")
    for item in history.skipped[:3]:
        print(f"  skipped {item.path}: {item.reason}")
    if len(history.skipped) > 3:
        print(f"  skipped {len(history.skipped) - 3} more")
    print(f"policy: {policy.name}, {len(policy.checks)} checks, digest {policy.digest()[:12]}")


def _print_proposal(
    proposal: Proposal,
    policy: VerificationPolicy,
    *,
    indent: str,
    with_audit: bool = False,
    history: History | None = None,
) -> None:
    state = applicability(proposal, policy)
    label = {
        "applicable": "applied",
        "pending": "waiting",
        "satisfied": "already in the policy",
        "stale": "stale",
        "obsolete": "obsolete",
        "rejected": "rejected",
    }[state]
    detail = "" if state == proposal.status else f", {label}"
    marker = f" [{proposal.status}{detail}]"
    print(f"{indent}{proposal.id}{marker} {proposal.title}")
    change = proposal.change
    before = "optional" if change.before_optional else "required"
    after = "optional" if change.after_optional else "required"
    print(f"{indent}  change: {change.check_id}: {before} -> {after}")
    counts = proposal.retrospective
    print(
        f"{indent}  evidence: {counts.failed} of {counts.runs} recorded runs failed it; "
        f"{counts.caught} still shipped, {counts.already_blocked} were already held"
    )
    if state in ("stale", "obsolete"):
        print(f"{indent}  ignored: {IGNORED_REASON[state]}")
    if proposal.note:
        print(f"{indent}  note: {proposal.note}")
    if with_audit and proposal.status == "approved" and state == "applicable" and history:
        after_adoption = audit(proposal, history)
        print(
            f"{indent}  since the decision: {_count(after_adoption.runs, 'run')}, "
            f"{after_adoption.failed} failed, {after_adoption.held} held"
        )


def _check_label(check: CheckRun) -> str:
    """A check the plan did not select is a decision, not a skip that should worry a reader."""
    return "not selected" if not check.selected else check.status


def _request_from_args(args: argparse.Namespace, repo: Path) -> VerificationRequest:
    """The request the command line describes: a file, or the flags turned into one."""
    if args.request is not None:
        overridden = [
            flag
            for flag, value in (
                ("--policy", args.policy),
                ("--diff", args.diff),
                ("--since", args.since),
                ("--reviewer", args.reviewer),
                ("--review-instructions", args.review_instructions),
                ("--review-model", args.review_model),
                ("--second-look", args.second_look or None),
            )
            if value is not None
        ]
        if overridden:
            raise ValueError(f"--request already names {', '.join(overridden)}; drop them")
        request = read_request(args.request)
    else:
        request = VerificationRequest(
            policy=str(args.policy) if args.policy is not None else ".qagent/policy.toml",
            diff=str(args.diff) if args.diff is not None else None,
            since=args.since,
            reviewer=args.reviewer,
            instructions=str(args.review_instructions)
            if args.review_instructions is not None
            else None,
            model=args.review_model,
            max_tokens=args.review_max_tokens,
            second_look=args.second_look,
        )
    policy_file = Path(request.policy)
    if not policy_file.is_absolute():
        policy_file = repo / policy_file
    if not policy_file.is_file():
        raise ValueError(
            f"no policy at {policy_file}; point --policy at one "
            "(the file lives in the project, in .qagent/policy.toml)"
        )
    return request


def _run_bench(args: argparse.Namespace) -> int:
    report, target = asyncio.run(
        run_suite(
            args.suite,
            reviewer=args.reviewer,
            model=args.review_model,
            out_dir=args.out,
            layer=args.layer,
            second_look=args.second_look,
        )
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        for case in report.cases:
            mark = "ok" if case.matched else "MISS"
            print(f"  {mark:4} {case.id:20} {case.expected:20} {case.actual}")
        print(report.summary())
        if target is not None:
            print(f"report: {target / 'bench.json'} and {target / 'bench.md'}")
    return EXIT_OK if report.passed else EXIT_FAILURE


def _run_show(args: argparse.Namespace) -> int:
    report, path = read_run(args.path)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_show(report))
        print(f"report: {path}")
    return exit_code_for(report)


def _profile_or_none(repo: Path) -> ProjectProfile | None:
    """The stored profile, or a fresh one when the project has not written it yet.

    Detecting on the fly is deterministic, so a verification run does not depend on whether
    someone remembered to run ``qagent inspect --write`` first.
    """
    try:
        return read_profile(repo)
    except FileNotFoundError:
        try:
            return detect_profile(repo)
        except NotADirectoryError:
            return None


def _run_check(args: argparse.Namespace) -> int:
    config = ProjectConfig.load(args.config)
    notes = validate(config)
    dataset = Dataset.load(Path(config.dataset))
    categories = sorted({case.category for case in dataset.cases})
    print(
        f"{config.name}: {len(dataset.cases)} cases ({', '.join(categories)}), "
        f"checks {len(config.checks.required.rules) if config.checks.required else 0} required, "
        f"rubric {len(config.rubric.criteria)} criteria"
    )
    for note in notes:
        print(f"note: {note}")
    return EXIT_OK


def _run_init(args: argparse.Namespace) -> int:
    target: Path = args.dir
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / "qagent.toml"
    dataset_path = target / "dataset.jsonl"
    if config_path.exists():
        raise ValueError(f"{config_path} already exists; not overwriting it")
    config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    if not dataset_path.exists():
        dataset_path.write_text(DATASET_TEMPLATE, encoding="utf-8")
    print(f"wrote {config_path} and {dataset_path}; edit the module names, then: ")
    print(f"  qagent check --config {config_path}")
    return EXIT_OK


def _run_compare(args: argparse.Namespace) -> int:
    diff = compare(args.a, args.b)
    if args.markdown:
        write_diff_markdown(diff, args.markdown)
    print(f"{diff.a} -> {diff.b}: {len(diff.changed_cases)} changed case(s)")
    return EXIT_OK


def load_guarded(spec: str) -> GuardedAgent[Any]:
    """Resolve ``module:factory`` and call it, with errors a human can act on."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"--spec must look like package.module:factory, got {spec!r}")
    module = importlib.import_module(module_name)
    factory: Callable[[], object] = getattr(module, attribute)
    if not callable(factory):
        raise ValueError(f"{spec} is not callable")
    guarded = factory()
    if not isinstance(guarded, GuardedAgent):
        raise ValueError(f"{spec} must return a GuardedAgent, got {type(guarded).__name__}")
    return guarded
