"""The executor: the declared command that proposes a change, and what it cost.

The box's agent is not built — the container runs a sleep, and ``status`` says so — so a
change comes from a command the box declares in ``persona/executor.toml``. The command gets
the task as JSON on stdin, may read the project, and answers with one unified diff and what
it spent. Everything after that is unchanged: the diff goes through Caesar's gate like any
other patch, and the record keeps the answer, not a promise about it.

Four rules make the answer usable:

* **Money is the budget; the room follows it.** The token room of a call is computed from the
  task's remaining cents and the model's output rate, capped by the provider's ceiling. The
  shape of the change says what it may touch, never how much a call may generate.
* **Usage is reported, never assumed.** A command that says nothing about tokens produces a
  record that says "unknown", because zero is a number and "nobody counted" is not; a call
  that never left the box is recorded as not billed, not charged at the ceiling.
* **The command proposes; it does not write.** The project is read before and after the call:
  a direct change is caught and the dispatch returns instead of closing on an unguarded tree.
* **A cut answer is not a change.** An answer that spent the whole room on thinking is
  recorded with its finish reason, its thinking and its price, and not applied. A cut means
  the task's money bought that much room; a larger room is a budget decision, not a retry the
  box can pay for out of the same budget.

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
    {
        "schema_version",
        "command",
        "engine",
        "engines",
        "model",
        "models",
        "max_seconds",
        "max_cents",
        "max_output_tokens",
        "max_steps",
        "shapes",
    }
)
ENGINES = ("one-shot", "steps")
"""How a task is run: one answer, or a loop of answers that sees what the last one did.

The command is the same in both: it gets the task, may read the project, and answers with one
diff. The difference is the controller: ``one-shot`` asks once and lives with the answer,
``steps`` asks again after applying the answer and running the visible check, and hands the
model the facts of the previous steps. The engine is the box's decision, declared per kind,
because how much rope a kind of work gets is a property of the work, not of the command.
"""
SHAPES = ("patch", "file", "multi")
"""The shape of a change a task asks for: it sizes the answer, and it is declared, not guessed.

A task that adds a few lines, one that rewrites a file and one that touches several files need
different rooms to answer in; one cap for all three is either waste or a cut-off. The box
declares what each shape may spend, the task names its shape, and a change that does not fit
the declared shape is an estimate to correct, not a wasted attempt.
"""
USAGE_KEYS = frozenset(
    {"prompt_tokens", "completion_tokens", "reasoning_tokens", "answer_tokens", "cost_cents"}
)
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
        "done",
        "finish_reason",
        "billing_status",
    }
)
BILLING = ("billed", "not_billed", "unknown")
"""What the box knows about the money of a call: paid, certainly not paid, or not knowable."""
DEFAULT_OUTPUT_TOKENS = 32768
"""The provider's output ceiling when neither the box nor the model table declares one.

