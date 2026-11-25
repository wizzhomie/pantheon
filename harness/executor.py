"""The executor: the declared command that proposes a change, and what it cost.

The box's agent is not built — the container runs a sleep, and ``status`` says so — so a
change comes from a command the box declares in ``persona/executor.toml``. The command gets
the task as JSON on stdin, may read the project, and answers with one unified diff and what
it spent. Everything after that is unchanged: the diff goes through Caesar's gate like any
other patch, and the record keeps the answer, not a promise about it.

Four rules make the answer usable:

* **A budget is a limit, not a wish.** ``max_seconds`` stops the command; an answer that
  claims more cents than the budget allows is recorded and not applied.
* **Usage is reported, never assumed.** A command that says nothing about tokens produces a
  record that says "unknown", because zero is a number and "nobody counted" is not.
* **The command proposes; it does not write.** The project is read before and after the call:
  a direct change is caught and the dispatch returns instead of closing on an unguarded tree.
* **A cut answer is not a change.** An answer that hit the token cap is recorded with its
  price and not applied: a diff that stops at the cap can still apply cleanly and pass the
  checks while half the fix is missing, so it is refused as incomplete rather than trusted.

A box without ``persona/executor.toml`` has no executor. Then a task must carry its own patch,
and the record says that too — the same honesty as before this module existed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import box as boxes

SCHEMA_VERSION = 1
PROFILE_FILE = Path("persona") / "executor.toml"
EXAMPLE_FILE = Path("persona") / "executor.toml.example"
CONFIG_KEYS = frozenset(
    {"schema_version", "command", "model", "models", "max_seconds", "max_cents"}
)
USAGE_KEYS = frozenset({"prompt_tokens", "completion_tokens", "cost_cents"})
ANSWER_KEYS = frozenset(
    {
        "patch",
        "patch_file",
        "summary",
        "model",
        "usage",
        "notes",
        "duration_ms",
        "truncated",
        "error",
        "reading",
    }
)
READING_KEYS = frozenset(
    {"files", "chars", "tokens_estimate", "cap_chars", "declared_chars", "left_out"}
)
"""What one call read, as the executor reports it: a count, a size, and what was left out."""
REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "task",
        "project",
        "box",
        "limits",
        "record",
        "attempt",
        "model",
        "context",
        "previous",
    }
)
DEFAULT_SECONDS = 300
PATCH_LIMIT = 5 * 1024 * 1024
"""A patch bigger than this is refused instead of read into memory and half-recorded."""

EXAMPLE = """\
# To give this box an executor, copy this file to `persona/executor.toml`.
#
# The command gets the task as JSON on stdin, may read the project, and answers with one
# unified diff plus what it spent. The diff still goes through the write gate; the command
# proposes, it does not write. The protocol is in docs/10-executor.md.
#
# schema_version = 1
# command = "/absolute/path/to/pantheon/bin/pantheon-executor"
# model = "deepseek-flash"
# max_seconds = 300
# max_cents = 5
#
# A kind of task may ask for another model: the table is the box's decision, and the
# record says which entry was taken. A kind without an entry uses `model`.
# The rate card is per entry, in dollars per million tokens: the box declares what a
# model costs, so the bill of a model chosen here is not priced by another model's rate.
# [models]
# docs = { model = "deepseek-chat", price_in = 0.15, price_out = 0.60 }
# security = { model = "deepseek-reasoner" }
"""


class TaskView(Protocol):
    """The part of a task the executor needs.

    Declared here so the executor does not import Augustus: a contour that imported the
    orchestrator would turn the architecture inside out, and the model check caught exactly
    that when this module first named ``augustus.Task``.
    """

    @property
    def id(self) -> str: ...

    @property
    def goal(self) -> str | None: ...

    @property
    def kind(self) -> str | None: ...

    @property
    def paths(self) -> tuple[str, ...]: ...

    @property
    def expected(self) -> str | None: ...

    @property
    def self_check(self) -> tuple[str, ...]: ...

    @property
    def forbidden(self) -> tuple[str, ...]: ...

    @property
    def budget(self) -> dict[str, int]: ...

    @property
    def role(self) -> str: ...


class _Malformed(Exception):
    """The command answered, but not in the declared shape: a record, not a traceback."""

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One model the box may ask for, with the rate the box declares for it."""

    model: str
    price_in: float | None = None
    price_out: float | None = None


