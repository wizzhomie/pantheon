"""The buttons on the workscreen: the owner's mark, put through the gates the CLI uses.

A button is not a gate. Every action here calls the function the command calls —
``rails.approve``, ``spec.accept``, ``runner.step``, ``runner.run`` — so a refusal is the
harness's own sentence and the record it leaves is the same record the terminal would leave.
What the page adds is a place to say yes without a terminal, one lane so two clicks cannot start
two steps at once, and a sentence about what the click will cost before it is made.

Two of the actions write a record into the box (the plan, a spec package); two run work through
the whole road (a step, an auto campaign) and spend money. The page asks twice for all of them:
the first click only names the act.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import augustus, campaign, contour, rails, reviewer, runner, spec
from .. import box as boxes

ACTIONS = ("plan.approve", "spec.accept", "step.run", "campaign.advance")
"""Every act the page may ask for; a name outside this list is refused before anything runs."""

PARAMS = {
    "plan.approve": frozenset({"note"}),
    "spec.accept": frozenset({"package", "note"}),
    "step.run": frozenset({"campaign", "step", "retry"}),
    "campaign.advance": frozenset({"campaign"}),
}
"""The keys each action reads: an unknown parameter is a refusal, not something ignored."""


@dataclass(frozen=True, slots=True)
class Button:
    """One act the page may offer now, and the facts that say so."""

    id: str
    label: str
    detail: str
    params: dict[str, str]
    money: bool
    note: str = ""
    enabled: bool = True
    """Whether a click could go through now: a button that cannot is shown, not hidden, with the
    detail saying what it waits for."""

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "params": dict(self.params),
            "money": self.money,
            "note": self.note,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class Result:
    """What the gate said when the button was pressed: the sentence, and its own words."""

    action: str
    sentence: str
    lines: tuple[str, ...]
    cents: float | None = None
    record: str | None = None
    ok: bool = True
    """Whether the act reached its own goal: a refusal is a result the page shows in red, because
    the gate said no and the owner has to see that it did."""

    def json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "sentence": self.sentence,
            "lines": list(self.lines),
            "cents": self.cents,
            "record": self.record,
            "ok": self.ok,
        }


def buttons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The acts this box offers now, from the same object the page renders."""
    found: list[Button] = []
    found.extend(_plan_buttons(payload))
    found.extend(_spec_buttons(payload))
    found.extend(_campaign_buttons(payload))
    return [item.json() for item in found]


def run(box: boxes.Box, action: str, params: dict[str, str]) -> Result:
    """Do one act through the gate that owns it, or refuse in that gate's own words."""
    if action not in ACTIONS:
        raise boxes.HarnessError(
            f"no action {action!r}: the page offers " + ", ".join(ACTIONS)
        )
    unknown = sorted(set(params) - PARAMS[action])
    if unknown:
        raise boxes.HarnessError(
            f"{action} does not take {', '.join(unknown)}: it reads "
            + (", ".join(sorted(PARAMS[action])) or "nothing")
        )
    if action == "plan.approve":
        return _approve_plan(box, params)
    if action == "spec.accept":
        return _accept_spec(box, params)
    if action == "step.run":
        return _run_step(box, params)
    return _advance(box, params)


def _plan_buttons(payload: dict[str, Any]) -> list[Button]:
    declared = payload.get("rails") or {}
    standing = declared.get("standing") or {}
    state = standing.get("state")
    proposal = standing.get("proposal") or {}
    approval = standing.get("approved") or {}
    offered = bool(proposal) and proposal.get("digest") == standing.get("digest")
    if state in {None, "none", "approved"}:
        return []
    if state == "changed":
        detail = (
            "changed since the approval of "
            f"{_short(approval.get('digest'))} — the plan on disk is not the one accepted"
        )
    elif proposal:
        detail = f"proposed {proposal.get('at')} by {proposal.get('by')}, never approved"
    else:
        detail = "nothing proposed yet: the architect has to offer it first"
    return [
        Button(
            id="plan.approve",
            label="Approve plan",
            detail=detail,
            params={},
            money=False,
            note="why this plan is the basis" if offered else "",
            enabled=offered,
        )
    ]


def _spec_buttons(payload: dict[str, Any]) -> list[Button]:
    found: list[Button] = []
    for package in payload.get("packages") or []:
        if package.get("error") or package.get("state") == spec.ACCEPTED:
            continue
        tasks = package.get("tasks") or []
        detail = f"{len(tasks)} task(s) · {package.get('state')}"
        found.append(
            Button(
                id="spec.accept",
                label=f"Accept {package.get('id')}",
                detail=detail,
                params={"package": str(package.get("id"))},
                money=False,
                note="the owner's words on this package",
            )
        )
    return found


def _campaign_buttons(payload: dict[str, Any]) -> list[Button]:
    found: list[Button] = []
    for plan in payload.get("campaigns") or []:
        if plan.get("error") or plan.get("mode") != "auto":
            continue
        verdict = plan.get("verdict") or {}
        if verdict.get("status") == "complete" or plan.get("problems"):
            continue
        found.append(
            Button(
                id="campaign.advance",
                label=f"Run {plan.get('campaign')}",
                detail=(
                    f"auto · {verdict.get('closed', 0)} of {verdict.get('total', 0)} steps closed"
                    f" · {_cents(plan.get('spend'))}"
                ),
                params={"campaign": str(plan.get("campaign"))},
                money=True,
            )
        )
    return found