32 768 is the largest room the pilot endpoint accepted (measured), so it is a ceiling the box
can actually get, not a number the provider would refuse. It is a safety bound, not a plan:
the room of a call is computed from the money the task has left.
"""
MIN_OUTPUT_TOKENS = 1024
"""Below this, a call has no room to say anything: the box stops instead of asking into a void."""
RESERVE_STEPS = 0.25
"""The share of the remaining money kept for the next step and the next prompt."""
RESERVE_ONE_SHOT = 0.10
"""A one-shot has no next step, so it keeps only enough for the prompt and the closing."""
RESERVE_FLOOR_CENTS = 0.5
"""Even a tiny budget keeps this much, because the prompt itself is paid before the answer."""
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
        "turn",
        "turns",
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
# max_output_tokens = 32768
#
# A kind of task may ask for another model: the table is the box's decision, and the
# record says which entry was taken. A kind without an entry uses `model`.
# The rate card is per entry, in dollars per million tokens: the box needs the output rate
# to turn the task's money into a room, and the output ceiling so the room never asks the
# provider for more than it serves. A model without a rate gets the provider's ceiling as its
# room, and the record says the cost could not be bounded before the call.
# [models]
# docs = { model = "deepseek-chat", price_in = 0.15, price_out = 0.60, max_output_tokens = 32768 }
# security = { model = "deepseek-reasoner" }
#
# A shape declares only the scope of the change: how many files and changed lines the answer
# may touch. The room of the call is computed from the task's budget, not from the shape.
# [shapes.patch]
# max_changed_lines = 40
# max_files = 1
#
# How a kind of task is run: `one-shot` asks once; `steps` asks again after applying the
# answer and running the visible check, up to `max_steps` calls. The engine is the box's
# decision, like the model, and the record names it.
# engine = "one-shot"
# [engines]
# bugfix = "steps"
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
    def shape(self) -> str | None: ...

    @property
    def forbidden(self) -> tuple[str, ...]: ...

    @property
    def budget(self) -> dict[str, int]: ...

    @property
    def max_steps(self) -> int | None: ...

    @property
    def role(self) -> str: ...


class _Malformed(Exception):
    """The command answered, but not in the declared shape: a record, not a traceback."""

    def __init__(
        self,
        message: str,
        usage: Usage | None = None,
        *,
        finish_reason: str | None = None,
        billing: str | None = "unknown",
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.finish_reason = finish_reason
        self.billing = billing


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One model the box may ask for, with the rate and the output ceiling the box declares."""

    model: str
    price_in: float | None = None
    price_out: float | None = None
    max_output_tokens: int | None = None
    """The provider's own output ceiling for this model; the room never exceeds it."""


@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    """What the box declared: the command, its model name, and the limits it may spend."""

    path: Path
    command: tuple[str, ...] | None = None
    model: str | None = None
    max_seconds: int = DEFAULT_SECONDS
    max_cents: float | None = None
    max_output_tokens: int | None = None
    """The default provider output ceiling; a model entry may declare its own for its kind."""
    max_steps: int | None = None
    """How many calls a task may make in this box: the ceiling a task can only lower."""
    engine: str = "one-shot"
    """The engine a kind without its own entry gets: one answer, or a loop of answers."""
    engines: dict[str, str] = field(default_factory=dict)
    """Which engine a kind of task runs under: the box's decision, like the model table."""
    models: dict[str, ModelChoice] = field(default_factory=dict)
    """Which model a kind of task asks for: the box's decision, not the model's own."""

    shapes: dict[str, Shape] = field(default_factory=dict)
    """What each shape of change may spend, when the box declares shapes."""

    @property
    def declared(self) -> bool:
        return self.command is not None


