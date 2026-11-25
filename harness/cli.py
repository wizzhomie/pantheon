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
    augustus,
    aurelius,
    caesar,
    campaign,
    container,
    contour,
    executor,
    frame,
    records,
    reviewer,
    runner,
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
    print(f"castrum {box.name} created at {box.root}")
    print(f"  project  {box.project}")
    print(f"  image    {box.image or 'not pinned yet (`pantheon up` pins it)'}")
    print(f"  executor {executor.describe(executor.read_config(box.root / executor.PROFILE_FILE))}")
    print(f"  example  {example}")
    print(f"  reader   {reviewer.describe(reviewer.read_config(box.root / reviewer.PROFILE_FILE))}")
    print(f"next: pantheon status {box.name}")
    return 0


def _status(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    report = boxes.status(box)
    tasks = augustus.recent_tasks(box.root)
    facts = container.state(box)
    agent_status = container.agent_status(box) if facts.running else None
    agent = executor.read_config(box.root / executor.PROFILE_FILE)
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
        payload["executor"] = {
            "declared": agent.declared,
            "command": None if agent.command is None else list(agent.command),
            "model": agent.model,
            "max_seconds": agent.max_seconds,
            "max_cents": agent.max_cents,
        }
        payload["frame"] = frame.standing(box).json()
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
    lines.insert(-1, f"frame      {frame.standing(box).line()}")
    lines.insert(-1, f"records    {_records_line(box)}")
    print("\n".join(lines))
    if tasks:
        for task in tasks:
            verify = f"verify {task.verify}" if task.verify else "not verified"
            read = _reading_brief(task.record)
            took = f"  in {task.seconds:.1f}s" if task.seconds is not None else ""
            print(f"task       {task.id}  {task.state}  {task.at}  {verify}{took}{read}")
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
        )
    result = augustus.dispatch(
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
