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

from . import augustus, aurelius, caesar, container, contour, executor
from . import box as boxes

DEFAULT_BOXES = Path("~/pantheon/boxes")
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
        description="A box per project: create it, describe it, and (one day) start its agent.",
    )
    parser.add_argument(
        "--boxes",
        default=None,
        help="where boxes live (default: $PANTHEON_BOXES, else ~/pantheon/boxes)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    new = commands.add_parser("new", help="create a box for a project")
    new.add_argument("project", nargs="?", default=".", help="path to the project (default: .)")
    new.add_argument("--name", default=None, help="box name (default: from the directory name)")
    new.add_argument("--image", default=None, help="pinned agent image, tag or digest")
    new.set_defaults(handler=_new)

    status = commands.add_parser("status", help="describe a box and its project, reading only")
    status.add_argument("name", nargs="?", default=None, help="box name (default: the only one)")
    status.add_argument("--json", action="store_true", help="the same facts as one JSON object")
    status.set_defaults(handler=_status)

    verify = commands.add_parser("verify", help="run trajan on the project's change")
    verify.add_argument("name", nargs="?", default=None, help="box name (default: the only one)")
    change = verify.add_mutually_exclusive_group()
    change.add_argument(
        "--since",
        default=None,
        help="review `git diff REV`; needs --reviewer, since a change needs a reader",
    )
    change.add_argument(
        "--diff", default=None, help="a unified diff to review; needs --reviewer"
    )
    verify.add_argument("--out", default=None, help="run directory (default: box artifacts/)")
    verify.add_argument("--trajan", default=None, help="the trajan checkout to call")
    verify.add_argument("--python", default=None, help="the interpreter that runs trajan")
    verify.add_argument("--reviewer", default=None, help="MODULE:FACTORY, passes --reviewer")
    verify.add_argument("--json", action="store_true", help="the answer as one JSON object")
    verify.set_defaults(handler=_verify)

    run = commands.add_parser("run", help="gate a change by the task's contract, then apply it")
    run.add_argument("name", nargs="?", default=None, help="box name (default: the only one)")
    run.add_argument(
        "--contract", required=True, help="tasks/<id>.contract.toml: what this attempt may touch"
    )
    run.add_argument("--patch", required=True, help="the unified diff the attempt proposes")
    run.add_argument("--json", action="store_true", help="the attempt as one JSON object")
    run.set_defaults(handler=_run)

    arch = commands.add_parser(
        "arch", help="Aurelius: check the project against its declared architecture"
    )
    arch.add_argument("name", nargs="?", default=None, help="box name (default: the only one)")
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

    dispatch = commands.add_parser(
        "dispatch", help="Augustus: run a task through the contours its scope routes to"
    )
    dispatch.add_argument("name", nargs="?", default=None, help="box name (default: the only one)")
    dispatch.add_argument("--task", required=True, help="tasks/<id>.toml: goal, scope, kind")
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
        help="override the box's executor command for this run (profile/executor.toml)",
    )
    dispatch.add_argument("--json", action="store_true", help="the answer as one JSON object")
    dispatch.set_defaults(handler=_dispatch)

    resume = commands.add_parser(
        "resume",
        help="Augustus: continue a task that stopped before its verdict, from its artifacts",
    )
    resume.add_argument("name", nargs="?", default=None, help="box name (default: the only one)")
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
        help="override the box's executor command for this run (profile/executor.toml)",
    )
    resume.add_argument("--json", action="store_true", help="the answer as one JSON object")
    resume.set_defaults(handler=_resume)

    listing = commands.add_parser("list", help="every box under the boxes directory")
    listing.set_defaults(handler=_list)

    up = commands.add_parser("up", help="start the box's container (mount only, no agent yet)")
    up.add_argument("name", nargs="?", default=None, help="box name")
    up.add_argument("--image", default=None, help="pin this image in box.toml, then start")
    up.set_defaults(handler=_up)

    down = commands.add_parser("down", help="stop the box's container, keeping the box's files")
    down.add_argument("name", nargs="?", default=None, help="box name")
    down.set_defaults(handler=_down)
    return parser


def _execute(args: argparse.Namespace) -> int:
    handler: object = getattr(args, "handler", None)
    if not callable(handler):
        raise boxes.HarnessError("no command given")
    result: int = handler(args, _boxes_root(args))
    return result


def _boxes_root(args: argparse.Namespace) -> Path:
    chosen = args.boxes or os.environ.get("PANTHEON_BOXES") or str(DEFAULT_BOXES)
    return Path(chosen).expanduser()


def _new(args: argparse.Namespace, root: Path) -> int:
    box = boxes.new_box(root, Path(args.project), args.name, args.image)
    example = box.root / executor.EXAMPLE_FILE
    example.parent.mkdir(parents=True, exist_ok=True)
    if not example.exists():
        example.write_text(executor.EXAMPLE, encoding="utf-8")
    print(f"box {box.name} created at {box.root}")
    print(f"  project  {box.project}")
    print(f"  image    {box.image or 'not chosen yet (stage 1.5 pins the tag)'}")
    print(f"  executor {executor.describe(executor.read_config(box.root / executor.PROFILE_FILE))}")
    print(f"  example  {example}")
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
        payload["tasks"] = [
            {
                "id": task.id,
                "state": task.state,
                "at": task.at,
                "verify": task.verify,
                "record": str(task.record),
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
    print("\n".join(lines))
    if tasks:
        for task in tasks:
            verify = f"verify {task.verify}" if task.verify else "not verified"
            print(f"task       {task.id}  {task.state}  {task.at}  {verify}")
    else:
        print("tasks      none dispatched yet")
    return 0


def _verify(args: argparse.Namespace, root: Path) -> int:
    box = boxes.find_box(root, args.name, Path.cwd())
    policy = box.project / ".trajan" / "policy.toml"
    if not policy.is_file():
        raise boxes.HarnessError(
            f"project {box.project} has no .trajan/policy.toml: there is nothing to verify against"
        )
    if (args.since or args.diff) and not args.reviewer:
        raise boxes.HarnessError(
            "a change needs a reader: pass --reviewer MODULE:FACTORY to have the diff reviewed, "
            "or drop --since/--diff to run the checks only"
        )
    trajan = contour.trajan_root(args.trajan)
    out = Path(args.out) if args.out else box.root / "artifacts" / _run_name(box.root)
    result = contour.run_verify(
        box.project,
        root=trajan,
        python=contour.trajan_python(trajan, args.python),
        out=out,
        since=args.since,
        diff=Path(args.diff).resolve() if args.diff else None,
        reviewer=args.reviewer,
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
        routes=augustus.read_routes(Path(routes) if routes else None),
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, args.python),
        tool=args.tool,
        executor_config=config,
    )
    if args.json:
        print(json.dumps(augustus.dispatch_json(result), indent=2))
    else:
        print("\n".join(augustus.render_dispatch(result)))
    return result.exit_code


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
    )
    if args.json:
        print(json.dumps(augustus.dispatch_json(result), indent=2))
    else:
        print("\n".join(augustus.render_dispatch(result)))
    return result.exit_code


def _run_name(box_root: Path) -> str:
    """The run directory name: it carries the moment, and never overwrites an earlier run."""
    base = "verify-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = base
    number = 2
    while (box_root / "artifacts" / candidate).exists():
        candidate = f"{base}-{number}"
        number += 1
    return candidate


def _list(args: argparse.Namespace, root: Path) -> int:
    found = boxes.list_boxes(root)
    if not found:
        print(f"no boxes under {root}; `pantheon new` creates one")
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
