"""The box as one JSON object: exactly what the workscreen renders, read from disk on every call.

Nothing here is cached or written. The page must show what the gates would read, not a copy
that was true when the server started, so every question re-reads the box, the project's
``.pantheon/`` files and the records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import augustus, campaign, container, fluxus, frame, rails, records
from .. import box as boxes


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
    payload["campaigns"] = _campaigns(box)
    payload["tasks"] = _tasks(box)
    digest, stored = records.state(box)
    payload["records"] = {"digest": digest.json(), "stored": stored}
    payload["stream"] = fluxus.payload(box)
    payload["spend"] = _spend(payload["campaigns"])
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
            found.append(campaign.report_json(campaign.report(box, plan, routes=table)))
        except boxes.HarnessError as error:
            found.append({"id": path.stem, "path": str(path), "error": str(error)})
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
