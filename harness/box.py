"""A box: the directory that holds one project's work, its history and, one day, its agent.

The box lives outside the project (ADR-001): the project's git carries its promises, the box
carries the record of working on them, and the only link between the two is the path in
``castrum.toml``. That is why ``remove the box`` must leave the project intact — nothing here
writes into the project, and ``status`` only reads it.

What this module refuses to pretend: ``up`` starts the container and ``status`` asks it how
it is, so a box is "running" only when the agent answers, never because a state machine says so.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
"""Bumped when castrum.toml grows a field that older code cannot read correctly."""

BOX_DIRS = (
    "persona",  # SOUL.md, tool ACL, model: whose agent this is
    "cella",  # the agent's volume: sessions, its memory, cache
    "fasti",  # events, one jsonl per day
    "tabularium",  # run directories: report.json/md, check logs
    "mandata",  # tasks and execution contracts
    "acta",  # summaries for the owner's own assistant
    "archivum",  # closed changes with their reasons, indexed by path
)
"""Every box has these, empty on day one; ``status`` names the ones that are missing."""

PROFILE_TEMPLATE = Path(__file__).with_name("profiles") / "augustus.md"
"""The agent's starting profile: copied into the box, then edited there, not here."""

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
"""A box name becomes a directory and a compose project name: lowercase, hyphenated, short."""

BOX_KEYS = frozenset({"schema_version", "name", "project", "image", "created"})


class HarnessError(Exception):
    """Something the caller may have meant but cannot have: bad name, missing project, no box."""


@dataclass(frozen=True, slots=True)
class Box:
    """One box: where it lives, which project it serves, which agent it will run."""

    root: Path
    name: str
    project: Path
    created: str
    image: str | None = None

    @property
    def file(self) -> Path:
        return self.root / "castrum.toml"

    @property
    def compose(self) -> Path:
        return self.root / "compose.yaml"


@dataclass(frozen=True, slots=True)
class Requirement:
    """One promise the project makes, as the project files it."""

    id: str
    status: str