@dataclass(frozen=True, slots=True)
class Shape:
    """The scope of one shape of change: how many files and lines it may touch, nothing more.

    The room of an answer is not a property of its shape: a one-line fix can need more thinking
    than a mechanical rewrite, and the endpoint counts thinking in the same tokens as the answer.
    The shape says what the change may touch; the task's money says how much room the call gets.
    """

    name: str
    max_changed_lines: int | None = None
    max_files: int | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    """What the command said it spent; ``None`` means "not reported", never "free"."""

    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_cents: float | None
    seconds: float
    reasoning_tokens: int | None = None
    """How much of ``completion_tokens`` the model spent thinking, when the provider says."""
    answer_tokens: int | None = None
    """The completion minus the thinking, when both are reported; never a guess."""

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
    done: bool = False
    """The executor said the work is finished, with no further change to apply."""
    engine: str | None = None
    """Which engine the box ran for this call: the declared table's decision, not the model's."""
    engine_choice: str | None = None
    """Why this engine, as the box decided it: a kind entry or the profile's default."""
    finish_reason: str | None = None
    """Why the provider stopped: ``stop``, ``length``, or whatever it said."""
    billing_status: str | None = None
    """``billed``, ``not_billed`` or ``unknown``: the money fact the budget is built on."""
    max_tokens: int | None = None
    """The room this call was given, so a cut can be read against what it was allowed."""
    truncated: bool = False
    """The answer was cut at the room; a cut answer is recorded, never applied."""

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
        if self.done:
            text += "; the work is declared done"
        return text

    def data(self) -> dict[str, object]:
        """The ledger's structured copy: the tokens and money a reader can add up."""
        usage = self.usage
        return {
            "status": self.status,
            "model": self.model,
            "prompt_tokens": None if usage is None else usage.prompt_tokens,
            "completion_tokens": None if usage is None else usage.completion_tokens,
            "reasoning_tokens": None if usage is None else usage.reasoning_tokens,
            "answer_tokens": None if usage is None else usage.answer_tokens,
            "cost_cents": None if usage is None else usage.cost_cents,
            "seconds": None if usage is None else usage.seconds,
            "proposal": f"proposal-{self.number}",
            "reading": self.reading,
            "model_choice": self.model_choice,
            "engine": self.engine,
            "engine_choice": self.engine_choice,
            "done": self.done,
            "finish_reason": self.finish_reason,
            "billing_status": self.billing_status,
            "max_tokens": self.max_tokens,
            "truncated": self.truncated,
        }


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def tokens_line(usage: Usage) -> str:
    """Tokens as reported, or the fact that nobody counted."""
    if usage.prompt_tokens is None or usage.completion_tokens is None:
        return "tokens not reported"
    text = f"{usage.prompt_tokens} in / {usage.completion_tokens} out tokens"
    if usage.reasoning_tokens is not None:
        text += f" ({usage.reasoning_tokens} thinking"
        if usage.answer_tokens is not None:
            text += f", {usage.answer_tokens} answer"
        text += ")"
    return text


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
            "reasoning_tokens": usage.reasoning_tokens,
            "answer_tokens": usage.answer_tokens,
            "cost_cents": usage.cost_cents,
            "seconds": usage.seconds,
        },
        "patch": None if proposal.patch is None else str(proposal.patch),
        "notes": list(proposal.notes),
        "reading": proposal.reading,
        "model_choice": proposal.model_choice,
        "engine": proposal.engine,
        "engine_choice": proposal.engine_choice,
        "done": proposal.done,
        "finish_reason": proposal.finish_reason,
        "billing_status": proposal.billing_status,
        "max_tokens": proposal.max_tokens,
        "truncated": proposal.truncated,
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
    shapes = _shapes(path, data.get("shapes"))
    engines = _engines(path, data.get("engines"))
    engine = data.get("engine", "one-shot")
    if not isinstance(engine, str) or engine not in ENGINES:
        raise boxes.HarnessError(
            f"{path}: engine must be one of {', '.join(ENGINES)}, not {engine!r}"
        )
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
    steps_raw = data.get("max_steps")
    steps: int | None = None
    if steps_raw is not None:
        if isinstance(steps_raw, bool) or not isinstance(steps_raw, int) or steps_raw <= 0:
            raise boxes.HarnessError(f"{path}: max_steps must be a positive whole number")
        steps = steps_raw
    output_raw = data.get("max_output_tokens")
    output: int | None = None
    if output_raw is not None:
        if isinstance(output_raw, bool) or not isinstance(output_raw, int) or output_raw <= 0:
            raise boxes.HarnessError(f"{path}: max_output_tokens must be a positive whole number")
        output = output_raw
    return ExecutorConfig(
        path=path,
        command=command,
        model=model.strip() if isinstance(model, str) else None,
        models=models,
        shapes=shapes,
        engine=engine,
        engines=engines,
        max_seconds=seconds,
        max_cents=cents,
        max_output_tokens=output,
        max_steps=steps,
    )