@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    """What the box declared: the command, its model name, and the limits it may spend."""

    path: Path
    command: tuple[str, ...] | None = None
    model: str | None = None
    max_seconds: int = DEFAULT_SECONDS
    max_cents: float | None = None
    models: dict[str, ModelChoice] = field(default_factory=dict)
    """Which model a kind of task asks for: the box's decision, not the model's own."""

    @property
    def declared(self) -> bool:
        return self.command is not None


@dataclass(frozen=True, slots=True)
class Usage:
    """What the command said it spent; ``None`` means "not reported", never "free"."""

    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_cents: float | None
    seconds: float

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class Proposal:
    """The answer of one executor call: the patch, its price, and what the harness decided."""

    status: str
    reason: str
    number: int
    directory: Path
    patch: Path | None = None
    usage: Usage | None = None
    model: str | None = None
    notes: tuple[str, ...] = ()
    reading: dict[str, object] | None = None
    model_choice: str | None = None
    """Why this model: a declared kind entry or the profile's default, as the box decided."""

    @property
    def usable(self) -> bool:
        return self.status == "ok" and self.patch is not None

    @property
    def line(self) -> str:
        """The proposal as a person reads it, in one line."""
        if self.usage is None:
            text = self.status
        else:
            usage = self.usage
            money = (
                "cost not reported" if usage.cost_cents is None else f"{usage.cost_cents:.2f} cents"
            )
            text = (
                f"{self.status} — {usage.model or 'model not named'}, "
                f"{tokens_line(usage)}, {money}, {usage.seconds:.1f} s"
            )
        if self.reading is not None:
            files = _int(self.reading.get("files"))
            chars = _int(self.reading.get("chars"))
            if files is not None and chars is not None:
                text += f"; read {files} file(s), {chars} chars"
                left = self.reading.get("left_out")
                if isinstance(left, list) and left:
                    text += f", {len(left)} gap(s) named"
        if self.reason:
            text += f"; {self.reason}"
        return text

    def data(self) -> dict[str, object]:
        """The ledger's structured copy: the tokens and money a reader can add up."""
        usage = self.usage
        return {
            "status": self.status,
            "model": self.model,
            "prompt_tokens": None if usage is None else usage.prompt_tokens,
            "completion_tokens": None if usage is None else usage.completion_tokens,
            "cost_cents": None if usage is None else usage.cost_cents,
            "seconds": None if usage is None else usage.seconds,
            "proposal": f"proposal-{self.number}",
            "reading": self.reading,
            "model_choice": self.model_choice,
        }


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def tokens_line(usage: Usage) -> str:
    """Tokens as reported, or the fact that nobody counted."""
    if usage.prompt_tokens is None or usage.completion_tokens is None:
        return "tokens not reported"
    return f"{usage.prompt_tokens} in / {usage.completion_tokens} out tokens"


def proposal_json(proposal: Proposal) -> dict[str, object]:
    """The proposal for the dispatch record: the same facts as ``proposal.json``."""
    usage = proposal.usage
    return {
        "status": proposal.status,
        "reason": proposal.reason,
        "proposal": proposal.number,
        "directory": str(proposal.directory),
        "model": proposal.model,
        "usage": None
        if usage is None
        else {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cost_cents": usage.cost_cents,
            "seconds": usage.seconds,
        },
        "patch": None if proposal.patch is None else str(proposal.patch),
        "notes": list(proposal.notes),
        "reading": proposal.reading,
        "model_choice": proposal.model_choice,
    }


