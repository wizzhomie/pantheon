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
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .context import ProjectProfile, detect_profile, read_profile, write_profile
from .evals import Dataset, compare, evaluate, write_diff_markdown, write_json, write_markdown
from .project import ProjectConfig, build_guarded, validate
from .runtime.facade import GuardedAgent
from .verify import (
    build_plan,
    build_report,
    decide,
    describe_plan,
    findings_from_run,
    read_policy,
    run_policy,
    write_report,
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
        if args.command == "run":
            return _run_command(args)
        if args.command == "verify":
            return _run_verify(args)
        return _run_compare(args)
    except (ValueError, ImportError, AttributeError, OSError) as exc:
        print(f"qagent: {exc}", file=sys.stderr)
        return EXIT_FAILURE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qagent",
        description="Evaluate a dataset against a preset and compare reports.",
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
    policy = read_policy(args.policy)
    profile = _profile_or_none(repo)
    plan = build_plan(policy, profile)
    budget = policy.budget
    if args.dry_run:
        print(
            f"{policy.name}: {len(plan)} checks, "
            f"budget {budget.max_seconds:g}s / {budget.max_checks} checks"
        )
        for line in describe_plan(plan):
            print(f"  {line}")
        return EXIT_OK
    run, target = asyncio.run(run_policy(policy, repo, out_dir=args.out, profile=profile))
    print(f"{run.run_id}: {len(run.checks)} checks in {run.spent_seconds:.1f}s -> {target}")
    for check in run.checks:
        detail = f"  ({check.reason})" if check.reason else ""
        print(f"  {check.status:14} {check.check_id}{detail}")
    blocking = run.blocking()
    if blocking:
        print(f"blocking: {', '.join(check.check_id for check in blocking)}")
        return EXIT_FAILURE
    return EXIT_OK


def _run_verify(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    policy_path = args.policy or repo / ".qagent" / "policy.toml"
    if not Path(policy_path).is_file():
        raise ValueError(
            f"no policy at {policy_path}; point --policy at one "
            "(the file lives in the project, in .qagent/policy.toml)"
        )
    policy = read_policy(policy_path)
    profile = _profile_or_none(repo)
    plan = build_plan(policy, profile)
    if args.dry_run:
        print(
            f"{policy.name}: {len(plan)} checks, "
            f"budget {policy.budget.max_seconds:g}s / {policy.budget.max_checks} checks"
        )
        for line in describe_plan(plan):
            print(f"  {line}")
        return EXIT_OK

    run, target = asyncio.run(run_policy(policy, repo, out_dir=args.out, profile=profile))
    findings = findings_from_run(run)
    verdict = decide(run, findings)
    report = build_report(run, findings, verdict)
    json_path, md_path = write_report(report, target)

    print(f"{run.run_id}: {len(run.checks)} checks in {run.spent_seconds:.1f}s -> {target}")
    for check in run.checks:
        detail = f"  ({check.reason})" if check.reason else ""
        print(f"  {check.status:14} {check.check_id}{detail}")
    print(f"verdict: {verdict.headline}")
    for reason in verdict.reasons:
        print(f"  {reason.code}: {reason.detail}")
    print(f"report: {json_path} and {md_path}")
    if verdict.status == "hold":
        return EXIT_FAILURE
    if verdict.status == "inconclusive":
        return EXIT_INCONCLUSIVE
    return EXIT_OK


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
