"""The box as one JSON object: exactly what the workscreen renders, read from disk on every call.

Nothing here is cached or written. The page must show what the gates would read, not a copy
that was true when the server started, so every question re-reads the box, the project's
``.pantheon/`` files and the records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import augustus, campaign, container, fluxus, frame, rails, records, spec
from .. import box as boxes
from . import actions


def listing(root: Path) -> list[dict[str, Any]]:
    """Every box on disk, in name order: the page's project switcher."""
    return [
        {
            "name": item.name,
            "project": str(item.project),
            "created": item.created,
            "image": item.image,
        }
        for item in boxes.list_boxes(root)
    ]


def overview(root: Path, name: str) -> dict[str, Any]:
    """The whole page in one answer: what exists, what runs, what waits, what was recorded."""
    box = boxes.find_box(root, name, Path.cwd())
    payload = boxes.status_json(boxes.status(box))
    payload["container"] = _container(box)
    payload["frame"] = frame.standing(box).json()
    payload["rails"] = _rails(box)
    payload["packages"] = _packages(box)
    payload["campaigns"] = _campaigns(box)
    payload["tasks"] = _tasks(box)
    digest, stored = records.state(box)
    payload["records"] = {"digest": digest.json(), "stored": stored}
    payload["stream"] = fluxus.payload(box)
    payload["spend"] = _spend(payload["campaigns"])
    # The buttons are read last, from the same object the page renders: an act is offered only
    # when the facts in this answer say the gate would take it.
    payload["actions"] = actions.buttons(payload)
    return payload


def _container(box: boxes.Box) -> dict[str, Any]:
    facts = container.state(box)
    return {
        "running": facts.running,
        "name": facts.name,
        "image": facts.image,
        "detail": facts.detail,
    }


def _tasks(box: boxes.Box) -> list[dict[str, Any]]:
    return [
        {
            "id": task.id,
            "state": task.state,
            "at": task.at,
            "verify": task.verify,
            "seconds": task.seconds,
            "record": str(task.record),
            "unknowns": list(task.unknowns),
            "parent": task.parent,
            "why": task.why,
            "replaced_by": task.replaced_by,
            "parked": task.parked,
        }
        for task in augustus.recent_tasks(box.root)
    ]


def _campaigns(box: boxes.Box) -> list[dict[str, Any]]:
    table = augustus.read_routes(None)
    found: list[dict[str, Any]] = []
    for path in campaign.campaign_files(box.project):
        try:
            plan = campaign.read_campaign(path)
            report = campaign.report(box, plan, routes=table)
        except boxes.HarnessError as error:
            found.append({"id": path.stem, "path": str(path), "error": str(error)})
            continue
        payload = campaign.report_json(report)
        rows = payload.get("steps")
        if isinstance(rows, list):
            for index, step in enumerate(plan.steps):
                if index < len(rows) and isinstance(rows[index], dict):
                    rows[index]["run"] = _runnable(plan, report, step)
        found.append(payload)
    return found


def _runnable(
    plan: campaign.Campaign, report: campaign.Report, step: campaign.Step
) -> dict[str, Any]:
    """Whether the page may offer a run button for this step, and the gate's reason when not.

    The rules are the runner's own, read from the same facts: the plan's problems, the budget,
    the dependencies, the record of a previous attempt, and the retry decision it produced. A
    step the page says "ready" about still goes through ``runner.step`` and can be refused
    there; saying "ready" is not a permission, it is what the record currently implies.
    """
    if report.problems:
        return {"allowed": False, "retry": False, "why": report.problems[0]}
    verdict = report.verdict
    if verdict.status == "over budget":
        return {
            "allowed": False,
            "retry": False,
            "why": "; ".join(verdict.reasons) or "the campaign spent its budget",
        }
    recorded = None if report.state is None else report.state.steps.get(step.id)
    if recorded is not None and recorded.state == "closed":
        return {"allowed": False, "retry": False, "why": "closed: nothing left to run"}
    waiting = campaign.blockers(plan, step, report.state)
    if waiting:
        return {"allowed": False, "retry": False, "why": waiting[0]}
    if report.state is not None and report.state.plan and report.state.plan != plan.digest:
        return {
            "allowed": False,
            "retry": False,
            "why": (
                "the plan changed since the last step, and the record still says the old one: "
                "another attempt needs a reason, and a reason is the CLI's to give"
            ),
        }
    if recorded is not None:
        decision = report.retries.get(step.id)
        if decision is None:
            return {"allowed": False, "retry": True, "why": "the record says no attempt is next"}
        return {"allowed": decision.allowed, "retry": True, "why": decision.reason}
    return {"allowed": True, "retry": False, "why": "ready: nothing it waits for is missing"}


def _packages(box: boxes.Box) -> list[dict[str, Any]]:
    """Every spec package imported into the box, with the acceptance record it stands against."""
    directory = box.root / spec.PACKAGES
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir():
            continue
        try:
            current = spec.standing(box, path.name)
        except boxes.HarnessError as error:
            found.append({"id": path.name, "error": str(error)})
            continue
        payload: dict[str, Any] = dict(current.json())
        payload["refusal"] = current.refusal
        found.append(payload)
    return found


def _rails(box: boxes.Box) -> dict[str, Any]:
    answer: dict[str, Any] = {"standing": rails.standing(box).json()}
    try:
        declared = rails.read(box.project)
    except boxes.HarnessError as error:
        answer["error"] = str(error)
        return answer
    if declared is None:
        answer["declared"] = False
        return answer
    answer["declared"] = True
    answer["digest"] = declared.digest
    answer["brief"] = {
        "why": declared.brief.why,
        "for": declared.brief.for_whom,
        "success": declared.brief.success,
    }
    answer["goals"] = [goal.text for goal in declared.goals]
    answer["milestones"] = [
        {
            "id": item.id,
            "title": item.title,
            "closes_when": item.closes_when,
            "metric": item.metric,
            "state": item.state,
        }
        for item in declared.milestones
    ]
    answer["metrics"] = [
        {"name": item.name, "direction": item.direction, "threshold": item.threshold}
        for item in declared.metrics
    ]
    answer["principles"] = [
        {"text": item.text, "why": item.why} for item in declared.principles
    ]
    answer["missing"] = list(declared.missing)
    return answer


def _spend(campaigns: object) -> dict[str, Any]:
    total = 0.0
    counted = 0
    if isinstance(campaigns, list):
        for item in campaigns:
            if not isinstance(item, dict):
                continue
            spend = item.get("spend")
            if isinstance(spend, (int, float)) and not isinstance(spend, bool):
                total += float(spend)
                counted += 1
    return {"live_cents": round(total, 4), "campaigns": counted}
