"""The rails: the frame a project's work serves, and the record that says it was accepted.

A task either names a milestone or says, in the record, that it stands outside the plan. The
frame is a file in the project — brief, goals, milestones, metrics, principles — and an approval
is a box record carrying the digest of the exact bytes accepted, the same discipline as the
architecture model. A part that was not filled is written down as missing, not skipped: a frame
that says nothing about principles still says that, and the approval records it.

Approving is refused while the rail itself is missing: no brief, no goal, no milestone or no
metric means there is nothing for a task to serve, and an approval of nothing would look like a
basis.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes
from . import caesar

SCHEMA_VERSION = 1
RAILS_FILE = ".pantheon/rails.toml"
RECORD_TYPE = "rails.approval"
REVISION_TYPE = "rails.revision"
CLOSURE_TYPE = "rails.closure"
RECORDS = Path("tabularium") / "forma"
KINDS = ("add", "replace", "revisit")
HEAVY = frozenset({"replace", "revisit"})
"""A heavy revision rewrites the frame and waits for it to be accepted again."""
DIRECTIONS = frozenset({"up", "down"})
STATES = frozenset({"open", "closed"})


@dataclass(frozen=True, slots=True)
class Brief:
    """Why the project exists, for whom, and what counts as done — the three sentences."""

    why: str
    for_whom: str
    success: str


@dataclass(frozen=True, slots=True)
class Goal:
    """A result the owner wants for the user, not a task."""

    text: str


@dataclass(frozen=True, slots=True)
class Milestone:
    """A checkpoint: tasks that close it, the condition that says so, and the metric it moves."""

    id: str
    title: str
    closes_when: str
    metric: str
    state: str


@dataclass(frozen=True, slots=True)
class Metric:
    """A number with a direction and a threshold — the same format the R&D cycle uses."""

    name: str
    direction: str
    threshold: str


@dataclass(frozen=True, slots=True)
class Principle:
    """What the project does not do, and why — the heaviest part of the frame."""

    text: str
    why: str


@dataclass(frozen=True, slots=True)
class Delegation:
    """A question the owner left to the box, and the default that was taken."""

    question: str
    default: str


@dataclass(frozen=True, slots=True)
class Rails:
    """The frame as it is on disk right now, and what is said in it."""

    project: Path
    path: Path
    digest: str
    brief: Brief
    goals: tuple[Goal, ...]
    milestones: tuple[Milestone, ...]
    metrics: tuple[Metric, ...]
    principles: tuple[Principle, ...]
    missing: tuple[str, ...]

    def blocking(self) -> tuple[str, ...]:
        """What stops an approval: all the rail needs, leaving principles for later."""
        return tuple(item for item in self.missing if item != "principles")

    def milestone(self, name: str) -> Milestone | None:
        """The milestone a task names, or ``None`` when the frame does not know it."""
        for item in self.milestones:
            if item.id == name:
                return item
        return None


@dataclass(frozen=True, slots=True)
class Approval:
    """A record a person wrote: this digest was the frame they accepted, and what they delegated."""

    path: Path
    at: str
    digest: str
    by: str
    note: str
    delegations: tuple[Delegation, ...]


@dataclass(frozen=True, slots=True)
class Standing:
    """How the rails on disk stand against the newest approval: approved, changed, never, none."""

    state: str
    digest: str
    approval: Approval | None = None

    def gap(self) -> str | None:
        """What a verdict about this project does not cover, or ``None`` when it is covered."""
        if self.state == "none":
            return (
                "the project declares no rails: no milestone holds this change, so its direction "
                "was not checked against anything"
            )
        if self.state == "never":
            return (
                "the rails were never approved: the milestones are a draft nobody accepted "
                "(`pantheon rails approve` makes that record)"
            )
        if self.state == "changed" and self.approval is not None:
            return (
                "the rails changed after they were approved (approved "
                f"{_short(self.approval.digest)}, now {_short(self.digest)}): the milestone this "
                "change names is not one the owner accepted"
            )
        return None

    def line(self) -> str:
        """The standing as one line for a person."""
        if self.state == "approved" and self.approval is not None:
            return f"approved {self.approval.at} by {self.approval.by} ({_short(self.digest)})"
        if self.state == "changed" and self.approval is not None:
            return (
                f"changed since approval ({_short(self.approval.digest)} -> {_short(self.digest)})"
            )
        if self.state == "never":
            return "never approved"
        return "nothing declared"

    def json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "digest": self.digest,
            "approved": None
            if self.approval is None
            else {
                "at": self.approval.at,
                "by": self.approval.by,
                "digest": self.approval.digest,
                "note": self.approval.note,
                "record": str(self.approval.path),
                "delegations": [
                    {"question": item.question, "default": item.default}
                    for item in self.approval.delegations
                ],
            },
        }


@dataclass(frozen=True, slots=True)
class Revision:
    """A departure from the accepted frame: what kind, why, and what it costs either way."""

    path: Path
    at: str
    kind: str
    from_digest: str
    to_digest: str
    note: str
    breaks: str | None
    lose: str | None
    by: str
    before: tuple[str, ...]
    """Tasks whose record rested on an older frame: they were run before this departure."""

    def json(self) -> dict[str, object]:
        return {
            "at": self.at,
            "kind": self.kind,
            "from": self.from_digest,
            "to": self.to_digest,
            "note": self.note,
            "breaks": self.breaks,
            "lose": self.lose,
            "by": self.by,
            "before": list(self.before),
            "record": str(self.path),
        }


@dataclass(frozen=True, slots=True)
class Closure:
    """A milestone closed by a record, not by an edit of the frame.

    The frame's ``state`` stays as it was written; the record is what says the closing
    condition was met, on which digest of the frame, and why. An edit of the frame would
    move its digest and make every task look like it rested on a frame nobody accepted.
    """

    path: Path
    at: str
    milestone: str
    title: str
    closes_when: str
    digest: str
    by: str
    note: str

    def json(self) -> dict[str, object]:
        return {
            "milestone": self.milestone,
            "title": self.title,
            "closes_when": self.closes_when,
            "at": self.at,
            "digest": self.digest,
            "by": self.by,
            "note": self.note,
            "record": str(self.path),
        }


@dataclass(frozen=True, slots=True)
class Check:
    """How one task stands against the rails: the milestone it serves, or why it stands out."""

    standing: Standing
    milestone: Milestone | None = None
    outside: str | None = None
    refusal: str | None = None

    def line(self) -> str:
        """The answer as one line, for the acta and the record."""
        if self.refusal is not None:
            return f"refused — {self.refusal}"
        if self.milestone is not None:
            return (
                f"milestone {self.milestone.id} — {self.milestone.title} "
                f"({self.milestone.state})"
            )
        if self.outside is not None:
            return f"outside the plan — {self.outside}"
        gap = self.standing.gap()
        return f"no milestone — {gap}" if gap is not None else "no milestone"

    def gap(self) -> str | None:
        """What this task's verdict does not cover: the rails it rested on were not accepted."""
        if self.standing.state != "approved":
            return self.standing.gap()
        return None

    def json(self) -> dict[str, object]:
        return {
            "state": self.standing.state,
            "digest": self.standing.digest,
            "milestone": None if self.milestone is None else self.milestone.id,
            "title": None if self.milestone is None else self.milestone.title,
            "outside": self.outside,
            "refusal": self.refusal,
        }


