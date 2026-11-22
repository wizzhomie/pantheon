"""Command line: evaluate a dataset and compare two reports.

The library owns the loop; the project owns its wiring. That is why ``eval`` takes a
``module:factory`` reference instead of a preset name: building clients, choosing a replay
directory and reading keys is project code, and the CLI must not grow a second, half-working
configuration system next to it.

The factory is a zero-argument callable returning a :class:`GuardedAgent`. ``--mode``
overrides its mode, which is how a baseline and a guarded report are produced from one
setup.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .evals import Dataset, compare, evaluate, write_diff_markdown, write_json, write_markdown
from .runtime.facade import GuardedAgent

EXIT_OK = 0
EXIT_FAILURE = 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "eval":
            return _run_eval(args)
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
    run.add_argument(
        "--spec",
        required=True,
        metavar="MODULE:FACTORY",
        help="zero-argument factory returning GuardedAgent, e.g. examples.intake.runner:build",
    )
    run.add_argument("--dataset", required=True, type=Path, help="dataset JSONL")
    run.add_argument("--out", required=True, type=Path, help="JSON report to write")
    run.add_argument("--markdown", type=Path, help="Markdown report to write")
    run.add_argument("--mode", choices=["guarded", "baseline", "shadow"])
    run.add_argument("--concurrency", type=int, default=4)

    diff = subcommands.add_parser("compare", help="compare two JSON reports")
    diff.add_argument("a", type=Path)
    diff.add_argument("b", type=Path)
    diff.add_argument("--markdown", type=Path)
    return parser


def _run_eval(args: argparse.Namespace) -> int:
    guarded = load_guarded(args.spec)
    if args.mode:
        guarded = guarded.with_mode(args.mode)
    dataset = Dataset.load(args.dataset)
    report = asyncio.run(evaluate(guarded, dataset, concurrency=args.concurrency))
    write_json(report, args.out)
    if args.markdown:
        write_markdown(report, args.markdown)
    print(
        f"{report.run_id}: {report.metrics.cases} cases, "
        f"pass {report.metrics.pass_rate_final:.1%} -> {args.out}"
    )
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