def _engines(path: Path, raw: object) -> dict[str, str]:
    """The engine table: kind → one-shot|steps, checked here so a typo costs nothing later."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise boxes.HarnessError(
            f'{path}: engines must be a table of kind = "one-shot"|"steps" entries'
        )
    table: dict[str, str] = {}
    for kind, engine in raw.items():
        if not isinstance(kind, str) or not kind.strip():
            raise boxes.HarnessError(f"{path}: engines has a key that is not a kind name")
        if not isinstance(engine, str) or engine not in ENGINES:
            raise boxes.HarnessError(
                f"{path}: engines.{kind} must be one of {', '.join(ENGINES)}, not {engine!r}"
            )
        table[kind.strip()] = engine
    return table


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
        extra = set(entry) - {"model", "price_in", "price_out", "max_output_tokens"}
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
        output = entry.get("max_output_tokens")
        if output is not None and (
            isinstance(output, bool) or not isinstance(output, int) or output <= 0
        ):
            raise boxes.HarnessError(
                f"{path}: models.{kind}.max_output_tokens must be a positive whole number"
            )
        table[kind.strip()] = ModelChoice(name.strip(), price_in, price_out, output)
    return table


def _shapes(path: Path, raw: object) -> dict[str, Shape]:
    """The shape table: each shape declares the scope of its change, never the room of a call.

    An unknown shape is a refusal rather than an entry: a task naming a shape nobody defined
    would otherwise look like a task with no declared scope. A shape may declare no size limits
    at all: then it names the kind of change and leaves the size to the gate.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise boxes.HarnessError(
            f"{path}: shapes must be a table of shape = {{ max_changed_lines = N, max_files = N }}"
        )
    table: dict[str, Shape] = {}
    for name, entry in raw.items():
        if name not in SHAPES:
            raise boxes.HarnessError(
                f"{path}: shape {name!r} is not one of {', '.join(SHAPES)}"
            )
        if not isinstance(entry, dict):
            raise boxes.HarnessError(f"{path}: shapes.{name} must be a table")
        extra = set(entry) - {"max_changed_lines", "max_files"}
        if extra:
            raise boxes.HarnessError(
                f"{path}: shapes.{name} has unknown key(s) {', '.join(sorted(extra))}"
            )
        lines = _size_limit(path, name, entry.get("max_changed_lines"), "max_changed_lines")
        files = _size_limit(path, name, entry.get("max_files"), "max_files")
        table[name] = Shape(name, lines, files)
    return table


def _size_limit(path: Path, shape: str, raw: object, name: str) -> int | None:
    """A limit on the answer's size, when the box declares one: whole, positive, or absent."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise boxes.HarnessError(
            f"{path}: shapes.{shape}.{name} must be a positive whole number"
        )
    return raw


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


def engine_for(config: ExecutorConfig, kind: str | None) -> tuple[str, str]:
    """The engine this task runs under, and the reason the box chose it.

    Like the model table: a kind with an entry takes it, a kind without uses the profile's
    engine, and the reason travels into the record. The engine is never inferred from the
    task's size or from its ``max_steps``: the task says how much work it wants to pay for,
    the box says how much rope that kind of work gets.
    """
    if kind is not None and kind in config.engines:
        return config.engines[kind], f"{config.engines[kind]}, declared for kind {kind}"
    if kind is None:
        return config.engine, f"{config.engine}, the profile's engine; the task declared no kind"
    if config.engines:
        return config.engine, f"{config.engine}, the profile's engine; no entry for kind {kind}"
    return config.engine, f"{config.engine}, the profile's engine"


def step_ceiling(task: TaskView, config: ExecutorConfig) -> int | None:
    """How many calls this task may make: the lower of the task's ceiling and the box's."""
    declared = [value for value in (task.max_steps, config.max_steps) if value is not None]
    return min(declared) if declared else None


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


def shape_for(config: ExecutorConfig, shape: str | None) -> tuple[Shape | None, str]:
    """The scope this task's change is held to, and the reason it is that scope.

    The shape no longer sizes the answer's room: it says what the change may touch. The room is
    computed from the task's money, because a room is a resource and the shape is a contract.
    """
    if shape is None:
        return None, "the task declared no shape: the change's size is its own declaration"
    found = config.shapes.get(shape)
    if found is None:
        return None, (
            f"the task declares shape {shape!r} and the box declares no limits for it: "
            f"declare [shapes.{shape}] in persona/executor.toml, or the answer is sized by "
            "guesswork"
        )
    return found, f"declared shape {shape}: {_scope_words(found)}"


def _scope_words(shape: Shape) -> str:
    """What a shape allows the change to touch, in words a record can keep."""
    bits: list[str] = []
    if shape.max_changed_lines is not None:
        bits.append(f"at most {shape.max_changed_lines} changed line(s)")
    if shape.max_files is not None:
        bits.append(f"at most {shape.max_files} file(s)")
    return ", ".join(bits) if bits else "no size limit declared"


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


def _prices(choice: ModelChoice | None) -> tuple[float | None, float | None]:
    """The rate of the chosen model: the table's, else the run's own declaration."""
    if choice is not None and choice.price_in is not None and choice.price_out is not None:
        return choice.price_in, choice.price_out
    raw_in = os.environ.get("PANTHEON_MODEL_PRICE_IN")
    raw_out = os.environ.get("PANTHEON_MODEL_PRICE_OUT")
    try:
        price_in = float(raw_in) if raw_in else None
        price_out = float(raw_out) if raw_out else None
    except ValueError:
        return None, None
    if price_in is None or price_out is None or price_in < 0 or price_out < 0:
        return None, None
    return price_in, price_out


def output_ceiling(config: ExecutorConfig, choice: ModelChoice | None) -> int:
    """The provider's own output ceiling: the model's, the box's, or the pilot's known maximum."""
    if choice is not None and choice.max_output_tokens is not None:
        return choice.max_output_tokens
    if config.max_output_tokens is not None:
        return config.max_output_tokens
    return DEFAULT_OUTPUT_TOKENS


def _last_prompt_tokens(directory: Path) -> int | None:
    """The prompt size of the last call on this task, the best evidence the next prompt's cost."""
    base = directory.parent
    found: int | None = None
    for path in sorted(base.glob("proposal-*/proposal.json"), key=lambda item: item.name):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        usage = data.get("usage")
        tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
            found = tokens
    return found