@dataclass(frozen=True, slots=True)
class ProjectFacts:
    """What ``status`` can say about the project without starting anything.

    Every field is best-effort on purpose: a project with a broken ``policy.toml`` still has a
    path and a git branch, and ``status`` exists to describe the damage, not to add to it.
    """

    path: str
    exists: bool
    branch: str | None = None
    uncommitted: int | None = None
    policy_name: str | None = None
    checks: tuple[str, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    has_baseline: bool = False
    has_target: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BoxStatus:
    """The whole answer of ``pantheon status``: the box, the project, and what is missing."""

    box: Box
    project: ProjectFacts
    missing_dirs: tuple[str, ...]
    compose_services: tuple[str, ...]

    @property
    def approved(self) -> int:
        return sum(1 for item in self.project.requirements if item.status == "approved")

    @property
    def proposed(self) -> int:
        return sum(1 for item in self.project.requirements if item.status == "proposed")


def today() -> str:
    """The creation stamp, in UTC and to the second, because boxes get compared across hosts."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_name(name: str) -> str:
    """A box name that is a safe directory and a safe compose project."""
    if not NAME_PATTERN.match(name):
        raise HarnessError(
            f"castrum name {name!r} is not usable: lowercase letters, digits and hyphens, "
            "starting with a letter or digit, at most 40 characters"
        )
    return name


def check_image(image: str) -> str:
    """A pinned image reference: a tag or a digest, never ``latest`` (ADR-010)."""
    value = image.strip()
    last = value.rsplit("/", 1)[-1]
    if not value or ":" not in last or not last.rsplit(":", 1)[1]:
        raise HarnessError(
            f"image {value!r} has no tag: a box pins an exact version, not a moving name"
        )
    if last.rsplit(":", 1)[1] == "latest":
        raise HarnessError(
            f"image {value!r} is pinned to latest, which pins nothing: an update would move "
            "every box at once (ADR-010)"
        )
    return value


def read_box(path: Path) -> Box:
    """Load ``castrum.toml``; an unknown key is an error: it would be a field nobody reads."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - BOX_KEYS
    if unknown:
        raise HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    missing = [key for key in ("name", "project", "created") if not isinstance(data.get(key), str)]
    if missing:
        raise HarnessError(f"{path}: missing or non-text key(s) {', '.join(missing)}")
    image = data.get("image")
    if image is not None and not isinstance(image, str):
        raise HarnessError(f"{path}: image must be text")
    root = path.parent
    return Box(
        root=root,
        name=data["name"],
        project=Path(data["project"]),
        created=data["created"],
        image=image,
    )


def render_box(box: Box) -> str:
    """``castrum.toml`` as a person reads it: the comments say why each field exists."""
    lines = [
        "# What this box is: which project it serves, which agent image it will run, when it",
        "# was created. Written by `pantheon new`, read by every command, reviewed as data.",
        "#",
        "# The image tag is pinned here and `latest` is refused (ADR-010): an update that moves",
        "# a tag would otherwise move every box at once. Until there is an image to name, the",
        "# key is absent and `pantheon status` says the tag is not chosen yet.",
        f"schema_version = {SCHEMA_VERSION}",
        f"name = {_text(box.name)}",
        f"project = {_text(box.project.as_posix())}",
    ]
    if box.image is not None:
        lines.append(f"image = {_text(box.image)}")
    lines.append(f"created = {_text(box.created)}")
    return "\n".join(lines) + "\n"


def render_compose() -> str:
    """A compose file with an empty service map: no image is pinned yet, so nothing runs.

    `pantheon up` rewrites this file from castrum.toml once an image is pinned. Writing a
    half-configured service here would be worse than an empty map: a file that looks runnable
    and runs nothing is the kind of placeholder this harness exists to avoid.
    """
    return (
        "# The box's container. No image is pinned yet, so the service map is empty and\n"
        "# `pantheon up` asks for one before it starts anything.\n"
        "services: {}\n"
        "\n"
        "volumes: {}\n"
    )


def new_box(
    boxes_root: Path,
    project: Path,
    name: str | None = None,
    image: str | None = None,
) -> Box:
    """Create a box for ``project`` under ``boxes_root``, refusing anything ambiguous."""
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise HarnessError(f"project {project} is not a directory")
    chosen = check_name(name if name is not None else _name_from(project))
    root = (boxes_root.expanduser().resolve()) / chosen
    if root.exists():
        raise HarnessError(f"castrum {chosen} already exists at {root}; remove it or pick a name")
    box = Box(
        root=root,
        name=chosen,
        project=project,
        created=today(),
        image=check_image(image) if image is not None else None,
    )
    for directory in BOX_DIRS:
        (root / directory).mkdir(parents=True)
    (root / "persona" / "SOUL.md").write_text(
        PROFILE_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    box.file.write_text(render_box(box), encoding="utf-8")
    box.compose.write_text(render_compose(), encoding="utf-8")
    return box


def list_boxes(boxes_root: Path) -> tuple[Box, ...]:
    """Every box under ``boxes_root`` that has a readable ``castrum.toml``, by name."""
    base = boxes_root.expanduser()
    if not base.is_dir():
        return ()
    found: list[Box] = []
    for child in sorted(base.iterdir()):
        if (child / "castrum.toml").is_file():
            found.append(read_box(child / "castrum.toml"))
    return tuple(found)


def find_box(boxes_root: Path, name: str | None, cwd: Path) -> Box:
    """The box the caller means: the named one, the one above ``cwd``, or the only one."""
    base = boxes_root.expanduser().resolve()
    if name is not None:
        path = base / check_name(name) / "castrum.toml"
        if not path.is_file():
            raise HarnessError(
                f"no castrum {name!r} under {base}; `pantheon list` shows what is there"
            )
        return read_box(path)
    here = cwd.resolve()
    for parent in (here, *here.parents):
        if parent.parent == base and (parent / "castrum.toml").is_file():
            return read_box(parent / "castrum.toml")
    boxes = list_boxes(base)
    if len(boxes) == 1:
        return boxes[0]
    if not boxes:
        raise HarnessError(f"no castra under {base}; `pantheon new` creates one")
    names = ", ".join(box.name for box in boxes)
    raise HarnessError(f"several castra under {base} ({names}); name one: `pantheon status <name>`")


def read_project(project: Path) -> ProjectFacts:
    """Read the promises and the arrangement the project keeps in ``.trajan/``."""
    if not project.is_dir():
        return ProjectFacts(
            path=str(project), exists=False, notes=("the project directory is gone",)
        )
    dirty = _git(project, "status", "--porcelain")
    facts = ProjectFacts(
        path=str(project),
        exists=True,
        branch=_git(project, "rev-parse", "--abbrev-ref", "HEAD"),
        uncommitted=None if dirty is None else len(dirty.splitlines()),
    )
    notes: list[str] = []
    facts, notes = _read_policy(facts, notes)
    facts, notes = _read_context(facts, notes)
    return replace(facts, notes=tuple(notes))


def _read_policy(facts: ProjectFacts, notes: list[str]) -> tuple[ProjectFacts, list[str]]:
    path = Path(facts.path, ".trajan", "policy.toml")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        notes.append("no .trajan/policy.toml: nothing would be checked")
    except (OSError, tomllib.TOMLDecodeError) as error:
        notes.append(f"policy.toml does not parse: {error}")
    else:
        checks = tuple(
            str(entry["id"])
            for entry in raw.get("checks", [])
            if isinstance(entry, dict) and "id" in entry
        )
        facts = replace(facts, policy_name=raw.get("name"), checks=checks)
    return facts, notes


def _read_context(facts: ProjectFacts, notes: list[str]) -> tuple[ProjectFacts, list[str]]:
    path = Path(facts.path, ".trajan", "context.toml")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        notes.append("no .trajan/context.toml: no requirements to trace against")
    except (OSError, tomllib.TOMLDecodeError) as error:
        notes.append(f"context.toml does not parse: {error}")
    else:
        requirements = tuple(
            Requirement(str(entry["id"]), str(entry.get("status")))
            for entry in raw.get("requirements", [])
            if isinstance(entry, dict) and "id" in entry
        )
        architecture = raw.get("architecture", {})
        declared = architecture if isinstance(architecture, dict) else {}
        facts = replace(
            facts,
            requirements=requirements,
            has_baseline=bool(declared.get("baseline")),
            has_target=bool(declared.get("target")),
        )
    return facts, notes


def status(box: Box) -> BoxStatus:
    """Everything ``pantheon status`` shows: read, never executed."""
    missing = tuple(
        directory for directory in BOX_DIRS if not (box.root / directory).is_dir()
    )
    return BoxStatus(
        box=box,
        project=read_project(box.project),
        missing_dirs=missing,
        compose_services=compose_services(box.compose),
    )


def compose_services(path: Path) -> tuple[str, ...]:
    """Service names declared under ``services:`` in the box's compose file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    services: list[str] = []
    inside = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            inside = line.strip() == "services:"
            continue
        if inside and len(line) - len(line.lstrip()) == 2 and line.strip().endswith(":"):
            services.append(line.strip()[:-1])
    return tuple(services)


def render_status(report: BoxStatus) -> list[str]:
    """The same facts as text, aligned so a person scans them instead of reading them."""
    box, project = report.box, report.project
    project_bits = [str(project.path)]
    if project.branch is not None:
        project_bits.append(f"git {project.branch}")
    if project.uncommitted:
        project_bits.append(f"{project.uncommitted} uncommitted")
    checks = ", ".join(project.checks) if project.checks else "none"
    requirements = (
        f"{len(project.requirements)} — {report.approved} approved, {report.proposed} proposed"
        if project.requirements
        else "none"
    )
    layout = "complete" if not report.missing_dirs else "missing " + ", ".join(report.missing_dirs)
    services = ", ".join(report.compose_services) or "none (pin an image with `pantheon up`)"
    lines = [
        f"castrum    {box.name}  ({box.root})",
        f"project    {' '.join(project_bits)}",
        f"image      {box.image or 'not pinned yet (`pantheon up` pins it)'}",
        f"created    {box.created}",
        f"layout     {layout}",
        f"policy     {project.policy_name or 'no policy'}; checks: {checks}",
        f"context    {requirements}",
        f"arch       {_architecture(project)}",
        f"compose    {services}",
        "augustus   the orchestrator is `pantheon dispatch`",
    ]
    for note in project.notes:
        lines.append(f"note       {note}")
    return lines


def _architecture(project: ProjectFacts) -> str:
    """What the project says about where it is and where it is going."""
    parts = [
        label
        for label, present in (
            ("baseline", project.has_baseline),
            ("target", project.has_target),
        )
        if present
    ]
    return " and ".join(parts) if parts else "none declared"


def status_json(report: BoxStatus) -> dict[str, object]:
    """The same facts for a machine: one object, no prose, stable key names."""
    box, project = report.box, report.project
    return {
        "box": {
            "name": box.name,
            "root": str(box.root),
            "image": box.image,
            "created": box.created,
            "missing_dirs": list(report.missing_dirs),
            "compose_services": list(report.compose_services),
        },
        "project": {
            "path": project.path,
            "exists": project.exists,
            "branch": project.branch,
            "uncommitted": project.uncommitted,
            "policy": project.policy_name,
            "checks": list(project.checks),
            "requirements": [
                {"id": item.id, "status": item.status} for item in project.requirements
            ],
            "has_baseline": project.has_baseline,
            "has_target": project.has_target,
            "notes": list(project.notes),
        },
        "agent": {
            "running": False,
            "reason": "the orchestrator runs as `pantheon dispatch`; the agent process needs "
            "the container, which is not built",
        },
    }


def _name_from(project: Path) -> str:
    """A default box name from the project directory: ``DocFlow`` becomes ``docflow``."""
    slug = re.sub(r"[^a-z0-9]+", "-", project.name.lower()).strip("-")
    if not slug:
        raise HarnessError(f"cannot name a box after {project.name!r}; pass --name")
    return slug


def _text(value: str) -> str:
    """A TOML basic string: quotes and backslashes escaped, nothing else needs it."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _git(project: Path, *args: str) -> str | None:
    """Run one read-only git query; ``None`` when git is absent or the path is not a repo."""
    try:
        done = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None