def read(project: Path) -> Rails | None:
    """Read the rails; ``None`` when none are declared, an error when they cannot be read."""
    path = project / RAILS_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise boxes.HarnessError(f"cannot read the rails {path}: {error}") from error
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"the rails {path} do not parse: {error}") from error
    if not isinstance(data, dict):
        raise boxes.HarnessError(f"the rails {path} are not a table")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"the rails {path} say schema_version {version!r}; this harness reads {SCHEMA_VERSION}"
        )
    brief_table = _table(data.get("brief", {}), "brief")
    brief = Brief(
        why=_text(brief_table, "why", "brief"),
        for_whom=_text(brief_table, "for", "brief"),
        success=_text(brief_table, "success", "brief"),
    )
    goals = tuple(Goal(text=_text(item, "text", "goal")) for item in _tables(data, "goal"))
    metrics = tuple(
        Metric(
            name=_text(item, "name", "metric"),
            direction=_text(item, "direction", "metric"),
            threshold=_text(item, "threshold", "metric"),
        )
        for item in _tables(data, "metric")
    )
    _check_metrics(metrics, path)
    milestones = tuple(
        Milestone(
            id=_text(item, "id", "milestone"),
            title=_text(item, "title", "milestone"),
            closes_when=_text(item, "closes_when", "milestone"),
            metric=_text(item, "metric", "milestone"),
            state=_text(item, "state", "milestone") or "open",
        )
        for item in _tables(data, "milestone")
    )
    _check_milestones(milestones, metrics, path)
    principles = tuple(
        Principle(
            text=_text(item, "text", "principle"),
            why=_text(item, "why", "principle"),
        )
        for item in _tables(data, "principle")
    )
    missing = _missing(brief, goals, milestones, metrics, principles)
    return Rails(
        project=project,
        path=path,
        digest=_sha(raw),
        brief=brief,
        goals=goals,
        milestones=milestones,
        metrics=metrics,
        principles=principles,
        missing=missing,
    )