def _approve_plan(box: boxes.Box, params: dict[str, str]) -> Result:
    approval = rails.approve(box, by="owner", note=params.get("note", ""))
    standing = rails.standing(box)
    sentence = (
        f"План принят: {_short(approval.digest)}, запись {_relative(box, approval.path)}."
    )
    lines = [
        f"approved {approval.at} by {approval.by}",
        f"digest   {approval.digest}",
        f"note     {approval.note or '—'}",
        f"now      {standing.line()}",
    ]
    if approval.proposal is not None:
        lines.append(f"proposal {approval.proposal.path}")
    return Result(
        action="plan.approve", sentence=sentence, lines=tuple(lines), record=str(approval.path)
    )


def _accept_spec(box: boxes.Box, params: dict[str, str]) -> Result:
    package = _required(params, "package")
    approval = spec.accept(box, package, by="owner", note=params.get("note", ""))
    plan = "" if approval.plan is None else f", план {_relative(box, approval.plan)}"
    sentence = (
        f"Спека {package} принята: {_short(approval.digest)}, "
        f"задач {len(approval.tasks)}{plan}."
    )
    lines = [
        f"accepted {approval.at} by {approval.by}",
        f"digest   {approval.digest}",
        f"note     {approval.note or '—'}",
        f"tasks    {', '.join(approval.tasks) or '—'}",
        f"record   {_relative(box, approval.path)}",
    ]
    if approval.plan is not None:
        lines.append(f"plan     {approval.plan}")
    return Result(
        action="spec.accept", sentence=sentence, lines=tuple(lines), record=str(approval.path)
    )


def _run_step(box: boxes.Box, params: dict[str, str]) -> Result:
    plan = _plan(box, _required(params, "campaign"))
    step = _required(params, "step")
    retry = params.get("retry", "") in {"1", "true", "yes"}
    trajan = contour.trajan_root(None)
    attempt = runner.step(
        box,
        plan,
        step,
        table=augustus.read_routes(None),
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, None),
        review=_reader(box),
        retry=retry,
    )
    state = attempt.state
    verdict = state.verdict or "no verdict"
    closed = "закрыт" if attempt.closed else "не закрыт"
    spent = "" if state.cents is None else f", {state.cents:.2f}¢"
    sentence = f"Шаг {step}: {closed}, вердикт {verdict}{spent}."
    lines = [
        f"{'closed' if attempt.closed else 'open'}   {step} ({state.state}, {verdict})",
        f"attempt  {state.attempts} of {plan.attempts}",
        f"record   {state.record}",
    ]
    lines.extend(attempt.dispatch.notes[-3:])
    return Result(
        action="step.run",
        sentence=sentence,
        lines=tuple(lines),
        cents=state.cents,
        record=state.record,
        ok=attempt.closed,
    )


def _advance(box: boxes.Box, params: dict[str, str]) -> Result:
    plan = _plan(box, _required(params, "campaign"))
    trajan = contour.trajan_root(None)
    result = runner.run(
        box,
        plan,
        table=augustus.read_routes(None),
        trajan_root=trajan,
        trajan_python=contour.trajan_python(trajan, None),
        review=_reader(box),
    )
    verdict = result.report.verdict
    sentence = (
        f"Кампания {plan.id}: закрыто {verdict.closed} из {verdict.total}"
        + (f", {result.report.state.cents:.2f}¢" if result.report.state is not None else "")
        + (f"; остановка: {result.stop}" if result.stop else "; все шаги закрыты")
        + "."
    )
    lines = [
        f"run      {plan.id}, {len(result.attempts)} attempt(s) by this run",
        f"verdict  {verdict.status} — {verdict.closed} of {verdict.total} steps closed",
    ]
    if result.stop:
        lines.append(f"stop     {result.stop}")
    if result.path is not None:
        lines.append(f"record   {result.path}")
    return Result(
        action="campaign.advance",
        sentence=sentence,
        lines=tuple(lines),
        cents=None if result.report.state is None else result.report.state.cents,
        record=None if result.path is None else str(result.path),
        ok=not result.stop,
    )


def _reader(box: boxes.Box) -> contour.Reviewer | None:
    """The box's own reader, exactly as a step without flags reads it: declared, or nobody."""
    config = reviewer.read_config(box.root / reviewer.PROFILE_FILE)
    if not config.enabled:
        return None
    return contour.box_reviewer(
        box.root,
        model=config.model,
        max_cents=config.max_cents,
        when_paths=config.when_paths,
    )


def _plan(box: boxes.Box, identifier: str) -> campaign.Campaign:
    found = box.project / campaign.CAMPAIGN_DIR / f"{identifier}.toml"
    if not found.is_file():
        known = ", ".join(path.stem for path in campaign.campaign_files(box.project)) or "none"
        raise boxes.HarnessError(
            f"no campaign {identifier!r}: {found} does not exist; the project declares {known}"
        )
    return campaign.read_campaign(found)


def _required(params: dict[str, str], key: str) -> str:
    value = params.get(key, "").strip()
    if not value:
        raise boxes.HarnessError(f"this button needs {key!r}: it names the thing to act on")
    return value


def _relative(box: boxes.Box, path: Path) -> str:
    try:
        return str(path.relative_to(box.root))
    except ValueError:
        return str(path)


def _short(digest: object) -> str:
    return str(digest or "").replace("sha256:", "")[:12] or "—"


def _cents(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "no spend recorded"
    return f"{float(value):.2f}¢ spent"