def read_config(path: Path) -> ExecutorConfig:
    """The box's executor profile; absent means the box declares no executor."""
    if not path.is_file():
        return ExecutorConfig(path=path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"executor profile {path} does not parse: {error}") from error
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    raw_command = data.get("command")
    if not isinstance(raw_command, str) or not raw_command.strip():
        raise boxes.HarnessError(
            f"{path}: command is required: a profile without a command declares no executor"
        )
    command = tuple(shlex.split(raw_command))
    if not command:
        raise boxes.HarnessError(f"{path}: command {raw_command!r} is empty")
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise boxes.HarnessError(f"{path}: model must be a non-empty string")
    models = _models(path, data.get("models"))
    seconds = data.get("max_seconds", DEFAULT_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise boxes.HarnessError(f"{path}: max_seconds must be a positive whole number")
    cents_raw = data.get("max_cents")
    cents: float | None = None
    if cents_raw is not None:
        if isinstance(cents_raw, bool) or not isinstance(cents_raw, (int, float)):
            raise boxes.HarnessError(f"{path}: max_cents must be a positive number")
        if cents_raw <= 0:
            raise boxes.HarnessError(f"{path}: max_cents must be a positive number")
        cents = float(cents_raw)
    return ExecutorConfig(
        path=path,
        command=command,
        model=model.strip() if isinstance(model, str) else None,
        models=models,
        max_seconds=seconds,
        max_cents=cents,
    )


def _models(path: Path, raw: object) -> dict[str, ModelChoice]:
    """The kind table: every entry names a model, and its rate is the box's declaration too."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise boxes.HarnessError(
            f'{path}: models must be a table of kind = {{ model = "..." }} entries'
        )
    table: dict[str, ModelChoice] = {}
    for kind, entry in raw.items():
        if not isinstance(kind, str) or not kind.strip():
            raise boxes.HarnessError(f"{path}: models has a key that is not a kind name")
        name = entry.get("model") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise boxes.HarnessError(
                f"{path}: models.{kind} must be a table with a non-empty model"
            )
        extra = set(entry) - {"model", "price_in", "price_out"}
        if extra:
            raise boxes.HarnessError(
                f"{path}: models.{kind} has unknown key(s) {', '.join(sorted(extra))}"
            )
        price_in = _rate(path, kind, entry.get("price_in"), "price_in")
        price_out = _rate(path, kind, entry.get("price_out"), "price_out")
        if (price_in is None) != (price_out is None):
            raise boxes.HarnessError(
                f"{path}: models.{kind} declares one half of a rate: name both price_in and "
                "price_out, or neither"
            )
        table[kind.strip()] = ModelChoice(name.strip(), price_in, price_out)
    return table


def _rate(path: Path, kind: str, raw: object, name: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise boxes.HarnessError(
            f"{path}: models.{kind}.{name} must be a non-negative number of dollars per million"
        )
    return float(raw)


def model_for(config: ExecutorConfig, kind: str | None) -> tuple[ModelChoice | None, str | None]:
    """The model this task asks for, and the reason the box chose it.

    The table decides by the task's declared kind; a kind without an entry (or a task that
    declared none) uses the profile's model. The reason travels with the record: a choice
    nobody can read afterwards is indistinguishable from the default.
    """
    default = ModelChoice(config.model) if config.model is not None else None
    if kind is not None and kind in config.models:
        return config.models[kind], f"declared for kind {kind}"
    if not config.models:
        return default, None
    if kind is None:
        return default, "the profile's model: the task declared no kind"
    return default, f"the profile's model: no entry for kind {kind}"


def _environment(choice: ModelChoice | None) -> dict[str, str] | None:
    """The call's environment, with the entry's rate when the box declared one.

    The adapter prices the call from ``PANTHEON_MODEL_PRICE_IN/OUT``; a model chosen by the
    table must not be priced by the run's default rate. ``None`` keeps the inherited
    environment, which is what a box with no declared rate expects.
    """
    if choice is None or choice.price_in is None or choice.price_out is None:
        return None
    env = dict(os.environ)
    env["PANTHEON_MODEL_PRICE_IN"] = f"{choice.price_in:g}"
    env["PANTHEON_MODEL_PRICE_OUT"] = f"{choice.price_out:g}"
    return env


def limits(config: ExecutorConfig, budget: dict[str, int]) -> tuple[int, float | None]:
    """The box's cap is a ceiling: a task may ask for less, never for more."""
    seconds = config.max_seconds
    cents = config.max_cents
    if "max_seconds" in budget:
        seconds = min(seconds, budget["max_seconds"])
    if "max_cents" in budget:
        asked = float(budget["max_cents"])
        cents = asked if cents is None else min(cents, asked)
    return seconds, cents