def approve(
    box: boxes.Box,
    *,
    by: str,
    note: str = "",
    delegations: tuple[Delegation, ...] = (),
    when: datetime | None = None,
) -> Approval:
    """Write the record that says this frame is the basis a person accepts; never edit old ones."""
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    current = read(box.project)
    if current is None:
        raise boxes.HarnessError(
            f"there are no rails to approve in {box.project}: {RAILS_FILE} does not exist, so an "
            "approval would name nothing"
        )
    blocking = current.blocking()
    if blocking:
        raise boxes.HarnessError(
            "the rails are not filled enough to be a basis: " + ", ".join(blocking)
        )
    for item in delegations:
        if not item.question.strip() or not item.default.strip():
            raise boxes.HarnessError(
                "a delegation needs a question and the default that was taken, not an empty one"
            )
    directory = box.root / RECORDS
    directory.mkdir(parents=True, exist_ok=True)
    path = _free_path(directory, stamp)
    record = {
        "schema_version": SCHEMA_VERSION,
        "type": RECORD_TYPE,
        "at": _stamp(stamp),
        "digest": current.digest,
        "file": RAILS_FILE,
        "missing": list(current.missing),
        "by": by,
        "note": note,
        "delegations": [
            {"question": item.question, "default": item.default} for item in delegations
        ],
        "base": caesar.head(box.project),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_event(box, by, note, path, stamp, kind="rails.approved")
    return Approval(
        path=path,
        at=_stamp(stamp),
        digest=current.digest,
        by=by,
        note=note,
        delegations=delegations,
    )


def revise(
    box: boxes.Box,
    kind: str,
    note: str,
    *,
    breaks: str | None = None,
    lose: str | None = None,
    by: str = "owner",
    when: datetime | None = None,
) -> Revision:
    """Record a departure from the accepted frame: what kind, why, and what it costs either way.

    The record answers the two questions a heavy revision has to answer — what breaks if we do,
    and what we lose if we do not — and names the tasks that rested on the older frame. It is
    not an approval: the new frame is accepted by its own record, and a heavy revision refuses
    tasks until that happens.
    """
    if kind not in KINDS:
        raise boxes.HarnessError(
            f"revision kind {kind!r} is not one of {', '.join(KINDS)}"
        )
    if not note.strip():
        raise boxes.HarnessError(
            "a revision names why it was made: an edit without a reason is a rewrite on the fly"
        )
    current = read(box.project)
    if current is None:
        raise boxes.HarnessError(
            f"there are no rails to revise in {box.project}: {RAILS_FILE} does not exist"
        )
    approval = latest(box)
    if approval is None:
        raise boxes.HarnessError(
            "there is nothing to revise: the frame was never approved, so no departure can be "
            "named; approve the frame first"
        )
    if current.digest == approval.digest:
        raise boxes.HarnessError(
            "the frame did not change since it was approved: a revision that changes nothing is "
            "not one; edit the frame first"
        )
    if kind in HEAVY:
        if not (breaks and breaks.strip()):
            raise boxes.HarnessError(
                f"a {kind} revision says what breaks if we do it: `--breaks` is empty"
            )
        if not (lose and lose.strip()):
            raise boxes.HarnessError(
                f"a {kind} revision says what we lose if we do not: `--lose` is empty"
            )
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / RECORDS
    directory.mkdir(parents=True, exist_ok=True)
    path = _free_path(directory, stamp, prefix="rails-revision")
    before = _older_tasks(box, current.digest)
    record = {
        "schema_version": SCHEMA_VERSION,
        "type": REVISION_TYPE,
        "at": _stamp(stamp),
        "kind": kind,
        "from": approval.digest,
        "to": current.digest,
        "note": note,
        "breaks": breaks,
        "lose": lose,
        "by": by,
        "before": list(before),
        "base": caesar.head(box.project),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_event(box, by, note, path, stamp, kind="rails.revised")
    return Revision(
        path=path,
        at=_stamp(stamp),
        kind=kind,
        from_digest=approval.digest,
        to_digest=current.digest,
        note=note,
        breaks=breaks,
        lose=lose,
        by=by,
        before=before,
    )


def latest(box: boxes.Box) -> Approval | None:
    """The newest approval record, or ``None``. An unreadable record is an error, not a skip."""
    directory = box.root / RECORDS
    if not directory.is_dir():
        return None
    records = sorted(
        directory.glob("rails-approval-*.json"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
    )
    if not records:
        return None
    path = records[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(
            f"the rails approval {path} does not parse: {error}"
        ) from error
    if not isinstance(data, dict) or data.get("type") != RECORD_TYPE:
        raise boxes.HarnessError(f"{path} is not a rails approval record")
    digest = data.get("digest")
    if not isinstance(digest, str) or not digest:
        raise boxes.HarnessError(f"{path} names no digest: an approval of nothing is not one")
    raw_delegations = data.get("delegations", [])
    if not isinstance(raw_delegations, list):
        raise boxes.HarnessError(f"{path} names delegations that are not a list")
    delegations = tuple(
        Delegation(
            question=str(item.get("question", "")),
            default=str(item.get("default", "")),
        )
        for item in raw_delegations
        if isinstance(item, dict)
    )
    return Approval(
        path=path,
        at=str(data.get("at", "")),
        digest=digest,
        by=str(data.get("by", "")),
        note=str(data.get("note", "")),
        delegations=delegations,
    )


def latest_revision(box: boxes.Box) -> Revision | None:
    """The newest revision record, or ``None``. An unreadable record is an error, not a skip."""
    directory = box.root / RECORDS
    if not directory.is_dir():
        return None
    records = sorted(
        directory.glob("rails-revision-*.json"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
    )
    if not records:
        return None
    path = records[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"the rails revision {path} does not parse: {error}") from error
    if not isinstance(data, dict) or data.get("type") != REVISION_TYPE:
        raise boxes.HarnessError(f"{path} is not a rails revision record")
    kind = str(data.get("kind", ""))
    if kind not in KINDS:
        raise boxes.HarnessError(
            f"{path} names revision kind {kind!r}, which is not one of {KINDS}"
        )
    before = data.get("before", [])
    return Revision(
        path=path,
        at=str(data.get("at", "")),
        kind=kind,
        from_digest=str(data.get("from", "")),
        to_digest=str(data.get("to", "")),
        note=str(data.get("note", "")),
        breaks=_optional(data.get("breaks")),
        lose=_optional(data.get("lose")),
        by=str(data.get("by", "")),
        before=tuple(str(item) for item in before) if isinstance(before, list) else (),
    )


def closures(box: boxes.Box) -> dict[str, Closure]:
    """Every milestone closed by a record, by id; an unreadable record is an error, not a skip."""
    directory = box.root / RECORDS
    if not directory.is_dir():
        return {}
    found: dict[str, Closure] = {}
    for path in sorted(
        directory.glob("milestone-*-closed.json"),
        key=lambda item: item.stat().st_mtime_ns,
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise boxes.HarnessError(
                f"the milestone closure {path} does not parse: {error}"
            ) from error
        if not isinstance(data, dict) or data.get("type") != CLOSURE_TYPE:
            raise boxes.HarnessError(f"{path} is not a milestone closure record")
        milestone = str(data.get("milestone", ""))
        if not milestone:
            raise boxes.HarnessError(f"{path} names no milestone")
        found[milestone] = Closure(
            path=path,
            at=str(data.get("at", "")),
            milestone=milestone,
            title=str(data.get("title", "")),
            closes_when=str(data.get("closes_when", "")),
            digest=str(data.get("digest", "")),
            by=str(data.get("by", "")),
            note=str(data.get("note", "")),
        )
    return found


def close(
    box: boxes.Box,
    milestone: str,
    note: str,
    *,
    by: str = "owner",
    when: datetime | None = None,
) -> Closure:
    """Record that a milestone's closing condition is met; the frame itself is not edited.

    The record names the digest of the frame the decision was made on: a frame that moves
    afterwards does not silently inherit the closure.
    """
    if not note.strip():
        raise boxes.HarnessError("a milestone closes for a reason: `--note` is empty")
    current = read(box.project)
    if current is None:
        raise boxes.HarnessError(
            f"there are no rails to close a milestone on in {box.project}: {RAILS_FILE} "
            "does not exist"
        )
    found = current.milestone(milestone)
    if found is None:
        known = ", ".join(item.id for item in current.milestones) or "none"
        raise boxes.HarnessError(
            f"the frame does not declare milestone {milestone!r}; known milestones: {known}"
        )
    if found.state == "closed":
        raise boxes.HarnessError(f"the frame already says milestone {milestone!r} is closed")
    existing = closures(box).get(milestone)
    if existing is not None:
        raise boxes.HarnessError(
            f"milestone {milestone!r} was already closed {existing.at} by {existing.by}"
        )
    how = standing(box)
    if how.state != "approved":
        raise boxes.HarnessError(
            "a milestone closes on an accepted frame: accept the frame first `pantheon rails "
            "--approve`"
        )
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box.root / RECORDS
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"milestone-{milestone}-closed.json"
    record = {
        "schema_version": SCHEMA_VERSION,
        "type": CLOSURE_TYPE,
        "at": _stamp(stamp),
        "milestone": milestone,
        "title": found.title,
        "closes_when": found.closes_when,
        "metric": found.metric,
        "digest": how.digest,
        "by": by,
        "note": note,
        "base": caesar.head(box.project),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_event(box, by, note, path, stamp, kind="rails.closed")
    return Closure(
        path=path,
        at=_stamp(stamp),
        milestone=milestone,
        title=found.title,
        closes_when=found.closes_when,
        digest=how.digest,
        by=by,
        note=note,
    )


def standing(box: boxes.Box) -> Standing:
    """The rails on disk against the newest approval: the answer a dispatch will record."""
    current = read(box.project)
    approval = latest(box)
    if current is None:
        return Standing("none", "", approval)
    if approval is None:
        return Standing("never", current.digest, None)
    if approval.digest != current.digest:
        return Standing("changed", current.digest, approval)
    return Standing("approved", current.digest, approval)


def check(box: boxes.Box, *, milestone: str | None, outside: str | None) -> Check:
    """Admit a task by the rails, or say why it does not start.

    Only an approved frame refuses: without one the task runs and the gap says that no milestone
    holds it. A frame with a milestone opens the task only to the milestone it names; a reason
    for standing outside the plan is an explicit decision and is recorded as one.
    """
    how = standing(box)
    if how.state != "approved":
        revision = latest_revision(box)
        if (
            revision is not None
            and revision.kind in HEAVY
            and revision.to_digest == how.digest
        ):
            return Check(
                how,
                refusal=(
                    f"the frame was rewritten ({revision.kind}) and the new frame was never "
                    "accepted: a heavy revision waits for `pantheon rails --approve` before a "
                    "task runs against it"
                ),
            )
        return Check(how)
    current = read(box.project)
    if current is None:
        return Check(how)
    if outside is not None:
        return Check(how, outside=outside)
    if not milestone:
        return Check(
            how,
            refusal=(
                "the rails are approved and this task names no milestone: a change either serves "
                'a milestone (`milestone = "M3"`) or says why it stands outside the plan '
                '(`outside_plan = "why"`)'
            ),
        )
    found = current.milestone(milestone)
    if found is None:
        known = ", ".join(item.id for item in current.milestones) or "none"
        return Check(
            how,
            refusal=(
                f"the task names milestone {milestone!r}, which the frame does not declare; "
                f"known milestones: {known}"
            ),
        )
    return Check(how, milestone=found)


def render(
    standing: Standing,
    current: Rails | None,
    revision: Revision | None = None,
    closed: dict[str, Closure] | None = None,
) -> list[str]:
    """The rails as a person reads them: the frame, what is missing, and who accepted it."""
    lines = [f"rails      {standing.line()}"]
    if current is None:
        lines.append(f"file       {RAILS_FILE}  missing")
    else:
        lines.append(f"file       {RAILS_FILE}  {_short(current.digest)}")
        lines.append(f"why        {current.brief.why}")
        lines.append(f"for        {current.brief.for_whom}")
        lines.append(f"success    {current.brief.success}")
        for goal in current.goals:
            lines.append(f"goal       {goal.text}")
        for milestone in current.milestones:
            closure = None if closed is None else closed.get(milestone.id)
            state = "closed" if closure is not None else milestone.state
            lines.append(f"milestone  {milestone.id}  {milestone.title}  ({state})")
            lines.append(f"           closes when: {milestone.closes_when}")
            lines.append(f"           metric: {milestone.metric}")
            if closure is not None:
                lines.append(f"           closed {closure.at} by {closure.by}")
                if closure.note:
                    lines.append(f"           note {closure.note}")
        for metric in current.metrics:
            lines.append(f"metric     {metric.name}  {metric.direction}  {metric.threshold}")
        for principle in current.principles:
            lines.append(f"principle  {principle.text}")
            if principle.why:
                lines.append(f"           because: {principle.why}")
        for absent in current.missing:
            lines.append(f"missing    {absent}")
    if standing.approval is not None:
        approval = standing.approval
        lines.append(f"approved   {approval.at} by {approval.by}")
        lines.append(f"record     {approval.path}")
        if approval.note:
            lines.append(f"note       {approval.note}")
        for item in approval.delegations:
            lines.append(f"delegated  {item.question} -> {item.default}")
    if revision is not None:
        lines.append(f"revision   {revision.kind} {revision.at} by {revision.by}")
        lines.append(
            f"           accepted {_short(revision.from_digest)} -> now {revision.to_digest[:12]}"
        )
        lines.append(f"           note {revision.note}")
        if revision.breaks:
            lines.append(f"           breaks: {revision.breaks}")
        if revision.lose:
            lines.append(f"           lose: {revision.lose}")
        if revision.before:
            lines.append(f"           before: {', '.join(revision.before)}")
        accepted = standing.approval
        if accepted is not None and revision.at > accepted.at:
            lines.append(
                "           not accepted yet — `pantheon rails --approve` makes the new frame "
                "the basis"
            )
    return lines


def payload(
    standing: Standing,
    current: Rails | None,
    revision: Revision | None = None,
    closed: dict[str, Closure] | None = None,
) -> dict[str, object]:
    """The rails as one JSON object: the same facts the render puts on lines."""
    body = standing.json()
    if current is None:
        body["file"] = RAILS_FILE
        body["missing"] = []
        return body
    body["file"] = RAILS_FILE
    body["missing"] = list(current.missing)
    body["revision"] = None if revision is None else revision.json()
    body["brief"] = {
        "why": current.brief.why,
        "for": current.brief.for_whom,
        "success": current.brief.success,
    }
    body["goals"] = [goal.text for goal in current.goals]
    body["milestones"] = [
        {
            "id": milestone.id,
            "title": milestone.title,
            "closes_when": milestone.closes_when,
            "metric": milestone.metric,
            "state": (
                "closed"
                if closed is not None and milestone.id in closed
                else milestone.state
            ),
        }
        for milestone in current.milestones
    ]
    body["closures"] = (
        []
        if closed is None
        else [item.json() for item in sorted(closed.values(), key=lambda item: item.milestone)]
    )
    body["metrics"] = [
        {"name": metric.name, "direction": metric.direction, "threshold": metric.threshold}
        for metric in current.metrics
    ]
    body["principles"] = [
        {"text": principle.text, "why": principle.why} for principle in current.principles
    ]
    return body


def _missing(
    brief: Brief,
    goals: tuple[Goal, ...],
    milestones: tuple[Milestone, ...],
    metrics: tuple[Metric, ...],
    principles: tuple[Principle, ...],
) -> tuple[str, ...]:
    """Every part of the rail that was not filled, named one by one instead of counted."""
    absent: list[str] = []
    for label, value in (
        ("brief.why", brief.why),
        ("brief.for", brief.for_whom),
        ("brief.success", brief.success),
    ):
        if not value:
            absent.append(label)
    if not goals:
        absent.append("goals")
    if not milestones:
        absent.append("milestones")
    if not metrics:
        absent.append("metrics")
    for milestone in milestones:
        if not milestone.title:
            absent.append(f"milestone {milestone.id}.title")
        if not milestone.closes_when:
            absent.append(f"milestone {milestone.id}.closes_when")
        if not milestone.metric:
            absent.append(f"milestone {milestone.id}.metric")
    for metric in metrics:
        if not metric.threshold:
            absent.append(f"metric {metric.name}.threshold")
    if not principles:
        absent.append("principles")
    return tuple(absent)


def _check_metrics(metrics: tuple[Metric, ...], path: Path) -> None:
    names: list[str] = []
    for metric in metrics:
        if not metric.name:
            raise boxes.HarnessError(f"the rails {path} have a metric with no name")
        if metric.name in names:
            raise boxes.HarnessError(f"the rails {path} name the metric {metric.name!r} twice")
        if metric.direction not in DIRECTIONS:
            raise boxes.HarnessError(
                f"the rails {path} say direction {metric.direction!r} for {metric.name!r}; "
                f"expected one of {', '.join(sorted(DIRECTIONS))}"
            )
        names.append(metric.name)


def _check_milestones(
    milestones: tuple[Milestone, ...], metrics: tuple[Metric, ...], path: Path
) -> None:
    names = {metric.name for metric in metrics}
    seen: list[str] = []
    for milestone in milestones:
        if not milestone.id:
            raise boxes.HarnessError(f"the rails {path} have a milestone with no id")
        if milestone.id in seen:
            raise boxes.HarnessError(f"the rails {path} name the milestone {milestone.id!r} twice")
        if milestone.metric and milestone.metric not in names:
            raise boxes.HarnessError(
                f"the rails {path} put milestone {milestone.id!r} on the metric "
                f"{milestone.metric!r}, which no metric table declares"
            )
        if milestone.state not in STATES:
            raise boxes.HarnessError(
                f"the rails {path} say state {milestone.state!r} for milestone {milestone.id!r}; "
                f"expected one of {', '.join(sorted(STATES))}"
            )
        seen.append(milestone.id)


def _table(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise boxes.HarnessError(f"the rails have {where} that is not a table")
    return {str(key): item for key, item in value.items()}


def _tables(data: dict[str, object], key: str) -> list[dict[str, object]]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise boxes.HarnessError(f"the rails have {key} that is not an array of tables")
    return [_table(item, key) for item in value]


def _text(data: dict[str, object], key: str, where: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise boxes.HarnessError(f"the rails have {where}.{key} that is not a string")
    return value.strip()


def _older_tasks(box: boxes.Box, digest: str) -> tuple[str, ...]:
    """The tasks whose record rested on an older frame: the ones a departure leaves behind."""
    base = box.root / "tabularium"
    if not base.is_dir():
        return ()
    found: list[str] = []
    for record in sorted(base.glob("*/dispatch-*.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        held = data.get("rails")
        if not isinstance(held, dict):
            continue
        older = held.get("digest")
        if isinstance(older, str) and older and older != digest:
            task = str(data.get("task") or record.parent.name)
            if task not in found:
                found.append(task)
    return tuple(found)


def _optional(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _free_path(directory: Path, when: datetime, *, prefix: str = "rails-approval") -> Path:
    """Two records in one second are two records, not one silently overwriting the other."""
    path = directory / f"{prefix}-{when:%Y%m%dT%H%M%SZ}.json"
    counter = 2
    while path.exists():
        path = directory / f"{prefix}-{when:%Y%m%dT%H%M%SZ}-{counter}.json"
        counter += 1
    return path


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_event(
    box: boxes.Box, by: str, note: str, record: Path, when: datetime, *, kind: str
) -> None:
    """One ledger event per record: a record the timeline cannot find is half a record."""
    ledger = box.root / "fasti"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    try:
        link = str(record.relative_to(box.root))
    except ValueError:
        link = str(record)
    event = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": kind,
        "at": _stamp(when),
        "task": None,
        "actor": by,
        "reason": note,
        "links": [link],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