def room_for(
    config: ExecutorConfig,
    budget_cents: float | None,
    chosen: ModelChoice | None,
    *,
    steps: bool,
    directory: Path,
) -> tuple[int | None, str]:
    """How many tokens this call may generate, from the money the task has left.

    The money is the budget; the room is its translation at the model's output rate. The input
    prompt is paid from a reserve, the next step keeps its share, and the provider's ceiling is
    a hard border no budget can cross. A room too small to say anything is a refusal, not a
    call into the void.
    """
    ceiling = output_ceiling(config, chosen)
    price_in, price_out = _prices(chosen)
    if budget_cents is None:
        return ceiling, (
            f"no money budget is declared: the room is the provider's ceiling of {ceiling} "
            "tokens, so the call's cost is not bounded before it is made"
        )
    if price_out is None:
        return ceiling, (
            f"no price is declared for the model: the room is the provider's ceiling of "
            f"{ceiling} tokens, so the call's cost is not bounded before it is made"
        )
    prompt = _last_prompt_tokens(directory)
    input_cost = (
        0.0 if prompt is None or price_in is None else prompt * price_in / 10_000
    )
    share = RESERVE_STEPS if steps else RESERVE_ONE_SHOT
    reserve = max(share * budget_cents, input_cost + 0.2, RESERVE_FLOOR_CENTS)
    generation = budget_cents - reserve
    if generation <= 0:
        return None, (
            f"the task has {budget_cents:.2f} cents left, and the prompt and the reserve need "
            f"{reserve:.2f}: there is no room for a useful answer"
        )
    room = min(ceiling, int(generation * 10_000 / price_out))
    if room < MIN_OUTPUT_TOKENS:
        return None, (
            f"the task has {budget_cents:.2f} cents left, which buys {room} answer token(s) "
            f"after the reserve: below the {MIN_OUTPUT_TOKENS} a useful answer needs"
        )
    return room, (
        f"the room is {room} token(s): {generation:.2f} cents for the answer after a "
        f"{reserve:.2f}-cent reserve (input ~{input_cost:.2f} cents)"
    )


def describe(config: ExecutorConfig) -> str:
    """One line for ``status``: what would run, or the fact that nothing would."""
    if config.command is None:
        return "not declared — a task must carry its own patch"
    bits: list[str] = []
    if config.model is not None:
        bits.append(f"model {config.model}")
    bits.append(f"engine {config.engine}")
    if config.engines:
        per_kind = ", ".join(
            f"{kind} → {engine}" for kind, engine in sorted(config.engines.items())
        )
        bits.append(f"engines by kind: {per_kind}")
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
        bits.append(f"models by kind: {per_kind}")
    bits.append(f"max {config.max_seconds} s")
    if config.max_cents is not None:
        bits.append(f"{config.max_cents:g} cents")
    if config.max_steps is not None:
        bits.append(f"{config.max_steps} call(s)")
    return f"{' '.join(config.command)} ({', '.join(bits)})"


