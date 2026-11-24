"""Aurelius: the architecture the project declares, and the check that it still holds.

The model is a file a person writes and reviews (``.pantheon/architecture.toml``): which
packages exist, which may depend on which, and which dependencies are forbidden outright.
Aurelius does not judge the answer — it compiles the model into the configuration of an
existing boundary tool (``import-linter``) and runs it. Writing our own import analyser would
be a second opinion about the same fact, and the point of this contour is to have one.

What the check will not do: pretend. If the tool is not installed, the answer is "not checked"
with the reason, the record says so, and the exit code is 2 — an unseen architecture is not a
clean one. The tool's own output is kept verbatim as evidence; its exit code and its
"Contracts: N kept, M broken" line are the verdict, and nothing here parses prose into
findings it did not read.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import box as boxes

SCHEMA_VERSION = 1
MODEL_FILE = ".pantheon/architecture.toml"
MODEL_KEYS = frozenset({"schema_version", "project", "architecture", "layer", "forbidden"})
PROJECT_KEYS = frozenset({"root_packages", "exclude_type_checking_imports"})
LAYER_KEYS = frozenset({"name", "modules", "independent"})
FORBIDDEN_KEYS = frozenset({"name", "source", "target", "reason"})
TOOL_ENV = "PANTHEON_LINT"
DEFAULT_TOOL = "lint-imports"
SUMMARY = re.compile(r"Contracts: (?P<kept>\d+) kept, (?P<broken>\d+) broken")


@dataclass(frozen=True, slots=True)
class Layer:
    """One layer of the model, highest first: a layer may import the ones below it.

    Modules inside one layer are peers. By default they may import each other; with
    ``independent = true`` they may not, which is how "these are alternatives, not a stack"
    is written down.
    """

    name: str
    modules: tuple[str, ...]
    independent: bool = False


@dataclass(frozen=True, slots=True)
class Forbidden:
    """A dependency that must never exist, with the reason a person wrote down."""

    name: str
    source: tuple[str, ...]
    target: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class Model:
    """The declared architecture, and where it was read from."""

    path: Path
    root_packages: tuple[str, ...]
    layers: tuple[Layer, ...]
    forbidden: tuple[Forbidden, ...]
    baseline: str | None
    target: str | None
    digest: str
    exclude_type_checking: bool = False


@dataclass(frozen=True, slots=True)
class Result:
    """What the boundary tool said, and what the box kept as evidence."""

    status: str  # "ok", "broken", "unchecked", "error"
    kept: int
    broken: int
    tool: str
    version: str
    directory: Path
    exit_code: int
    digest: str
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.status == "ok"


def read_model(path: Path) -> Model:
    """Load the architecture model; an unknown key is an error, because nobody would enforce it."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise boxes.HarnessError(
            f"no architecture model at {path}; the check needs a model before it can disagree"
        ) from error
    except OSError as error:
        raise boxes.HarnessError(f"cannot read {path}: {error}") from error
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"{path} does not parse: {error}") from error
    unknown = set(data) - MODEL_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise boxes.HarnessError(f"{path}: project must be a table")
    unknown = set(project) - PROJECT_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown project key(s) {', '.join(sorted(unknown))}")
    roots = project.get("root_packages", [])
    if not isinstance(roots, list) or not roots or not all(isinstance(item, str) for item in roots):
        raise boxes.HarnessError(
            f"{path}: project.root_packages must be a non-empty list of importable package names"
        )
    layers = tuple(
        _read_layer(path, entry, index) for index, entry in enumerate(data.get("layer", []))
    )
    forbidden = tuple(
        _read_forbidden(path, entry, index)
        for index, entry in enumerate(data.get("forbidden", []))
    )
    if not layers and not forbidden:
        raise boxes.HarnessError(
            f"{path}: no [[layer]] and no [[forbidden]]: a model that says nothing cannot be broken"
        )
    exclude = project.get("exclude_type_checking_imports", False)
    if not isinstance(exclude, bool):
        raise boxes.HarnessError(f"{path}: exclude_type_checking_imports must be true or false")
    architecture = data.get("architecture", {})
    if not isinstance(architecture, dict):
        raise boxes.HarnessError(f"{path}: architecture must be a table")
    return Model(
        path=path,
        root_packages=tuple(roots),
        layers=layers,
        forbidden=forbidden,
        baseline=_text(architecture.get("baseline")),
        target=_text(architecture.get("target")),
        digest=hashlib.sha256(raw).hexdigest(),
        exclude_type_checking=exclude,
    )