def describe(config: ExecutorConfig) -> str:
    """One line for ``status``: what would run, or the fact that nothing would."""
    if config.command is None:
        return "not declared — a task must carry its own patch"
    bits: list[str] = []
    if config.model is not None:
        bits.append(f"model {config.model}")
    if config.models:
        per_kind = ", ".join(
            f"{kind} → {choice.model}"
            + (
                ""
                if choice.price_in is None
                else f" ({choice.price_in:g}/{choice.price_out:g} per M)"
            )
            for kind, choice in sorted(config.models.items())
        )
        bits.append(f"by kind: {per_kind}")
    bits.append(f"max {config.max_seconds} s")
    if config.max_cents is not None:
        bits.append(f"{config.max_cents:g} cents")
    return f"{' '.join(config.command)} ({', '.join(bits)})"


def propose(
    config: ExecutorConfig,
    task: TaskView,
    box: boxes.Box,
    when: datetime | None = None,
    context: dict[str, object] | None = None,
    previous: dict[str, object] | None = None,
) -> Proposal:
    """Ask the declared command for a change, and record the answer with its price."""
    if config.command is None:
        raise boxes.HarnessError("the box declares no executor: there is nothing to ask")
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    number = _next_number(box.root, task.id)
    directory = box.root / "tabularium" / task.id / f"proposal-{number}"
    directory.mkdir(parents=True, exist_ok=True)
    seconds, cents = limits(config, task.budget)
    read_chars = task.budget.get("max_read_chars")
    chosen, reason = model_for(config, task.kind)
    model = chosen.model if chosen is not None else None
    request = _request(
        task, box, directory, number, model, seconds, cents, context, previous, read_chars
    )
    (directory / "request.json").write_text(
        json.dumps(request, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    before = _tree(box.project)
    started = time.monotonic()
    try:
        done = subprocess.run(
            list(config.command),
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            check=False,
            cwd=str(box.project),
            timeout=float(seconds),
            env=_environment(chosen),
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        _write_call(directory, "", "")
        return _record(
            directory,
            task,
            number,
            "failed",
            f"the executor did not answer within {seconds} s: the attempt was stopped",
            None,
            Usage(None, None, None, None, round(elapsed, 1)),
            model,
            stamp,
            [],
            choice=reason,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _write_call(directory, "", "")
        return _record(
            directory,
            task,
            number,
            "failed",
            f"the executor could not be started: {error}",
            None,
            None,
            model,
            stamp,
            [],
            choice=reason,
        )
    elapsed = time.monotonic() - started
    _write_call(directory, done.stdout, done.stderr)
    try:
        patch, usage, answer_model, answer_notes, reading, truncated = _read_answer(
            done, directory, elapsed, model
        )
    except _Malformed as error:
        return _record(
            directory,
            task,
            number,
            "failed",
            str(error),
            None,
            error.usage,
            model,
            stamp,
            [],
            choice=reason,
        )
    notes = list(answer_notes)
    if model is not None and reason is not None:
        notes.append(f"model {model} chosen: {reason}")
    if before is None:
        notes.append(
            "the project is not a git repository: a direct write by the executor could not "
            "have been noticed"
        )
    changed = _changed(before, _tree(box.project))
    if changed:
        return _record(
            directory,
            task,
            number,
            "unapproved-write",
            "the executor changed the project directly, without the gate: "
            + ", ".join(changed[:5]),
            patch,
            usage,
            answer_model,
            stamp,
            notes,
            choice=reason,
        )
    if truncated:
        return _record(
            directory,
            task,
            number,
            "incomplete",
            "the answer was cut at the token cap: the diff is incomplete, so it was recorded "
            "and not applied — raise the answer cap or narrow the task",
            patch,
            usage,
            answer_model,
            stamp,
            notes,
            reading=reading,
            choice=reason,
        )
    if cents is not None and usage is not None and usage.cost_cents is not None:
        if usage.cost_cents > cents:
            return _record(
                directory,
                task,
                number,
                "over-budget",
                f"the executor spent {usage.cost_cents:.2f} cents of the {cents:g} allowed: "
                "the change was recorded but not applied",
                patch,
                usage,
                answer_model,
                stamp,
                notes,
                choice=reason,
            )
    elif usage is not None and usage.cost_cents is None and cents is not None:
        notes.append(
            "the executor reported no cost: the budget cannot be checked, and the record "
            "does not pretend it was met"
        )
    if cents is None:
        notes.append("no cent limit is declared: the cost of this call is recorded, not bounded")
    return _record(
        directory,
        task,
        number,
        "ok",
        "",
        patch,
        usage,
        answer_model,
        stamp,
        notes,
        reading=reading,
        choice=reason,
    )


def previous_lines(previous: object) -> list[str]:
    """The last attempt's refusal as lines for the prompt: a retry that cannot see why is blind.

    The words are facts from the box's own record — the verdict, the failed checks, the findings —
    never an instruction invented here. Both executors print them under their own heading.
    """
    if not isinstance(previous, dict):
        return []
    lines: list[str] = []
    verdict = previous.get("verdict")
    attempts = previous.get("attempts")
    if isinstance(verdict, str) and verdict:
        lines.append(f"- the previous attempt returned {verdict} (attempt {attempts})")
    reason = previous.get("reason")
    if isinstance(reason, str) and reason:
        lines.append(f"- what the box said: {reason}")
    checks = previous.get("checks")
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, str) and item:
                lines.append(f"- failed check: {item}")
    findings = previous.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, str) and item:
                lines.append(f"- finding: {item}")
    return lines


def _request(
    task: TaskView,
    box: boxes.Box,
    directory: Path,
    number: int,
    model: str | None,
    seconds: int,
    cents: float | None,
    context: dict[str, object] | None = None,
    previous: dict[str, object] | None = None,
    read_chars: int | None = None,
) -> dict[str, object]:
    """The task as the command receives it: declared facts, paths, the limits, and context."""
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt": number,
        "task": {
            "id": task.id,
            "goal": task.goal,
            "kind": task.kind,
            "role": task.role,
            "paths": list(task.paths),
            "expected": task.expected,
            "self_check": list(task.self_check),
            "forbidden": list(task.forbidden),
            "budget": dict(task.budget),
        },
        "project": {"path": str(box.project)},
        "box": {"name": box.name, "root": str(box.root)},
        "record": {"directory": str(directory)},
        "model": model,
        "context": context,
        "previous": previous,
        "limits": {
            "max_seconds": seconds,
            "max_cents": cents,
            "max_read_chars": read_chars,
        },
    }


def _read_answer(
    done: subprocess.CompletedProcess[str],
    directory: Path,
    elapsed: float,
    model: str | None,
) -> tuple[Path, Usage, str | None, tuple[str, ...], dict[str, object] | None, bool]:
    """Parse the answer strictly: an unknown key is a protocol defect, not something to ignore."""
    text = done.stdout.strip()
    if not text:
        raise _Malformed(
            f"the executor printed nothing (exit {done.returncode}): an empty answer is not "
            "a change"
        )
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise _Malformed(f"the executor's answer is not JSON: {error}") from error
    if not isinstance(raw, dict):
        raise _Malformed("the executor's answer is not a JSON object")
    data: dict[str, object] = {str(key): value for key, value in raw.items()}
    unknown = set(data) - ANSWER_KEYS
    if unknown:
        raise _Malformed(f"the executor's answer has unknown key(s) {', '.join(sorted(unknown))}")
    reported = data.get("model")
    if reported is not None and not isinstance(reported, str):
        raise _Malformed("the executor's answer: model must be a string")
    answer_model = reported if isinstance(reported, str) else model
    if "error" in data:
        usage = _usage(data.get("usage"), elapsed, answer_model)
        raise _Malformed(f"the executor failed: {data['error']}", usage=usage)
    if done.returncode != 0:
        detail = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise _Malformed(f"the executor exited {done.returncode}: {detail}")
    patch = _patch_file(data, directory)
    usage = _usage(data.get("usage"), elapsed, answer_model)
    notes = _notes(data.get("notes"))
    reading = _reading(data.get("reading"))
    truncated = data.get("truncated", False)
    if not isinstance(truncated, bool):
        raise _Malformed("the executor's answer: truncated must be true or false")
    return patch, usage, answer_model, notes, reading, truncated


def _reading(raw: object) -> dict[str, object] | None:
    """What the call read, if the executor says: counts are checked, sizes stay honest."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _Malformed("the executor's reading must be a table")
    table: dict[str, object] = {str(key): value for key, value in raw.items()}
    unknown = set(table) - READING_KEYS
    if unknown:
        raise _Malformed(f"the executor's reading has unknown key(s) {', '.join(sorted(unknown))}")
    for name in ("files", "chars", "tokens_estimate"):
        value = _count(table.get(name), f"reading.{name}")
        if value is None:
            raise _Malformed(f"the executor's reading.{name} is required")
        table[name] = value
    for name in ("cap_chars", "declared_chars"):
        raw_value = table.get(name)
        if raw_value is not None:
            table[name] = _count(raw_value, f"reading.{name}")
    left = table.get("left_out", [])
    if not isinstance(left, list) or not all(isinstance(item, str) for item in left):
        raise _Malformed("the executor's reading.left_out must be a list of strings")
    table["left_out"] = list(left)
    return table


def _patch_file(data: dict[str, object], directory: Path) -> Path:
    """Exactly one of ``patch`` (text) or ``patch_file`` (a path) is the change."""
    inline = data.get("patch")
    external = data.get("patch_file")
    if (inline is None) == (external is None):
        raise _Malformed("the executor must answer with exactly one of patch or patch_file")
    if isinstance(inline, str):
        text = inline
        source = "inline"
    elif isinstance(external, str) and external.strip():
        path = Path(external).expanduser()
        if not path.is_absolute():
            # Relative to the record directory the command was told about, not to the project:
            # a patch file inside the project would be an unapproved write by definition.
            path = (directory / path).resolve()
        if not path.is_file():
            raise _Malformed(f"the executor named a patch file that does not exist: {path}")
        if path.stat().st_size > PATCH_LIMIT:
            raise _Malformed(f"the patch at {path} is larger than {PATCH_LIMIT} bytes")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise _Malformed(f"cannot read the patch at {path}: {error}") from error
        source = str(path)
    else:
        raise _Malformed("the executor's patch must be text, or patch_file must be a path")
    if not text.strip():
        raise _Malformed("the executor returned an empty patch")
    target = directory / "change.diff"
    target.write_text(text, encoding="utf-8")
    if source != "inline":
        (directory / "patch-source.txt").write_text(source + "\n", encoding="utf-8")
    return target


def _usage(raw: object, elapsed: float, model: str | None) -> Usage:
    """Tokens and money as reported; a missing field stays ``None``."""
    if raw is None:
        return Usage(model, None, None, None, round(elapsed, 1))
    if not isinstance(raw, dict):
        raise _Malformed("the executor's usage must be a table")
    table: dict[str, object] = {str(key): value for key, value in raw.items()}
    unknown = set(table) - USAGE_KEYS
    if unknown:
        raise _Malformed(f"the executor's usage has unknown key(s) {', '.join(sorted(unknown))}")
    return Usage(
        model=model,
        prompt_tokens=_count(table.get("prompt_tokens"), "prompt_tokens"),
        completion_tokens=_count(table.get("completion_tokens"), "completion_tokens"),
        cost_cents=_money(table.get("cost_cents")),
        seconds=round(elapsed, 1),
    )


def _count(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _Malformed(f"the executor's usage.{name} must be a non-negative whole number")
    return value


def _money(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _Malformed("the executor's usage.cost_cents must be a non-negative number")
    return float(value)


def _notes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise _Malformed("the executor's notes must be a list of strings")
    return tuple(raw)


def _write_call(directory: Path, stdout: str, stderr: str) -> None:
    (directory / "stdout.txt").write_text(stdout, encoding="utf-8")
    (directory / "stderr.txt").write_text(stderr, encoding="utf-8")


def _record(
    directory: Path,
    task: TaskView,
    number: int,
    status: str,
    reason: str,
    patch: Path | None,
    usage: Usage | None,
    model: str | None,
    when: datetime,
    notes: list[str],
    *,
    reading: dict[str, object] | None = None,
    choice: str | None = None,
) -> Proposal:
    """Every outcome gets a record: a failed call is part of the task's history too."""
    digest = hashlib.sha256(patch.read_bytes()).hexdigest() if patch is not None else None
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "task": task.id,
        "proposal": number,
        "status": status,
        "reason": reason,
        "at": _stamp(when),
        "model": model,
        "model_choice": choice,
        "usage": None
        if usage is None
        else {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cost_cents": usage.cost_cents,
            "seconds": usage.seconds,
        },
        "patch": None if digest is None else {"file": "change.diff", "digest": f"sha256:{digest}"},
        "notes": notes,
        "reading": reading,
    }
    (directory / "proposal.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return Proposal(
        status=status,
        reason=reason,
        number=number,
        directory=directory,
        patch=patch,
        usage=usage,
        model=model,
        notes=tuple(notes),
        reading=reading,
        model_choice=choice,
    )


