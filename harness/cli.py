"""The ``pantheon`` command: the harness's own entry point, and the one a person types.

Exit codes are part of the contract, because a script reads them: 0 done, 2 the caller asked
for something that cannot exist, 3 not built yet. ``up`` and ``down`` use the last one on
purpose — a box with no agent is honest about it instead of printing "started".
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from . import (
    archivum,
    augustus,
    aurelius,
    caesar,
    campaign,
    container,
    contour,
    contradiction,
    debt,
    executor,
    fluxus,
    frame,
    mutation,
    preflight,
    rails,
    records,
    reviewer,
    runner,
    sensitivity,
    spec,
    study,
)
from . import box as boxes

DEFAULT_CASTRA = Path("~/pantheon/castra")
NAME_HELP = "castrum name (default: the only one)"
EXIT_REFUSED = 3
EXIT_CANNOT = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _execute(args)
    except boxes.HarnessError as error:
        print(f"pantheon: {error}", file=sys.stderr)
        return EXIT_CANNOT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pantheon",
        description="A castrum per project: create it, describe it, and start its agent.",
    )
    parser.add_argument(
        "--castra",
        default=None,
        help="where castra live (default: $PANTHEON_CASTRA, else ~/pantheon/castra)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    new = commands.add_parser("new", help="create a castrum for a project")
    new.add_argument("project", nargs="?", default=".", help="path to the project (default: .)")
    new.add_argument("--name", default=None, help="box name (default: from the directory name)")
    new.add_argument("--image", default=None, help="pinned agent image, tag or digest")
    new.set_defaults(handler=_new)

    status = commands.add_parser("status", help="describe a castrum and its project, reading only")
    status.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    status.add_argument("--json", action="store_true", help="the same facts as one JSON object")
    status.set_defaults(handler=_status)

    verify = commands.add_parser("verify", help="run trajan on the project's change")
    verify.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    change = verify.add_mutually_exclusive_group()
    change.add_argument(
        "--since",
        default=None,
        help="review `git diff REV`; needs --review or --reviewer, since a change needs a reader",
    )
    change.add_argument("--diff", default=None, help="a unified diff to review; needs --reviewer")
    verify.add_argument("--out", default=None, help="run directory (default: castrum tabularium/)")
    verify.add_argument("--trajan", default=None, help="the trajan checkout to call")
    verify.add_argument("--python", default=None, help="the interpreter that runs trajan")
    verify.add_argument("--reviewer", default=None, help="MODULE:FACTORY, passes --reviewer")
    verify.add_argument(
        "--review",
        action="store_true",
        help="have the box's own agent read the change (costs money; needs --diff or --since)",
    )
    review = verify.add_argument_group("the box's agent as the reviewer")
    review.add_argument("--review-model", default=None, help="model that reads the change")
    review.add_argument(
        "--review-seconds", type=int, default=None, help="how long the reading may take"
    )
    verify.add_argument("--json", action="store_true", help="the answer as one JSON object")
    verify.set_defaults(handler=_verify)

    run = commands.add_parser("run", help="gate a change by the task's contract, then apply it")
    run.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    run.add_argument(
        "--contract", required=True, help="mandata/<id>.contract.toml: what this attempt may touch"
    )
    run.add_argument("--patch", required=True, help="the unified diff the attempt proposes")
    run.add_argument("--json", action="store_true", help="the attempt as one JSON object")
    run.set_defaults(handler=_run)

    arch = commands.add_parser(
        "arch", help="Aurelius: check the project against its declared architecture"
    )
    arch.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    arch.add_argument(
        "--model",
        default=None,
        help=f"architecture model (default: <project>/{aurelius.MODEL_FILE})",
    )
    arch.add_argument(
        "--tool", default=None, help="the boundary tool (default: lint-imports, or $PANTHEON_LINT)"
    )
    arch.add_argument("--json", action="store_true", help="the answer as one JSON object")
    arch.set_defaults(handler=_arch)

    approve = commands.add_parser(
        "approve", help="accept the frame a project declares, as a record in the box"
    )
    approve.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    approve.add_argument(
        "--by", default="owner", help="who accepts the frame: a name in the record"
    )
    approve.add_argument("--note", default="", help="why this frame was accepted")
    approve.add_argument("--json", action="store_true", help="the answer as one JSON object")
    approve.set_defaults(handler=_approve)

    spec_parser = commands.add_parser(
        "spec",
        help="a spec package: import it, accept it, and show its standing",
        description=(
            "A feature task starts only from an accepted spec package. `spec <id> --from <dir>` "
            "imports a package built from manifest.toml, spec.md and task entries; `spec <id> "
            '--accept --note "..."` writes the acceptance; `spec <id>` shows the standing.'
        ),
    )
    spec_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    spec_parser.add_argument("package", nargs="?", default=None, help="spec package id")
    action = spec_parser.add_mutually_exclusive_group()
    action.add_argument(
        "--from",
        dest="source",
        default=None,
        metavar="DIR",
        help="import the package in this directory; the id comes from its manifest",
    )
    action.add_argument(
        "--accept", action="store_true", help="accept the imported package as the owner's basis"
    )
    spec_parser.add_argument("--note", default="", help="the owner's words on the acceptance")
    spec_parser.add_argument("--by", default="owner", help="who accepts (owner)")
    spec_parser.add_argument("--json", action="store_true", help="the answer as one JSON object")
    spec_parser.set_defaults(handler=_spec)

    rails_parser = commands.add_parser(
        "rails",
        help="the project's frame: brief, goals, milestones, metrics, principles",
        description=(
            "A task names the milestone its work serves; the rails are what makes that name mean "
            "something. `rails` shows the frame and its standing, `rails --approve` records that "
            "this frame is the basis the owner accepted."
        ),
    )
    rails_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    rails_parser.add_argument(
        "--approve", action="store_true", help="accept the frame as the basis the work serves"
    )
    rails_parser.add_argument(
        "--close",
        default=None,
        metavar="MILESTONE",
        help="record that a milestone's closing condition is met, and why",
    )
    rails_parser.add_argument("--by", default="owner", help="who accepts the frame")
    rails_parser.add_argument("--note", default="", help="why this frame was accepted")
    rails_parser.add_argument(
        "--revise",
        choices=rails.KINDS,
        default=None,
        help="record a departure from the accepted frame: add, replace or revisit",
    )
    rails_parser.add_argument(
        "--breaks",
        default=None,
        help="what breaks if the revision is made; heavy revisions require it",
    )
    rails_parser.add_argument(
        "--lose",
        default=None,
        help="what we lose if the revision is not made; heavy revisions require it",
    )
    rails_parser.add_argument(
        "--delegate",
        action="append",
        default=[],
        metavar="QUESTION=DEFAULT",
        help="a question the owner left to the box, and the default that was taken",
    )
    rails_parser.add_argument("--json", action="store_true", help="the same facts as one object")
    rails_parser.set_defaults(handler=_rails)

    flow_parser = commands.add_parser(
        "flow",
        help="the stream: a brief, a feed of attempts, and a queue of tasks",
        description=(
            "A task answers whether a change passed. A stream answers what is being built and "
            "what has already been tried: `flow` shows the brief, the queue and the feed; "
            "`--enqueue` puts a task down, `--next` runs the oldest one, `--discard` keeps a "
            "reason for an abandoned approach, and `--mark-read` says the verdicts were read."
        ),
    )
    flow_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    flow_parser.add_argument("--brief", default=None, metavar="WHAT", help="say what is built")
    flow_parser.add_argument("--why", default=None, help="why it is built; the brief needs both")
    flow_parser.add_argument("--milestone", default=None, help="the milestone the brief serves")
    flow_parser.add_argument("--enqueue", default=None, metavar="TASK", help="queue a task file")
    flow_parser.add_argument(
        "--next", action="store_true", help="take the oldest queued task and run it"
    )
    flow_parser.add_argument(
        "--discard", default=None, metavar="TASK", help="record an attempt as abandoned"
    )
    flow_parser.add_argument("--reason", default=None, help="why it was queued or abandoned")
    flow_parser.add_argument(
        "--mark-read", action="store_true", help="mark every verdict as read"
    )
    flow_parser.add_argument("--by", default="owner", help="who is speaking in the record")
    flow_parser.add_argument("--routes", default=None, help="routing table for --next")
    flow_parser.add_argument("--trajan", default=None, help="the trajan checkout for --next")
    flow_parser.add_argument("--python", default=None, help="the interpreter that runs trajan")
    flow_parser.add_argument("--executor", default=None, help="the command producing the change")
    flow_parser.add_argument("--json", action="store_true", help="the answer as one JSON object")
    flow_parser.set_defaults(handler=_flow)

    study_parser = commands.add_parser(
        "study",
        help="the R&D cycle: question, experiments, numbers, a decision by code",
        description=(
            "A study is a file in the project: one question, when it counts as closed, and "
            "experiments that declare a hypothesis, metrics with a threshold and a pattern, and "
            "runs with a parameter matrix. `study` lists them; `--run` runs the next experiment, "
            "aggregates the numbers, gives the hypothesis its verdict, and follows the "
            "confirmed/refuted decision the file declares."
        ),
    )
    study_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    study_parser.add_argument(
        "--run", default=None, metavar="RESEARCH", help="run the next experiment of a research"
    )
    study_parser.add_argument(
        "--experiment", default=None, help="run this experiment instead of the next one"
    )
    study_parser.add_argument("--json", action="store_true", help="the answer as one JSON object")
    study_parser.set_defaults(handler=_study)

    ledger_parser = commands.add_parser(
        "records",
        help="digest the box's finished tasks into one bounded list of <task>/<stamp> addresses",
    )
    ledger_parser.add_argument(
        "name", nargs="?", default=None, help=NAME_HELP
    )
    ledger_parser.add_argument("--json", action="store_true", help="the digest as JSON")
    ledger_parser.set_defaults(handler=_records)

    campaign_parser = commands.add_parser(
        "campaign", help="a plan in the repository, and the verdict the code computes about it"
    )
    campaign_parser.add_argument(
        "name", nargs="?", default=None, help=NAME_HELP
    )
    campaign_parser.add_argument(
        "--file",
        default=None,
        help="one campaign file (default: .pantheon/expeditiones/*.toml)",
    )
    campaign_parser.add_argument(
        "--routes",
        default=None,
        help=f"routing table (default: ${augustus.ROUTES_ENV}, else built-in)",
    )
    campaign_parser.add_argument("--json", action="store_true", help="the answer as JSON")
    campaign_parser.set_defaults(handler=_campaign)

    step = commands.add_parser(
        "step", help="run one step of a campaign through the same gates, checks and commit"
    )
    step.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    step.add_argument("--campaign", required=True, help="campaign id or path to its file")
    step.add_argument("--step", required=True, help="the step id inside that campaign")
    step.add_argument(
        "--reason",
        default="",
        help="why the plan changed since the last step; a silent plan change is refused",
    )
    step.add_argument(
        "--retry",
        action="store_true",
        help="run a step that already ran again, when the retry rule allows it",
    )
    step.add_argument(
        "--routes",
        default=None,
        help=f"routing table (default: ${augustus.ROUTES_ENV}, else built-in)",
    )
    step.add_argument("--trajan", default=None, help="the trajan checkout to call")
    step.add_argument("--python", default=None, help="the interpreter that runs trajan")
    step.add_argument(
        "--review", action="store_true", help="have the box's agent read the step's change"
    )
    step.add_argument(
        "--no-review",
        action="store_true",
        help="do not read the change, whatever persona/reviewer.toml says",
    )
    step.add_argument("--review-model", default=None, help="model that reads the change")
    step.add_argument(
        "--review-seconds", type=int, default=None, help="how long the reading may take"
    )
    step.add_argument("--json", action="store_true", help="the answer as one JSON object")
    step.set_defaults(handler=_step)

    advance = commands.add_parser(
        "advance",
        help="run an auto campaign by its own rule: ready steps, retries, a stop with its reason",
    )
    advance.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    advance.add_argument("--campaign", required=True, help="campaign id or path to its file")
    advance.add_argument(
        "--reason",
        default="",
        help="why the plan changed since the last step; a silent plan change is refused",
    )
    advance.add_argument(
        "--routes",
        default=None,
        help=f"routing table (default: ${augustus.ROUTES_ENV}, else built-in)",
    )
    advance.add_argument("--trajan", default=None, help="the trajan checkout to call")
    advance.add_argument("--python", default=None, help="the interpreter that runs trajan")
    advance.add_argument(
        "--review", action="store_true", help="have the box's agent read each step's change"
    )
    advance.add_argument(
        "--no-review",
        action="store_true",
        help="do not read the changes, whatever persona/reviewer.toml says",
    )
    advance.add_argument("--review-model", default=None, help="model that reads the change")
    advance.add_argument(
        "--review-seconds", type=int, default=None, help="how long one reading may take"
    )
    advance.add_argument("--json", action="store_true", help="the answer as one JSON object")
    advance.set_defaults(handler=_advance)

    dispatch = commands.add_parser(
        "dispatch", help="Augustus: run a task through the contours its scope routes to"
    )
    dispatch.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    dispatch.add_argument("--task", required=True, help="mandata/<id>.toml: goal, scope, kind")
    dispatch.add_argument(
        "--routes",
        default=None,
        help=f"routing table (default: ${augustus.ROUTES_ENV}, else built-in)",
    )
    dispatch.add_argument("--trajan", default=None, help="the trajan checkout to call")
    dispatch.add_argument("--python", default=None, help="the interpreter that runs trajan")
    dispatch.add_argument("--tool", default=None, help="boundary tool for the architecture stage")
    dispatch.add_argument(
        "--executor",
        default=None,
        help="override the box's executor command for this run (persona/executor.toml)",
    )
    dispatch.add_argument(
        "--review",
        action="store_true",
        help="have the box's agent read the change too (one more paid call per task)",
    )
    dispatch.add_argument(
        "--no-review",
        action="store_true",
        help="do not read the change, whatever persona/reviewer.toml says",
    )
    dispatch.add_argument(
        "--no-commit",
        action="store_true",
        help="leave a green change in the tree instead of committing it after the verdict",
    )
    dispatch.add_argument("--review-model", default=None, help="model that reads the change")
    dispatch.add_argument(
        "--review-seconds", type=int, default=None, help="how long the reading may take"
    )
    dispatch.add_argument("--json", action="store_true", help="the answer as one JSON object")
    dispatch.set_defaults(handler=_dispatch)

    resume = commands.add_parser(
        "resume",
        help="Augustus: continue a task that stopped before its verdict, from its artifacts",
    )
    resume.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    resume.add_argument("--task", required=True, help="the task id whose artifacts to continue")
    resume.add_argument(
        "--retry",
        action="store_true",
        help="ask the executor again instead of reusing the recorded change",
    )
    resume.add_argument(
        "--routes",
        default=None,
        help=f"routing table (default: ${augustus.ROUTES_ENV}, else built-in)",
    )
    resume.add_argument(
        "--max-cents", type=float, default=None, help="raise the cent limit (whole cents)"
    )
    resume.add_argument("--max-seconds", type=int, default=None, help="raise the second limit")
    resume.add_argument("--trajan", default=None, help="the trajan checkout to call")
    resume.add_argument("--python", default=None, help="the interpreter that runs trajan")
    resume.add_argument(
        "--executor",
        default=None,
        help="override the box's executor command for this run (persona/executor.toml)",
    )
    resume.add_argument(
        "--review",
        action="store_true",
        help="have the box's agent read the change (costs money; the change must still be in "
        "the tree)",
    )
    resume.add_argument(
        "--no-review",
        action="store_true",
        help="do not read the change, whatever persona/reviewer.toml says",
    )
    resume.add_argument(
        "--no-commit",
        action="store_true",
        help="leave a green change in the tree instead of committing it after the verdict",
    )
    resume.add_argument("--review-model", default=None, help="model that reads the change")
    resume.add_argument(
        "--review-seconds", type=int, default=None, help="how long the reading may take"
    )
    resume.add_argument("--json", action="store_true", help="the answer as one JSON object")
    resume.set_defaults(handler=_resume)

    amend = commands.add_parser(
        "amend",
        help="Augustus: write a successor task that names its parent, the reason and the diff",
    )
    amend.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    amend.add_argument(
        "--task", required=True, help="the task id whose frozen record is amended"
    )
    amend.add_argument("--why", required=True, help="why the task grew: the successor's reason")
    amend.add_argument(
        "--add-path",
        action="append",
        default=[],
        metavar="PATH",
        help="widen the scope by one path or glob (repeatable)",
    )
    amend.add_argument(
        "--budget",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="change one budget key, e.g. max_cents=8 (repeatable)",
    )
    amend.add_argument(
        "--max-steps", type=int, default=None, help="raise or lower the call ceiling"
    )
    amend.add_argument("--json", action="store_true", help="the amendment as one JSON object")
    amend.set_defaults(handler=_amend)

    park = commands.add_parser(
        "park",
        help="Augustus: set a task aside with a reason; it stays readable and does not run",
    )
    park.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    park.add_argument("--task", required=True, help="the task id to set aside")
    park.add_argument("--why", required=True, help="why it is set aside")
    park.add_argument("--json", action="store_true", help="the park record as one JSON object")
    park.set_defaults(handler=_park)

    explain = commands.add_parser(
        "explain",
        help="Augustus: what a dispatch would decide, before it pays for a call (reads only)",
    )
    explain.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    explain.add_argument(
        "--task", required=True, help="a task file (mandata/<id>.toml) or a frozen task id"
    )
    explain.add_argument(
        "--routes",
        default=None,
        help=f"routing table (default: ${augustus.ROUTES_ENV}, else built-in)",
    )
    explain.add_argument(
        "--executor",
        default=None,
        help="override the box's executor command for this explanation",
    )
    explain.add_argument(
        "--review", action="store_true", help="assume a reader will be asked for the change"
    )
    explain.add_argument(
        "--no-review",
        action="store_true",
        help="assume the change will not be read, whatever persona/reviewer.toml says",
    )
    explain.add_argument("--review-model", default=None, help="model that would read the change")
    explain.add_argument(
        "--review-seconds", type=int, default=None, help="how long the reading may take"
    )
    explain.add_argument("--json", action="store_true", help="the explanation as one object")
    explain.set_defaults(handler=_explain)

    preflight_parser = commands.add_parser(
        "preflight",
        help="Augustus: run the task's own reproduction on the base and on a fix (reads only)",
    )
    preflight_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    preflight_parser.add_argument(
        "--task", required=True, help="a task file (mandata/<id>.toml) or a frozen task id"
    )
    preflight_parser.add_argument(
        "--fix",
        default=None,
        help="a commit known to fix the defect: the check must pass there",
    )
    preflight_parser.add_argument("--trajan", default=None, help="the trajan checkout to call")
    preflight_parser.add_argument(
        "--python", default=None, help="the interpreter that runs the check"
    )
    preflight_parser.add_argument("--json", action="store_true", help="the answer as one object")
    preflight_parser.set_defaults(handler=_preflight)

    intake_parser = commands.add_parser(
        "intake",
        help="Augustus: check the task contract before the work is handed on (reads only)",
    )
    intake_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    intake_parser.add_argument(
        "--task", required=True, help="a task file (mandata/<id>.toml) or a frozen task id"
    )
    intake_parser.add_argument("--json", action="store_true", help="the answer as one object")
    intake_parser.set_defaults(handler=_intake)

    sensitivity_parser = commands.add_parser(
        "sensitivity",
        help="Augustus: reverse each piece of the change and see whether the check notices",
    )
    sensitivity_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    sensitivity_parser.add_argument(
        "--task", required=True, help="a task file carrying the change and its check"
    )
    sensitivity_parser.add_argument(
        "--max-units", type=int, default=sensitivity.LIMIT_PIECES, help="how many pieces to reverse"
    )
    sensitivity_parser.add_argument("--trajan", default=None, help="the trajan checkout to call")
    sensitivity_parser.add_argument(
        "--python", default=None, help="the interpreter that runs the check"
    )
    sensitivity_parser.add_argument(
        "--base", default="HEAD", help="the commit the change applies to (default: HEAD)"
    )
    sensitivity_parser.add_argument("--json", action="store_true", help="the answer as one object")
    sensitivity_parser.set_defaults(handler=_sensitivity)

    mutate_parser = commands.add_parser(
        "mutate",
        help="Augustus: break the code on purpose and see whether the check notices",
    )
    mutate_parser.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    mutate_parser.add_argument(
        "--file", required=True, help="the audit: the check and the breaks it should notice"
    )
    mutate_parser.add_argument(
        "--max-breaks",
        type=int,
        default=mutation.LIMIT_BREAKS,
        help="how many breaks to apply",
    )
    mutate_parser.add_argument("--trajan", default=None, help="the trajan checkout to call")
    mutate_parser.add_argument(
        "--python", default=None, help="the interpreter that runs the check"
    )
    mutate_parser.add_argument("--json", action="store_true", help="the reading as one object")
    mutate_parser.set_defaults(handler=_mutate)

    history = commands.add_parser(
        "history", help="Augustus: what was changed here and why, from the box's archive"
    )
    history.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    history.add_argument(
        "--path",
        action="append",
        default=[],
        help="a path or glob to narrow by, e.g. src/app/** (repeatable)",
    )
    history.add_argument(
        "--diff", default=None, help="print the stored change for one address: <task>/<stamp>"
    )
    history.add_argument("--limit", type=int, default=10, help="how many changes to show (10)")
    history.add_argument("--json", action="store_true", help="the same answer as one object")
    history.set_defaults(handler=_history)

    accept = commands.add_parser(
        "accept", help="the owner's mark on a closed change: accepted, or not"
    )
    accept.add_argument("name", nargs="?", default=None, help=NAME_HELP)
    accept.add_argument("address", help="a closed change: <task>/<stamp>, as `history` prints it")
    accept.add_argument(
        "--reject", action="store_true", help="record a refusal instead of an acceptance"
    )
    accept.add_argument("--note", default="", help="why, in the owner's words")
    accept.add_argument("--by", default="owner", help="who decided (owner)")
    accept.add_argument("--json", action="store_true", help="the mark as one object")
    accept.set_defaults(handler=_accept)

    listing = commands.add_parser("list", help="every box under the boxes directory")
    listing.set_defaults(handler=_list)

    up = commands.add_parser("up", help="start the box's container (mount only, no agent yet)")
    up.add_argument("name", nargs="?", default=None, help="box name")
    up.add_argument("--image", default=None, help="pin this image in castrum.toml, then start")
    up.set_defaults(handler=_up)

    down = commands.add_parser("down", help="stop the box's container, keeping the box's files")
    down.add_argument("name", nargs="?", default=None, help="box name")
    down.set_defaults(handler=_down)
    return parser


def _execute(args: argparse.Namespace) -> int:
    handler: object = getattr(args, "handler", None)
    if not callable(handler):
        raise boxes.HarnessError("no command given")
    result: int = handler(args, _castra_root(args))
    return result


def _castra_root(args: argparse.Namespace) -> Path:
    chosen = args.castra or os.environ.get("PANTHEON_CASTRA") or str(DEFAULT_CASTRA)
    return Path(chosen).expanduser()


def _new(args: argparse.Namespace, root: Path) -> int:
    box = boxes.new_box(root, Path(args.project), args.name, args.image)
    example = box.root / executor.EXAMPLE_FILE
    example.parent.mkdir(parents=True, exist_ok=True)
    if not example.exists():
        example.write_text(executor.EXAMPLE, encoding="utf-8")
    reader = box.root / reviewer.EXAMPLE_FILE
    if not reader.exists():
        reader.write_text(reviewer.EXAMPLE, encoding="utf-8")
    validator = box.root / contradiction.EXAMPLE_FILE
    if not validator.exists():
        validator.write_text(contradiction.EXAMPLE, encoding="utf-8")
    print(f"castrum {box.name} created at {box.root}")
    print(f"  project  {box.project}")
    print(f"  image    {box.image or 'not pinned yet (`pantheon up` pins it)'}")
    print(f"  executor {executor.describe(executor.read_config(box.root / executor.PROFILE_FILE))}")
    print(f"  example  {example}")
    print(f"  reader   {reviewer.describe(reviewer.read_config(box.root / reviewer.PROFILE_FILE))}")
    checker = contradiction.read_config(box.root / contradiction.PROFILE_FILE)
    print(f"  check    {contradiction.describe(checker)}")
    print(f"next: pantheon status {box.name}")
    return 0


def _status(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    report = boxes.status(box)
    tasks = augustus.recent_tasks(box.root)
    facts = container.state(box)
    agent_status = container.agent_status(box) if facts.running else None
    agent = executor.read_config(box.root / executor.PROFILE_FILE)
    comparison = contradiction.read_config(box.root / contradiction.PROFILE_FILE)
    if args.json:
        payload = boxes.status_json(report)
        payload["container"] = {
            "running": facts.running,
            "name": facts.name,
            "image": facts.image,
            "detail": facts.detail,
            "agent": None
            if agent_status is None
            else {
                "running": agent_status.running,
                "gateway": agent_status.gateway,
                "model": agent_status.model,
                "provider": agent_status.provider,
                "platforms": agent_status.platforms,
            },
        }
        payload["contracts"] = {
            "declared": comparison.declared,
            "enabled": comparison.enabled,
            "model": comparison.model,
            "max_cents": comparison.max_cents,
        }
        payload["executor"] = {
            "declared": agent.declared,
            "command": None if agent.command is None else list(agent.command),
            "model": agent.model,
            "max_seconds": agent.max_seconds,
            "max_cents": agent.max_cents,
        }
        payload["frame"] = frame.standing(box).json()
        payload["rails"] = rails.standing(box).json()
        payload["debt"] = debt.tally(box).json()
        fresh, stored = records.state(box)
        payload["records"] = {
            "count": fresh.count,
            "total": fresh.total,
            "digest": fresh.digest,
            "stored": stored,
        }
        payload["tasks"] = [
            {
                "id": task.id,
                "state": task.state,
                "at": task.at,
                "verify": task.verify,
                "record": str(task.record),
                "seconds": task.seconds,
                "reading": _reading_of(task.record),
                "unknowns": list(task.unknowns),
                "parent": task.parent,
                "why": task.why,
                "replaced_by": task.replaced_by,
                "parked": task.parked,
            }
            for task in tasks
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    lines = boxes.render_status(report)
    lines.insert(-1, f"container  {facts.line}")
    lines.insert(
        -1,
        "agent      "
        + (
            agent_status.line
            if agent_status is not None
            else ("not running" if not facts.running else "no answer")
        ),
    )
    lines.insert(-1, f"executor   {executor.describe(agent)}")
    reader = reviewer.read_config(box.root / reviewer.PROFILE_FILE)
    lines.insert(-1, f"reviewer   {reviewer.describe(reader)}")
    lines.insert(-1, f"contracts  {contradiction.describe(comparison)}")
    lines.insert(-1, f"frame      {frame.standing(box).line()}")
    lines.insert(-1, f"rails      {rails.standing(box).line()}")
    lines.insert(-1, f"debt       {debt.tally(box).line()}")
    lines.insert(-1, f"records    {_records_line(box)}")
    print("\n".join(lines))
    if tasks:
        for task in tasks:
            verify = f"verify {task.verify}" if task.verify else "not verified"
            read = _reading_brief(task.record)
            took = f"  in {task.seconds:.2f}s" if task.seconds is not None else ""
            link = ""
            if task.parent is not None:
                link += f"  amends {task.parent}"
            if task.replaced_by is not None:
                link += f"  replaced by {task.replaced_by}"
            if task.parked is not None:
                link += f"  parked: {task.parked}"
            print(
                f"task       {task.id}  {task.state}  {task.at}  {verify}{took}{read}{link}"
            )
        for unknown in tasks[-1].unknowns:
            print(f"unknown    {unknown}")
    else:
        print("tasks      none dispatched yet")
    return 0


def _records(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    digest = records.write(box)
    if args.json:
        print(json.dumps(digest.json(), indent=2, ensure_ascii=False))
    else:
        print("\n".join(records.render(digest)))
    return 0


def _reading_of(record: Path) -> dict[str, object] | None:
    """What that task read, from its dispatch record: absent means the executor did not say."""
    try:
        data: object = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    reading = data.get("reading")
    return reading if isinstance(reading, dict) else None


def _reading_brief(record: Path) -> str:
    reading = _reading_of(record)
    if reading is None:
        return ""
    files = reading.get("files")
    chars = reading.get("chars")
    if not isinstance(files, int) or not isinstance(chars, int):
        return ""
    return f"  read {files} file(s), {chars} chars"


def _records_line(box: boxes.Box) -> str:
    """One line for ``status``: how many records, and whether the stored digest is current."""
    digest, stored = records.state(box)
    noun = "task" if digest.count == 1 else "tasks"
    if stored == "none":
        return f"{digest.count} {noun}, digest not written yet (`pantheon records` writes it)"
    if stored == "stale":
        return f"{digest.count} {noun}, digest stale — the records changed since it was written"
    return f"{digest.count} {noun}, digest fresh"


def _verify(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    policy = box.project / ".trajan" / "policy.toml"
    if not policy.is_file():
        raise boxes.HarnessError(
            f"project {box.project} has no .trajan/policy.toml: there is nothing to verify against"
        )
    if args.review and args.reviewer:
        raise boxes.HarnessError(
            "--review and --reviewer name two different readers: pick one, or drop both to run "
            "the checks only"
        )
    if (args.since or args.diff) and not (args.review or args.reviewer):
        raise boxes.HarnessError(
            "a change needs a reader: pass --review to have the box's agent read it, or "
            "--reviewer MODULE:FACTORY for your own, or drop --since/--diff to run the checks "
            "only"
        )
    if args.review and not (args.since or args.diff):
        raise boxes.HarnessError(
            "--review needs something to read: pass --diff (a file) or --since (a commit)"
        )
    trajan = contour.trajan_root(args.trajan)
    out = (
        Path(args.out)
        if args.out
        else contour.run_directory(box.root / "tabularium", datetime.now(UTC))
    )
    reviewer = args.reviewer
    reviewer_env: dict[str, str] | None = None
    if args.review:
        chosen = contour.box_reviewer(
            box.root, model=args.review_model, seconds=args.review_seconds
        )
        reviewer, reviewer_env = chosen.spec, chosen.env
    result = contour.run_verify(
        box.project,
        root=trajan,
        python=contour.trajan_python(trajan, args.python),
        out=out,
        since=args.since,
        diff=Path(args.diff).resolve() if args.diff else None,
        reviewer=reviewer,
        reviewer_env=reviewer_env,
        box_root=box.root,
    )
    if args.json:
        print(json.dumps(contour.result_json(result, box.project, box.name), indent=2))
    else:
        print("\n".join(contour.render_result(result, box.project)))
        print(f"box        {box.name}")
    return result.exit_code


def _run(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    contract = caesar.read_contract(Path(args.contract))
    if not box.project.is_dir():
        raise boxes.HarnessError(f"project {box.project} is gone: nothing to apply a change to")
    attempt = caesar.run_attempt(box.root, box.project, contract, Path(args.patch))
    if args.json:
        print(json.dumps(caesar.attempt_json(attempt), indent=2))
    else:
        print("\n".join(caesar.render_attempt(attempt, contract)))
    return 0 if attempt.applied else 1


def _arch(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    model_path = Path(args.model) if args.model else box.project / aurelius.MODEL_FILE
    model = aurelius.read_model(model_path)
    result = aurelius.check(box.root, box.project, model, tool=args.tool)
    if args.json:
        print(json.dumps(aurelius.result_json(result, model), indent=2))
    else:
        print("\n".join(aurelius.render_result(result, model)))
    return result.exit_code


def _approve(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    current = frame.read_frame(box.project)
    approval = frame.approve(box, by=args.by, note=args.note)
    if args.json:
        print(
            json.dumps(
                {
                    "approved": approval.digest,
                    "at": approval.at,
                    "by": approval.by,
                    "record": str(approval.path),
                    "files": [
                        {"path": item.path, "digest": item.digest} for item in approval.files
                    ],
                    "missing": list(current.missing),
                },
                indent=2,
            )
        )
        return 0
    print("\n".join(frame.render_approval(approval, current)))
    return 0


def _rails(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    if args.revise is not None and args.approve:
        raise boxes.HarnessError(
            "a revision and an approval are two acts: record the revision, then accept the frame"
        )
    if args.close is not None and (args.revise is not None or args.approve):
        raise boxes.HarnessError(
            "closing a milestone is its own act: close it first, then revise or accept the frame"
        )
    if args.close is not None:
        closure = rails.close(box, args.close, args.note, by=args.by)
        if args.json:
            print(json.dumps({"closed": closure.json()}, indent=2, ensure_ascii=False))
            return 0
        print(f"closed {closure.milestone}  {closure.title}")
        print(f"record {closure.path}")
        print(f"note   {closure.note}")
        return 0
    if args.revise is not None:
        revision = rails.revise(
            box,
            args.revise,
            args.note,
            breaks=args.breaks,
            lose=args.lose,
            by=args.by,
        )
        standing = rails.standing(box)
        current = rails.read(box.project)
        if args.json:
            print(json.dumps({"revision": revision.json()}, indent=2, ensure_ascii=False))
            return 0
        print("\n".join(rails.render(standing, current, revision, rails.closures(box))))
        return 0
    if args.approve:
        delegations = tuple(_delegation(item) for item in args.delegate)
        approval = rails.approve(box, by=args.by, note=args.note, delegations=delegations)
        current = rails.read(box.project)
        if args.json:
            print(
                json.dumps(
                    {
                        "approved": approval.digest,
                        "at": approval.at,
                        "by": approval.by,
                        "record": str(approval.path),
                        "file": rails.RAILS_FILE,
                        "delegations": [
                            {"question": item.question, "default": item.default}
                            for item in approval.delegations
                        ],
                        "missing": [] if current is None else list(current.missing),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        print(
            "\n".join(
                rails.render(
                    rails.standing(box),
                    current,
                    rails.latest_revision(box),
                    rails.closures(box),
                )
            )
        )
        return 0
    standing = rails.standing(box)
    current = rails.read(box.project)
    previous = rails.latest_revision(box)
    if args.json:
        print(
            json.dumps(
                rails.payload(standing, current, previous, rails.closures(box)),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    print("\n".join(rails.render(standing, current, previous, rails.closures(box))))
    return 0


def _delegation(value: str) -> rails.Delegation:
    question, separator, default = value.partition("=")
    if not separator or not question.strip() or not default.strip():
        raise boxes.HarnessError(
            f"--delegate {value!r} is not QUESTION=DEFAULT: a delegation needs both sides"
        )
    return rails.Delegation(question=question.strip(), default=default.strip())


def _study(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    if args.experiment is not None and args.run is None:
        raise boxes.HarnessError(
            "--experiment chooses what --run runs: pass `--run <research>` with it"
        )
    if args.run is None:
        if args.json:
            print(json.dumps(study.payload(box), indent=2, ensure_ascii=False))
        else:
            print("\n".join(study.render_all(box)))
        return 0
    research = study.read(box.project, args.run)
    experiment = study.next_experiment(
        research, study.latest(box, research.id), args.experiment
    )
    outcome = study.run_experiment(box, research, experiment)
    if args.json:
        print(json.dumps(study.outcome_json(outcome), indent=2, ensure_ascii=False))
    else:
        print("\n".join(study.render(outcome)))
    return 0


def _flow(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    actions = [
        args.brief is not None,
        args.enqueue is not None,
        args.discard is not None,
        args.mark_read,
        args.next,
    ]
    if sum(actions) > 1:
        raise boxes.HarnessError(
            "one stream action at a time: a brief, an enqueue, a discard, a mark-read, or a next"
        )
    if args.brief is not None:
        if not args.why:
            raise boxes.HarnessError("a brief says what and why: `--why` is empty")
        if not args.milestone:
            raise boxes.HarnessError("a brief serves a milestone: `--milestone` is empty")
        brief = fluxus.write_brief(
            box, milestone=args.milestone, what=args.brief, why=args.why, by=args.by
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "brief": {
                            "at": brief.at,
                            "by": brief.by,
                            "milestone": brief.milestone,
                            "what": brief.what,
                            "why": brief.why,
                        }
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        print("\n".join(fluxus.render(box)))
        return 0
    if args.enqueue is not None:
        task_id, queued = fluxus.enqueue(box, Path(args.enqueue), reason=args.reason or "")
        if args.json:
            print(json.dumps({"task": task_id, "queued": str(queued)}, indent=2))
            return 0
        print(f"queued     {task_id} -> {queued}")
        return 0
    if args.discard is not None:
        entry = fluxus.discard(box, args.discard, args.reason, by=args.by)
        if args.json:
            print(json.dumps(entry.json(), indent=2, ensure_ascii=False))
            return 0
        print(f"discarded  {entry.task}  {entry.reason}")
        return 0
    if args.mark_read:
        entry = fluxus.mark_read(box, by=args.by)
        if args.json:
            print(json.dumps(entry.json(), indent=2, ensure_ascii=False))
            return 0
        print(f"read       {entry.at}  by {entry.reason}")
        return 0
    if args.next:
        return _flow_next(box, args)
    if args.json:
        print(json.dumps(fluxus.payload(box), indent=2, ensure_ascii=False))
    else:
        print("\n".join(fluxus.render(box)))
    return 0


def _flow_next(box: boxes.Box, args: argparse.Namespace) -> int:
    path = fluxus.next_queued(box)
    if path is None:
        raise boxes.HarnessError(
            "the stream's queue is empty: `pantheon flow --enqueue <task>` puts a task there"
        )
    held = fluxus.lock(box)
    try:
        task = augustus.read_task(path)
        routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
        trajan = contour.trajan_root(args.trajan)
        config = executor.read_config(box.root / executor.PROFILE_FILE)
        if args.executor:
            config = executor.ExecutorConfig(
                path=config.path,
                command=tuple(shlex.split(args.executor)),
                model=config.model,
                max_seconds=config.max_seconds,
                max_cents=config.max_cents,
                max_steps=config.max_steps,
            )
        result = augustus.dispatch_with_attempts(
            box,
            task,
            routes=_executor_routes(
                augustus.read_routes(Path(routes) if routes else None), task, config
            ),
            trajan_root=trajan,
            trajan_python=contour.trajan_python(trajan, args.python),
            executor_config=config,
            review=_declared_reviewer(box),
        )
        fluxus.record_verdict(box, result)
        path.unlink(missing_ok=True)
    finally:
        fluxus.unlock(held)
    if args.json:
        payload = augustus.dispatch_json(result)
        payload["stream"] = fluxus.payload(box)
        print(json.dumps(payload, indent=2))
    else:
        print("\n".join(augustus.render_dispatch(result)))
        print()
        print("\n".join(fluxus.render(box)))
    return result.exit_code


def _declared_reviewer(box: boxes.Box) -> contour.Reviewer | None:
    """The reader the box's own profile declares: `--next` has no flags to override it."""
    config = reviewer.read_config(box.root / reviewer.PROFILE_FILE)
    if not config.enabled:
        return None
    return contour.box_reviewer(
        box.root,
        model=config.model,
        max_cents=config.max_cents,
        when_paths=config.when_paths,
    )