def compile_config(model: Model) -> str:
    """The model as ``import-linter`` reads it: one layers contract, one contract per rule.

    All layers go into a *single* layers contract. A contract with one layer forbids nothing —
    it "keeps" trivially — so compiling each ``[[layer]]`` separately would produce a check
    that always passes, which is worse than no check. Peers written on one layer line are
    joined with " : " so that they may import each other; a layer may import the ones below.
    """
    lines = ["[importlinter]", "root_packages ="]
    lines.extend(f"    {root}" for root in model.root_packages)
    if model.exclude_type_checking:
        # A type-only import under `if TYPE_CHECKING:` is not a dependency at run time; a
        # model that counted it would forbid the annotations that make the layers visible.
        lines.append("exclude_type_checking_imports = true")
    if model.layers:
        lines += [
            "",
            "[importlinter:contract:layers]",
            "name = declared layers",
            "type = layers",
            "layers =",
        ]
        lines.extend(
            "    " + (" | " if layer.independent else " : ").join(layer.modules)
            for layer in model.layers
        )
    for index, rule in enumerate(model.forbidden, start=1):
        lines += [
            "",
            f"[importlinter:contract:forbidden-{index}]",
            f"name = {rule.name}",
            "type = forbidden",
            "source_modules =",
        ]
        lines.extend(f"    {source}" for source in rule.source)
        lines.append("forbidden_modules =")
        lines.extend(f"    {target}" for target in rule.target)
    return "\n".join(lines) + "\n"


def check(
    box_root: Path,
    project: Path,
    model: Model,
    *,
    tool: str | None = None,
    when: datetime | None = None,
    timeout: int = 300,
) -> Result:
    """Run the boundary tool from the model, keep what it said, and record the event."""
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    directory = box_root / "artifacts" / "arch" / stamp.strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "importlinter.ini"
    config.write_text(compile_config(model), encoding="utf-8")
    executable = _find_tool(tool)
    if executable is None:
        result = Result(
            status="unchecked",
            kept=0,
            broken=0,
            tool=tool or os.environ.get(TOOL_ENV, DEFAULT_TOOL),
            version="",
            directory=directory,
            exit_code=2,
            digest=model.digest,
            notes=[
                f"the boundary tool is not installed: looked for {DEFAULT_TOOL} on PATH and "
                f"for ${TOOL_ENV}; the architecture was not checked"
            ],
        )
        _write_record(box_root, project, model, result, "", "", stamp)
        return result
    version = _version(executable)
    try:
        done = subprocess.run(
            [str(executable), str(config)],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=_environment(project),
        )
    except (OSError, subprocess.SubprocessError) as error:
        result = Result(
            status="error",
            kept=0,
            broken=0,
            tool=str(executable),
            version=version,
            directory=directory,
            exit_code=2,
            digest=model.digest,
            notes=[f"the boundary tool could not run: {error}"],
        )
        _write_record(box_root, project, model, result, "", "", stamp)
        return result
    summary = SUMMARY.search(done.stdout)
    kept = int(summary["kept"]) if summary else 0
    broken = int(summary["broken"]) if summary else 0
    if done.returncode == 0:
        status = "ok"
    elif summary is not None:
        status = "broken"
    else:
        status = "error"
    result = Result(
        status=status,
        kept=kept,
        broken=broken,
        tool=str(executable),
        version=version,
        directory=directory,
        exit_code=0 if status == "ok" else 1 if status == "broken" else 2,
        digest=model.digest,
        notes=[] if status != "error" else ["the tool exited without its summary line"],
    )
    _write_record(box_root, project, model, result, done.stdout, done.stderr, stamp)
    return result


def render_result(result: Result, model: Model) -> list[str]:
    """The check as a person reads it, with the record last so a script can find it."""
    lines = [
        f"model      {model.path} (sha256:{model.digest[:16]})",
        f"tool       {result.tool}{' ' + result.version if result.version else ''}",
        f"contracts  {result.kept} kept, {result.broken} broken",
        f"result     {result.status}",
    ]
    for note in result.notes:
        lines.append(f"note       {note}")
    lines.append(f"record     {result.directory}")
    return lines


def result_json(result: Result, model: Model) -> dict[str, object]:
    """The same answer for a machine."""
    return {
        "model": str(model.path),
        "model_digest": f"sha256:{result.digest}",
        "tool": result.tool,
        "tool_version": result.version,
        "kept": result.kept,
        "broken": result.broken,
        "status": result.status,
        "notes": list(result.notes),
        "record": str(result.directory),
        "exit_code": result.exit_code,
    }