def propose(
    config: ExecutorConfig,
    task: TaskView,
    box: boxes.Box,
    when: datetime | None = None,
    context: dict[str, object] | None = None,
    previous: dict[str, object] | None = None,
    budget_cents: float | None = None,
    turn: int | None = None,
    turns: tuple[dict[str, object], ...] = (),
    engine: str | None = None,
    engine_choice: str | None = None,
) -> Proposal:
    """Ask the declared command for a change, and record the answer with its price.

    ``budget_cents`` is what is left of the task's total: the room of the call is computed from
    it, not from the shape of the change. The record keeps the room and the money it was given,
    so "it used 17 402 of 32 000 thinking tokens" is a fact after the run.

    ``turn`` and ``turns`` are the step's place and the facts of the steps before it. A one-shot
    call is turn 1 with nothing before it; a step gets the same facts a retry does, plus what
    the visible check said after each applied step, so it answers what happened, not what it
    imagined.
    """
    if config.command is None:
        raise boxes.HarnessError("the box declares no executor: there is nothing to ask")
    stamp = (when or datetime.now(UTC)).replace(microsecond=0)
    number = next_number(box.root, task.id)
    directory = box.root / "tabularium" / task.id / f"proposal-{number}"
    directory.mkdir(parents=True, exist_ok=True)
    seconds, cents = limits(config, task.budget)
    if budget_cents is not None:
        cents = budget_cents if cents is None else min(cents, budget_cents)
    read_chars = task.budget.get("max_read_chars")
    chosen, reason = model_for(config, task.kind)
    model = chosen.model if chosen is not None else None
    room, room_reason = room_for(
        config,
        cents,
        chosen,
        steps=engine == "steps",
        directory=directory,
    )
    if room is None:
        return _record(
            directory,
            task,
            number,
            "insufficient-budget",
            room_reason,
            None,
            None,
            model,
            stamp,
            [],
            choice=reason,
            engine=engine,
            engine_choice=engine_choice,
            billing="not_billed",
        )
    request = _request(
        task,
        box,
        directory,
        number,
        model,
        seconds,
        cents,
        context,
        previous,
        read_chars,
        turn=turn,
        turns=turns,
        ceiling=step_ceiling(task, config),
        engine=engine,
        engine_choice=engine_choice,
        room_tokens=room,
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
            engine=engine,
            engine_choice=engine_choice,
            billing="unknown",
            max_tokens=room,
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
            engine=engine,
            engine_choice=engine_choice,
            billing="not_billed",
            max_tokens=room,
        )
    elapsed = time.monotonic() - started
    _write_call(directory, done.stdout, done.stderr)
    try:
        (
            patch,
            usage,
            answer_model,
            answer_notes,
            reading,
            truncated,
            declared_done,
            finish,
            billing,
        ) = _read_answer(done, directory, elapsed, model)
    except _Malformed as error:
        cut = error.finish_reason == "length"
        return _record(
            directory,
            task,
            number,
            "truncated" if cut else "failed",
            str(error),
            None,
            error.usage,
            model,
            stamp,
            [],
            choice=reason,
            engine=engine,
            engine_choice=engine_choice,
            finish_reason=error.finish_reason,
            billing=error.billing,
            max_tokens=room,
            truncated=cut,
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
            engine=engine,
            engine_choice=engine_choice,
            finish_reason=finish,
            billing=billing,
            max_tokens=room,
        )
    if declared_done:
        return _record(
            directory,
            task,
            number,
            "done",
            "the executor declared the change done",
            None,
            usage,
            answer_model,
            stamp,
            notes,
            reading=reading,
            choice=reason,
            engine=engine,
            engine_choice=engine_choice,
            done=True,
            finish_reason=finish,
            billing=billing,
            max_tokens=room,
        )
    if truncated:
        return _record(
            directory,
            task,
            number,
            "incomplete",
            "the answer was cut at the room: the diff is incomplete, so it was recorded and "
            "not applied — a bigger room or a narrower task is the next question",
            patch,
            usage,
            answer_model,
            stamp,
            notes,
            reading=reading,
            choice=reason,
            engine=engine,
            engine_choice=engine_choice,
            finish_reason=finish,
            billing=billing,
            max_tokens=room,
            truncated=True,
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
                engine=engine,
                engine_choice=engine_choice,
                finish_reason=finish,
                billing=billing,
                max_tokens=room,
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
        engine=engine,
        engine_choice=engine_choice,
        finish_reason=finish,
        billing=billing,
        max_tokens=room,
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


def turn_lines(
    turn: object, turns: object, ceiling: object = None, *, steps: bool = False
) -> list[str]:
    """The steps before this one as lines for the prompt: the change's own short history.

    A steps run is only worth paying for if the next answer can see what the last one did: the
    gate's verdict on it, and the words of the visible check. These are the box's facts, never
    a retelling, and a step that was refused says so — otherwise the model repeats a change the
    gate already turned down. Even the first step says it is a step: the loop will apply the
    answer and come back with what the check said, and a model that knows it can answer small
    instead of restating a file. A one-shot run with no steps before it renders nothing.
    """
    if not isinstance(turns, list):
        return []
    if not turns and not steps:
        return []
    step = isinstance(turn, int) and isinstance(ceiling, int)
    if not turns:
        return [
            f"STEP {turn} OF AT MOST {ceiling}. THIS IS A STEP, NOT A ONE-SHOT ANSWER."
            if step
            else "THIS IS A STEP, NOT A ONE-SHOT ANSWER.",
            "The box will apply your change, run the check the task shows you, and ask you again "
            "with what it said. Propose the next change against the tree as it is now; do not "
            "restate the file. If the work is finished, answer with one line: DONE.",
        ]
    heading = (
        f"STEP {turn} OF AT MOST {ceiling}. THE STEPS BEFORE THIS ONE."
        if step
        else "THE STEPS BEFORE THIS ONE."
    )
    lines = [heading, "Each line is what the box did with an answer, not what was intended:"]
    for item in turns:
        if not isinstance(item, dict):
            continue
        number = item.get("turn")
        label = f"- step {number}" if isinstance(number, int) else "- step"
        status = item.get("status")
        if status == "applied":
            check = item.get("check")
            if isinstance(check, dict) and check.get("status") == "passed":
                lines.append(f"{label}: applied; the check you can see passed after it.")
            elif isinstance(check, dict):
                tail = str(check.get("tail") or "no output")
                lines.append(f"{label}: applied; the check you can see still failed ({tail}).")
            else:
                lines.append(f"{label}: applied; the check you can see was not run.")
        elif status == "refused":
            reason = str(item.get("reason") or "no reason recorded")
            lines.append(f"{label}: the write gate refused the change ({reason}).")
        else:
            reason = str(item.get("reason") or "no reason recorded")
            lines.append(f"{label}: {status or 'no result'} ({reason}).")
    lines.append(
        "Propose the next change against the tree as it is now. If the work is finished, answer "
        "with one line: DONE."
    )
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
    *,
    turn: int | None = None,
    turns: tuple[dict[str, object], ...] = (),
    ceiling: int | None = None,
    engine: str | None = None,
    engine_choice: str | None = None,
    room_tokens: int | None = None,
) -> dict[str, object]:
    """The task as the command receives it: declared facts, paths, the limits, and context.

    ``turn`` is the call's place in the task (the one-shot call is turn 1), and ``turns`` the
    steps that came before it: what the gate did with each answer and what the visible check
    said. Both are facts from the box's records, so the model sees what happened, not a
    paraphrase of it. ``room_tokens`` is the generation room the box computed from the money
    that is left; it is a resource allowance, not a property of the change's shape.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt": number,
        "turn": number if turn is None else turn,
        "turns": list(turns),
        "task": {
            "id": task.id,
            "goal": task.goal,
            "kind": task.kind,
            "role": task.role,
            "paths": list(task.paths),
            "expected": task.expected,
            "shape": task.shape,
            "self_check": list(task.self_check),
            "forbidden": list(task.forbidden),
            "budget": dict(task.budget),
            "max_steps": task.max_steps,
        },
        "project": {"path": str(box.project)},
        "box": {"name": box.name, "root": str(box.root)},
        "record": {"directory": str(directory)},
        "model": model,
        "engine": engine,
        "engine_choice": engine_choice,
        "context": context,
        "previous": previous,
        "limits": {
            "max_seconds": seconds,
            "max_cents": cents,
            "max_read_chars": read_chars,
            "max_answer_tokens": room_tokens,
            "max_steps": ceiling,
        },
    }


def _read_answer(
    done: subprocess.CompletedProcess[str],
    directory: Path,
    elapsed: float,
    model: str | None,
) -> tuple[
    Path | None,
    Usage,
    str | None,
    tuple[str, ...],
    dict[str, object] | None,
    bool,
    bool,
    str | None,
    str | None,
]:
    """Parse the answer strictly: an unknown key is a protocol defect, not something to ignore.

    The tuple ends with the two facts the budget is built on: ``finish_reason`` (why the
    provider stopped) and ``billing_status`` (whether the call was paid, certainly not paid, or
    unknowable). A cut answer and a logical failure both carry a price, and the record must be
    able to tell them apart.
    """
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
    finish = data.get("finish_reason")
    if finish is not None and not isinstance(finish, str):
        raise _Malformed("the executor's answer: finish_reason must be a string")
    billing = data.get("billing_status", "billed")
    if billing not in BILLING:
        raise _Malformed(
            f"the executor's answer: billing_status must be one of {', '.join(BILLING)}"
        )
    if "error" in data:
        usage = _usage(data.get("usage"), elapsed, answer_model)
        raise _Malformed(
            f"the executor failed: {data['error']}",
            usage=usage,
            finish_reason=finish,
            billing=str(billing),
        )
    if done.returncode != 0:
        detail = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise _Malformed(f"the executor exited {done.returncode}: {detail}")
    declared_done = data.get("done", False)
    if not isinstance(declared_done, bool):
        raise _Malformed("the executor's answer: done must be true or false")
    if declared_done:
        if data.get("patch") is not None or data.get("patch_file") is not None:
            raise _Malformed(
                "the executor's answer says done and carries a patch: one of the two"
            )
        usage = _usage(data.get("usage"), elapsed, answer_model)
        return (
            None,
            usage,
            answer_model,
            _notes(data.get("notes")),
            _reading(data.get("reading")),
            bool(data.get("truncated", False)),
            True,
            finish,
            str(billing),
        )
    patch = _patch_file(data, directory)
    usage = _usage(data.get("usage"), elapsed, answer_model)
    notes = _notes(data.get("notes"))
    reading = _reading(data.get("reading"))
    truncated = data.get("truncated", False)
    if not isinstance(truncated, bool):
        raise _Malformed("the executor's answer: truncated must be true or false")
    return patch, usage, answer_model, notes, reading, truncated, False, finish, str(billing)


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
        reasoning_tokens=_count(table.get("reasoning_tokens"), "reasoning_tokens"),
        answer_tokens=_count(table.get("answer_tokens"), "answer_tokens"),
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
    done: bool = False,
    engine: str | None = None,
    engine_choice: str | None = None,
    finish_reason: str | None = None,
    billing: str | None = "billed",
    max_tokens: int | None = None,
    truncated: bool = False,
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
            "reasoning_tokens": usage.reasoning_tokens,
            "answer_tokens": usage.answer_tokens,
            "cost_cents": usage.cost_cents,
            "seconds": usage.seconds,
        },
        "patch": None if digest is None else {"file": "change.diff", "digest": f"sha256:{digest}"},
        "notes": notes,
        "reading": reading,
        "done": done,
        "engine": engine,
        "engine_choice": engine_choice,
        "finish_reason": finish_reason,
        "billing_status": billing,
        "max_tokens": max_tokens,
        "truncated": truncated,
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
        done=done,
        engine=engine,
        engine_choice=engine_choice,
        finish_reason=finish_reason,
        billing_status=billing,
        max_tokens=max_tokens,
        truncated=truncated,
    )


def next_number(box_root: Path, task: str) -> int:
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