def _campaign(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
    table = augustus.read_routes(Path(routes) if routes else None)
    files = (Path(args.file),) if args.file else campaign.campaign_files(box.project)
    if not files:
        raise boxes.HarnessError(
            f"no campaigns in {box.project / campaign.CAMPAIGN_DIR}: a plan is a file, so there "
            "is nothing to check"
        )
    reports = [campaign.report(box, campaign.read_campaign(path), routes=table) for path in files]
    if args.json:
        payload: object = [campaign.report_json(item) for item in reports]
        if len(reports) == 1:
            payload = campaign.report_json(reports[0])
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for index, item in enumerate(reports):
            if index:
                print()
            print("\n".join(campaign.render(item)))
    return 0 if all(not item.problems for item in reports) else 1


def _step(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
    table = augustus.read_routes(Path(routes) if routes else None)
    plan = campaign.read_campaign(_campaign_path(box, args.campaign))
    trajan = contour.trajan_root(args.trajan)
    result = runner.step(
        box,
        plan,
        args.step,
        table=table,
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, args.python),
        review=_reviewer(args, box),
        reason=args.reason,
        retry=args.retry,
    )
    report = campaign.report(box, plan, routes=table)
    if args.json:
        payload = {
            "dispatch": augustus.dispatch_json(result.dispatch),
            "step": {
                "id": result.step.id,
                "state": result.state.state,
                "verdict": result.state.verdict,
                "cents": result.state.cents,
                "record": result.state.record,
            },
            "campaign": campaign.report_json(report),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(augustus.render_dispatch(result.dispatch)))
        print()
        print("\n".join(campaign.render(report)))
    if result.state.state == "noop":
        return 1
    return result.dispatch.exit_code


def _advance(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
    table = augustus.read_routes(Path(routes) if routes else None)
    plan = campaign.read_campaign(_campaign_path(box, args.campaign))
    trajan = contour.trajan_root(args.trajan)
    result = runner.run(
        box,
        plan,
        table=table,
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, args.python),
        review=_reviewer(args, box),
        reason=args.reason,
    )
    if args.json:
        payload = {
            "run": runner.run_json(result),
            "campaign": campaign.report_json(result.report),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("\n".join(runner.render(result)))
    return result.exit_code


def _campaign_path(box: boxes.Box, value: str) -> Path:
    """The campaign by path or by id: a person types the id, a script may type the file."""
    direct = Path(value).expanduser()
    if direct.is_file():
        return direct
    found = box.project / campaign.CAMPAIGN_DIR / f"{value}.toml"
    if found.is_file():
        return found
    known = ", ".join(path.stem for path in campaign.campaign_files(box.project)) or "none"
    raise boxes.HarnessError(
        f"no campaign {value!r}: {found} does not exist; the project declares {known}"
    )


def _spec(args: argparse.Namespace, root: Path) -> int:
    """Import, accept or inspect a spec package: the basis a feature task needs before it runs.

    The positional grammar is ``[name] [package]``, so one argument is ambiguous: it is the
    package id unless it names a box. The spec commands in the refusals are written without a
    box name, and they have to work from inside a box as written.
    """
    cwd = Path.cwd()
    name = args.name
    package_id = args.package
    box: boxes.Box | None = None
    if package_id is None and name is not None:
        try:
            box = boxes.find_box(root, name, cwd)
        except boxes.HarnessError:
            package_id = name
            name = None
    if box is None:
        box = boxes.find_box(root, name, cwd)
    if args.source is not None:
        source = Path(args.source).expanduser()
        package = spec.read_package(source)
        if package_id is not None and package_id != package.id:
            raise boxes.HarnessError(
                f"the manifest at {source} names the package {package.id!r}, not {package_id!r}: "
                "the id comes from the manifest"
            )
        written = spec.import_package(box, package)
        if args.json:
            print(json.dumps(spec.payload_import(package, written), indent=2))
        else:
            print("\n".join(spec.render_import(package, written)))
        return 0
    if package_id is None:
        raise boxes.HarnessError(
            "spec needs a package id: `pantheon spec <id>` shows its standing and "
            "`pantheon spec <id> --from <dir>` imports one"
        )
    if args.accept:
        approval = spec.accept(box, package_id, by=args.by, note=args.note)
        if args.json:
            print(json.dumps(spec.payload_approval(approval, package_id), indent=2))
        else:
            print("\n".join(spec.render_approval(approval, package_id)))
        return 0
    current = spec.standing(box, package_id)
    if args.json:
        print(json.dumps(spec.payload_standing(current), indent=2))
    else:
        print("\n".join(spec.render_standing(current)))
    return 0 if current.state == spec.ACCEPTED else 1


def _dispatch(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    task = augustus.read_task(Path(args.task))
    routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
    trajan = contour.trajan_root(args.trajan)
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    if args.executor:
        config = executor.ExecutorConfig(
            path=config.path,
            command=tuple(shlex.split(args.executor)),
            model=config.model,
            max_seconds=config.max_seconds,
            max_cents=config.max_cents,
            max_steps=config.max_steps,
        )
    result = augustus.dispatch_with_attempts(
        box,
        task,
        routes=_executor_routes(
            augustus.read_routes(Path(routes) if routes else None), task, config
        ),
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, args.python),
        tool=args.tool,
        executor_config=config,
        review=_reviewer(args, box),
        commit_requested=not args.no_commit,
    )
    if args.json:
        print(json.dumps(augustus.dispatch_json(result), indent=2))
    else:
        print("\n".join(augustus.render_dispatch(result)))
    return result.exit_code


def _executor_routes(
    table: dict[str, tuple[str, ...]], task: augustus.Task, config: executor.ExecutorConfig
) -> dict[str, tuple[str, ...]]:
    """A declared executor means the task is work: a checks-only route would close a tree
    nobody touched. The write gate is not optional for a kind whose route forgot it."""
    if not config.declared or task.change is not None or task.kind not in table:
        return table
    if "run" in table[task.kind]:
        return table
    return {**table, task.kind: ("run", *table[task.kind])}


def _resume(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    if args.max_cents is None and args.max_seconds is None:
        budget: dict[str, int] | None = None
    else:
        budget = {}
        if args.max_cents is not None:
            budget["max_cents"] = max(1, math.ceil(args.max_cents))
        if args.max_seconds is not None:
            budget["max_seconds"] = args.max_seconds
    routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
    trajan = contour.trajan_root(args.trajan)
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    if args.executor:
        config = executor.ExecutorConfig(
            path=config.path,
            command=tuple(shlex.split(args.executor)),
            model=config.model,
            max_seconds=config.max_seconds,
            max_cents=config.max_cents,
            max_steps=config.max_steps,
        )
    result = augustus.resume(
        box,
        args.task,
        routes=augustus.read_routes(Path(routes) if routes else None),
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, args.python),
        executor_config=config,
        budget=budget,
        retry=args.retry,
        review=_reviewer(args, box),
        commit_requested=not args.no_commit,
    )
    if args.json:
        print(json.dumps(augustus.dispatch_json(result), indent=2))
    else:
        print("\n".join(augustus.render_dispatch(result)))
    return result.exit_code


def _amend(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    budget: dict[str, int] = {}
    for item in args.budget:
        key, separator, raw = item.partition("=")
        if not separator or not key.strip():
            raise boxes.HarnessError(
                "amend: --budget takes KEY=VALUE, e.g. --budget max_cents=8"
            )
        try:
            value = int(raw)
        except ValueError as error:
            raise boxes.HarnessError(
                f"amend: --budget {item!r}: the value must be a whole number"
            ) from error
        budget[key.strip()] = value
    result = augustus.amend(
        box,
        args.task,
        why=args.why,
        add_paths=tuple(args.add_path),
        budget=budget,
        max_steps=args.max_steps,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "parent": result.parent.id,
                    "child": result.child.id,
                    "why": result.child.why,
                    "changes": list(result.changes),
                    "task": str(result.child.path),
                    "record": str(result.record),
                },
                indent=2,
            )
        )
        return 0
    print(f"parent     {result.parent.id}")
    print(f"successor  {result.child.id}  ({result.child.path})")
    print(f"why        {result.child.why}")
    print("changes")
    for change in result.changes:
        print(f"- {augustus.change_line(change)}")
    return 0


def _park(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    record = augustus.park(box, args.task, why=args.why)
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(f"parked     {record['task']}")
        print(f"why        {record['why']}")
        print("state      it does not run; `pantheon amend` writes a successor that names it")
    return 0


def _resolve_task(box: boxes.Box, value: str) -> augustus.Task:
    """A task file, or a frozen task id: ``explain`` may read either without writing."""
    path = Path(value).expanduser()
    if path.is_file():
        return augustus.read_task(path)
    frozen = box.root / "tabularium" / value / augustus.SNAPSHOT_FILE
    if frozen.is_file():
        return augustus.read_task(frozen)
    raise boxes.HarnessError(
        f"no task file at {value} and no frozen task {value!r}: "
        "`pantheon status` lists the ids the box knows"
    )


def _explain(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    task = _resolve_task(box, args.task)
    routes = args.routes or os.environ.get(augustus.ROUTES_ENV)
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    if args.executor:
        config = executor.ExecutorConfig(
            path=config.path,
            command=tuple(shlex.split(args.executor)),
            model=config.model,
            max_seconds=config.max_seconds,
            max_cents=config.max_cents,
            max_steps=config.max_steps,
        )
    result = augustus.explain(
        box,
        task,
        routes=_executor_routes(
            augustus.read_routes(Path(routes) if routes else None), task, config
        ),
        config=config,
        review=_reviewer(args, box),
    )
    if args.json:
        print(json.dumps(augustus.explanation_json(result), indent=2))
    else:
        print("\n".join(augustus.render_explanation(result)))
    return 1 if result.refusal is not None else 0


def _preflight(args: argparse.Namespace, root: Path) -> int:
    """Check the task's own reproduction before any model is paid for."""
    box = boxes.find_box(root, args.name, Path.cwd())
    task = _resolve_task(box, args.task)
    trajan = contour.trajan_root(args.trajan)
    result = preflight.check(
        box.project,
        task,
        fix=args.fix,
        root=trajan,
        python=contour.trajan_python(trajan, args.python),
    )
    if args.json:
        print(json.dumps(preflight.payload(result), indent=2))
    else:
        print("\n".join(preflight.render(result)))
    return 0 if result.ok else 1


def _intake(args: argparse.Namespace, root: Path) -> int:
    """Check the contract before any work: ready, or the named facts it is missing."""
    box = boxes.find_box(root, args.name, Path.cwd())
    task = _resolve_task(box, args.task)
    result = augustus.intake(task)
    if args.json:
        print(json.dumps(augustus.intake_payload(result), indent=2))
    else:
        print("\n".join(augustus.render_intake(result)))
    return 0 if result.ready else 1


def _sensitivity(args: argparse.Namespace, root: Path) -> int:
    """Reverse the change piece by piece: does the check notice any of it?

    Reads only — every piece is reversed in a throwaway copy, and the project is left as it was.
    """
    box = boxes.find_box(root, args.name, Path.cwd())
    task = _resolve_task(box, args.task)
    if task.change is None:
        raise boxes.HarnessError(
            f"{task.id} carries no change: this step reverses the change it is given, and a task "
            "without a patch has nothing to reverse"
        )
    trajan = contour.trajan_root(args.trajan)
    report = sensitivity.check(
        box.project,
        task=task.id,
        patch=task.change,
        repro=task.repro,
        root=trajan,
        python=contour.trajan_python(trajan, args.python),
        limit=max(1, args.max_units),
        base=args.base,
    )
    if args.json:
        print(json.dumps(sensitivity.payload(report), indent=2))
    else:
        print("\n".join(sensitivity.render(report)))
    return 0


def _mutate(args: argparse.Namespace, root: Path) -> int:
    """Apply the named breaks to copies of the tree and ask the check whether it notices.

    Reads only: every break lives in a throwaway copy, and the project is left as it was. The
    answer is an audit, not a verdict (ADR-048): a blind break is written down with its address
    and does not return the code or change any verdict.
    """
    box = boxes.find_box(root, args.name, Path.cwd())
    audit = mutation.read_audit(Path(args.file))
    trajan = contour.trajan_root(args.trajan)
    report = mutation.run(
        box.project,
        audit,
        root=trajan,
        python=contour.trajan_python(trajan, args.python),
        limit=max(1, args.max_breaks),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    saved = mutation.save(report, box.root / "tabularium" / "mutatio", stamp)
    if args.json:
        print(json.dumps({**mutation.payload(report), "record": str(saved)}, indent=2))
    else:
        print("\n".join([*mutation.render(report), f"record     {saved}"]))
    return 0


def _accept(args: argparse.Namespace, root: Path) -> int:
    """The third acceptance mark: the owner says the requirement is the one he wanted.

    The box cannot compute this one — the check passing and the reason being confirmed are the
    first two marks. The mark is written beside the archived change, with the diff it is about,
    and is read back by `history`; it does not touch the verdict, which was computed by code.
    """
    box = boxes.find_box(root, args.name, Path.cwd())
    decision = archivum.REJECTED if args.reject else archivum.ACCEPTED
    made = augustus.accept(box, args.address, decision=decision, by=args.by, note=args.note)
    if args.json:
        print(json.dumps(made.json(), indent=2, ensure_ascii=False))
        return 0
    lines = [
        f"change     {made.address}",
        f"decision   {made.decision} by {made.by} at {made.at}",
    ]
    if made.note:
        lines.append(f"note       {made.note}")
    lines.append(f"commit     {made.commit}")
    lines.append(f"diff       {made.diff_hash or 'not recorded'}")
    print("\n".join(lines))
    return 0


def _history(args: argparse.Namespace, root: Path) -> int:
    """The archive's first reader: what touched these paths, and why.

    A query without paths lists the newest changes; a query with paths answers the refactoring
    and the reproducer question. The diff is not in the answer — it is read on demand, because a
    map that carried every change would be the whole history in every prompt.
    """
    box = boxes.find_box(root, args.name, Path.cwd())
    if args.diff:
        entry = archivum.find(box.root, args.diff)
        if entry is None:
            raise boxes.HarnessError(f"no archived change at {args.diff}: the address is wrong")
        path = archivum.diff(box.root, args.diff)
        if path is None:
            raise boxes.HarnessError(f"{args.diff} has no stored change")
        print(path.read_text(encoding="utf-8"))
        return 0
    patterns = tuple(args.path)
    found = archivum.for_paths(box.root, patterns)
    shown = found if args.limit <= 0 else found[: args.limit]
    if args.json:
        print(
            json.dumps(
                {
                    "paths": list(patterns),
                    "changes": [
                        archivum.payload(item, archivum.decisions(box.root, item.address))
                        for item in shown
                    ],
                },
                indent=2,
            )
        )
        return 0
    if not shown:
        if patterns:
            print(f"nothing archived touches {', '.join(patterns)}")
        else:
            print("nothing archived yet: the archive is written when a change is closed")
        return 0
    for index, entry in enumerate(shown):
        if index:
            print()
        marks = archivum.decisions(box.root, entry.address)
        print("\n".join(archivum.render(entry, marks)))
    return 0


def _reviewer(args: argparse.Namespace, box: boxes.Box) -> contour.Reviewer | None:
    """The box's agent as the reader, when the box declared one or the run asked for one.

    The file says what usually happens; the flags say what happens now, and a flag always
    wins: ``--review`` reads even a box that declared nothing, ``--no-review`` refuses for
    one run without editing the file.
    """
    if getattr(args, "no_review", False):
        return None
    config = reviewer.read_config(box.root / reviewer.PROFILE_FILE)
    if not getattr(args, "review", False) and not config.enabled:
        return None
    return contour.box_reviewer(
        box.root,
        model=args.review_model or config.model,
        seconds=args.review_seconds,
        max_cents=config.max_cents,
        # `--review` means "read this one": a declared path list narrows the file, not the flag.
        when_paths=() if getattr(args, "review", False) else config.when_paths,
    )


def _list(args: argparse.Namespace, root: Path) -> int:
    found = boxes.list_boxes(root)
    if not found:
        print(f"no castra under {root}; `pantheon new` creates one")
        return 0
    for box in found:
        print(f"{box.name:<20} {box.project}  created {box.created}")
    return 0


def _up(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    result = container.up(box, args.image)
    print(f"box {box.name}: {result.line}")
    if not result.running:
        print("pantheon: the container did not come up; nothing is running", file=sys.stderr)
        return EXIT_CANNOT
    return 0


def _down(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    result = container.down(box)
    print(f"box {box.name}: {result.line}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