def _read_layer(path: Path, entry: object, index: int) -> Layer:
    if not isinstance(entry, dict):
        raise boxes.HarnessError(f"{path}: [[layer]] #{index + 1} is not a table")
    unknown = set(entry) - LAYER_KEYS
    if unknown:
        raise boxes.HarnessError(
            f"{path}: [[layer]] #{index + 1} has unknown key(s) {', '.join(sorted(unknown))}"
        )
    name = entry.get("name")
    modules = entry.get("modules")
    if not isinstance(name, str) or not name.strip() or "\n" in name:
        raise boxes.HarnessError(f"{path}: [[layer]] #{index + 1} needs a one-line name")
    if not isinstance(modules, list) or not modules or not all(
        isinstance(item, str) and item.strip() for item in modules
    ):
        raise boxes.HarnessError(
            f"{path}: [[layer]] #{index + 1} needs modules: the importable names it contains"
        )
    independent = entry.get("independent", False)
    if not isinstance(independent, bool):
        raise boxes.HarnessError(
            f"{path}: [[layer]] #{index + 1}: independent must be true or false"
        )
    return Layer(name.strip(), tuple(item.strip() for item in modules), independent)


def _read_forbidden(path: Path, entry: object, index: int) -> Forbidden:
    if not isinstance(entry, dict):
        raise boxes.HarnessError(f"{path}: [[forbidden]] #{index + 1} is not a table")
    unknown = set(entry) - FORBIDDEN_KEYS
    if unknown:
        raise boxes.HarnessError(
            f"{path}: [[forbidden]] #{index + 1} has unknown key(s) {', '.join(sorted(unknown))}"
        )
    name = entry.get("name")
    source = entry.get("source")
    target = entry.get("target")
    reason = entry.get("reason")
    if not isinstance(name, str) or not name.strip():
        raise boxes.HarnessError(f"{path}: [[forbidden]] #{index + 1} needs a name")
    sources = _module_list(source)
    targets = _module_list(target)
    if sources is None or targets is None:
        raise boxes.HarnessError(
            f"{path}: [[forbidden]] #{index + 1} needs source and target module lists"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise boxes.HarnessError(
            f"{path}: [[forbidden]] #{index + 1} needs a reason: a rule nobody explained is "
            "the first one everybody ignores"
        )
    return Forbidden(name.strip(), tuple(sources), tuple(targets), reason.strip())


def _module_list(value: object) -> list[str] | None:
    """A non-empty list of module names, or ``None`` when it is anything else."""
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return [item.strip() for item in value]


def _find_tool(chosen: str | None) -> Path | None:
    """The tool to run, resolved absolutely: it runs with the project as its working directory."""
    candidate = chosen or os.environ.get(TOOL_ENV) or DEFAULT_TOOL
    found = shutil.which(candidate)
    return Path(found).resolve() if found else None


def _version(executable: Path) -> str:
    try:
        done = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (done.stdout or done.stderr).strip().splitlines()
    return text[0] if text and done.returncode == 0 else ""


def _environment(project: Path) -> dict[str, str]:
    """The tool imports the project's packages: put them where Python can find them."""
    env = dict(os.environ)
    parts = [str(project / "src"), str(project)]
    existing = env.get("PYTHONPATH")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _write_record(
    box_root: Path,
    project: Path,
    model: Model,
    result: Result,
    stdout: str,
    stderr: str,
    when: datetime,
) -> None:
    if stdout:
        (result.directory / "lint.stdout.txt").write_text(stdout, encoding="utf-8")
    if stderr:
        (result.directory / "lint.stderr.txt").write_text(stderr, encoding="utf-8")
    record = {
        "schema_version": SCHEMA_VERSION,
        "status": result.status,
        "at": when.isoformat().replace("+00:00", "Z"),
        "project": str(project),
        "model": str(model.path),
        "model_digest": f"sha256:{model.digest}",
        "tool": result.tool,
        "tool_version": result.version,
        "kept": result.kept,
        "broken": result.broken,
        "notes": list(result.notes),
    }
    (result.directory / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    _append_event(box_root, result, when)


def _append_event(box_root: Path, result: Result, when: datetime) -> None:
    ledger = box_root / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / f"{when:%Y-%m-%d}.jsonl"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kind = {"ok": "arch.checked", "broken": "arch.violated"}.get(result.status, "arch.unchecked")
    event = {
        "id": f"e-{when:%Y-%m-%d}-{len(existing) + 1}",
        "type": kind,
        "at": when.isoformat().replace("+00:00", "Z"),
        "actor": "aurelius",
        "reason": f"{result.kept} kept, {result.broken} broken"
        if result.status in {"ok", "broken"}
        else result.status,
        "links": [str(result.directory.relative_to(box_root))],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