def _next_number(box_root: Path, task: str) -> int:
    base = box_root / "tabularium" / task
    if not base.is_dir():
        return 1
    numbers = [
        int(child.name.removeprefix("proposal-"))
        for child in base.iterdir()
        if child.is_dir()
        and child.name.startswith("proposal-")
        and child.name.removeprefix("proposal-").isdigit()
    ]
    return max(numbers, default=0) + 1


def _tree(project: Path) -> tuple[str | None, str | None] | None:
    """The project's HEAD and working tree; ``None`` when git cannot tell us.

    The status is kept exactly as git prints it: its first three characters are the state and
    the separator, so stripping the output would shift every path by one character.
    """
    head = _git(project, "rev-parse", "HEAD")
    status = _git(project, "status", "--porcelain", "--untracked-files=all")
    if head is None or status is None:
        return None
    return head.strip(), status


def _changed(
    before: tuple[str | None, str | None] | None,
    after: tuple[str | None, str | None] | None,
) -> list[str]:
    """Paths that changed during the call, according to git; empty when it cannot tell."""
    if before is None or after is None:
        return []
    if before[0] != after[0]:
        return ["HEAD"]
    old = {line[3:] for line in (before[1] or "").splitlines()}
    new = {line[3:] for line in (after[1] or "").splitlines()}
    return sorted(new - old)


def _git(project: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")
