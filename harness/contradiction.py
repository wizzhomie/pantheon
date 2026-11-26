"""The new contract against the archive: a change of decision goes to the owner, not to the gate.

The archive remembers why each closed change was made. A new task that touches the same paths may
be an ordinary repair — or it may quietly reverse a decision that was taken deliberately. Text
cannot tell the two apart, so the box asks a reader and makes it name the contradiction with a
citation: the address in the archive and the old clause it conflicts with. Code then checks that
the citation exists; a claim that does not point at an archived clause counts as nothing.

The reader is declared, never assumed: ``persona/validator.toml`` with ``enabled = true``. With
the file absent or switched off, the box does not guess and does not go silent — the record says
the contracts were not compared, so the gap is visible instead of looking like a pass.

What is escalated is a decision, not a verdict: the task is refused before the executor is paid,
and the owner is the one who decides whether the old clause still stands (ADR-045).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import archivum
from . import box as boxes

SCHEMA_VERSION = 1
PROFILE_FILE = Path("persona") / "validator.toml"
CONFIG_KEYS = frozenset({"schema_version", "enabled", "model", "max_cents", "command", "seconds"})
REPO_COMMAND = Path(__file__).resolve().parent.parent / "bin" / "pantheon-review"
"""The box's agent, asked a different question: the same command the reader of a change uses."""
DEFAULT_SECONDS = 240
MAX_HISTORY = 6
"""How many earlier decisions are put in front of the reader: a map, not the whole archive."""

EXAMPLE_FILE = Path("persona") / "validator.toml.example"
"""Where `pantheon new` puts the example: turning the comparison on is one deliberate line."""

EXAMPLE = """\
# To compare every new contract with the decisions the archive keeps on the same paths, copy
# this file to `persona/validator.toml` and set `enabled = true`.
#
# This is one paid call to the box's own agent, made before the executor is asked for anything.
# Only the paths are compared: a task that touches no archived change costs nothing. When the
# reader names a contradiction, the task is refused and the owner decides — a reversed decision
# is not a repair.
#
# schema_version = 1
# enabled = true
# model = "deepseek-flash"
# max_cents = 1
# seconds = 240
"""

LABELS = ("ожидаемое", "цель", "symptom", "visible", "boundaries", "expected", "goal")
"""The words the history is rendered with; a reader that quotes them is quoting our formatting."""

SYSTEM = (
    "Ты — валидатор контрактов. Тебе дают новый контракт задачи и решения, уже принятые в этих "
    "путях. Твоя работа — найти только ЯВНОЕ противоречие: новый контракт требует обратного "
    "тому, что старый контракт объявил обязательным. Обычная правка, которая просто трогает те "
    "же файлы, противоречием не является, и выдумывать его нельзя. Отвечай одним JSON-объектом: "
    '{"contradiction": null} или {"contradiction": {"address": "<адрес из списка>", "old": '
    '"<пункт старого контракта, дословно>", "new": "<пункт нового, дословно>", "why": "<одна '
    'фраза>"}}. Адрес и старый пункт бери только из списка; ничего не добавляй.'
)


@dataclass(frozen=True, slots=True)
class Config:
    """What the box declared about comparing contracts: whether, which model, how much."""

    path: Path
    enabled: bool = False
    model: str | None = None
    max_cents: float | None = None
    command: str | None = None
    seconds: int = DEFAULT_SECONDS

    @property
    def declared(self) -> bool:
        return self.path.is_file()


@dataclass(frozen=True, slots=True)
class Citation:
    """A contradiction the reader named: the address, the two clauses, one phrase why."""

    address: str
    old: str
    new: str
    why: str


@dataclass(frozen=True, slots=True)
class Comparison:
    """What the archive had on these paths, what the reader said, and what it cost."""

    history: tuple[archivum.Entry, ...]
    citation: Citation | None
    checked: bool
    skipped: str | None
    cost_cents: float | None = None
    error: str | None = None
    answer: str = ""
    """What the reader said, kept when its citation did not hold: a gap must be explainable."""

    @property
    def escalated(self) -> bool:
        return self.citation is not None


Answer = Callable[[str], tuple[str, float | None]]
"""One question to the reader: the prompt in, the answer and the bill out."""


def describe(config: Config) -> str:
    """The comparison as a person reads it in `new` and `status`."""
    if not config.declared:
        return "no profile: contracts are not compared with the archive"
    if not config.enabled:
        return "declared and off: contracts are not compared with the archive"
    money = "" if config.max_cents is None else f", up to {config.max_cents:g} cents"
    return f"on: every contract is compared with the archive on its paths{money}"


def default_command() -> str:
    """The reader this repository ships: the box's agent, asked in the reader's own protocol."""
    return str(REPO_COMMAND)


def read_config(path: Path) -> Config:
    """The box's validator profile; absent means the box does not compare contracts."""
    if not path.is_file():
        return Config(path=path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise boxes.HarnessError(f"validator profile {path} does not parse: {error}") from error
    unknown = set(data) - CONFIG_KEYS
    if unknown:
        raise boxes.HarnessError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise boxes.HarnessError(
            f"{path}: schema_version {data.get('schema_version')!r} is not {SCHEMA_VERSION}"
        )
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise boxes.HarnessError(f"{path}: enabled must be true or false")
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise boxes.HarnessError(f"{path}: model must be a non-empty string")
    cents = data.get("max_cents")
    if cents is not None:
        if isinstance(cents, bool) or not isinstance(cents, (int, float)) or cents <= 0:
            raise boxes.HarnessError(f"{path}: max_cents must be a positive number")
        cents = float(cents)
    command = data.get("command")
    if command is not None and (not isinstance(command, str) or not command.strip()):
        raise boxes.HarnessError(f"{path}: command must be a non-empty string")
    seconds = data.get("seconds", DEFAULT_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise boxes.HarnessError(f"{path}: seconds must be a positive whole number")
    return Config(
        path=path,
        enabled=enabled,
        model=model,
        max_cents=cents,
        command=command.strip() if isinstance(command, str) else None,
        seconds=seconds,
    )


def compare(
    box_root: Path,
    paths: tuple[str, ...],
    prompt_for: Callable[[tuple[archivum.Entry, ...]], str],
    *,
    ask: Answer | None,
) -> Comparison:
    """The archived decisions on these paths, and what the reader says about them.

    The history is read first and is the only source of addresses and clauses: a reader that
    answers with something the archive does not hold is refused, not believed.
    """
    history = archivum.for_paths(box_root, paths)[:MAX_HISTORY]
    if not history:
        return Comparison(
            history=(),
            citation=None,
            checked=False,
            skipped="no closed change in the archive touches these paths",
        )
    if ask is None:
        return Comparison(
            history=history,
            citation=None,
            checked=False,
            skipped="no validator is declared: the contracts were not compared",
        )
    prompt = prompt_for(history)
    try:
        text, cost = ask(prompt)
    except boxes.HarnessError as error:
        return Comparison(
            history=history,
            citation=None,
            checked=True,
            skipped=None,
            error=str(error),
        )
    citation, problem = _citation(text, history)
    return Comparison(
        history=history,
        citation=citation,
        checked=True,
        skipped=None,
        cost_cents=cost,
        error=problem,
        answer="" if citation is not None else " ".join(text.split())[:400],
    )


def reason(comparison: Comparison) -> str | None:
    """The sentence that stops the task, or ``None`` when nothing was contradicted."""
    citation = comparison.citation
    if citation is None:
        return None
    return (
        f"the new contract contradicts the decision kept at {citation.address}: it says "
        f"«{citation.old}», the task asks for «{citation.new}» ({citation.why}). This is a change "
        "of decision, not a repair: the owner decides whether the old clause still stands, and "
        "nothing is paid until then"
    )


def payload(comparison: Comparison) -> dict[str, object]:
    """The comparison as a machine reads it: what the reader was given, said, and cost."""
    citation = comparison.citation
    return {
        "history": [item.address for item in comparison.history],
        "checked": comparison.checked,
        "skipped": comparison.skipped,
        "cost_cents": comparison.cost_cents,
        "error": comparison.error,
        "answer": comparison.answer,
        "citation": None
        if citation is None
        else {
            "address": citation.address,
            "old": citation.old,
            "new": citation.new,
            "why": citation.why,
        },
    }


def note(comparison: Comparison) -> str:
    """What the record says when no contradiction was found, including why nobody looked."""
    if comparison.citation is not None:
        assert reason(comparison) is not None
        return str(reason(comparison))
    if comparison.skipped:
        addresses = ", ".join(item.address for item in comparison.history)
        return f"the contract was not compared with the archive ({comparison.skipped}: {addresses})"
    if comparison.error:
        addresses = ", ".join(item.address for item in comparison.history)
        told = f" The reader said: {comparison.answer}" if comparison.answer else ""
        return (
            f"the comparison with {addresses} produced nothing usable: {comparison.error}. The "
            f"task goes on, and the gap is named here.{told}"
        )
    addresses = ", ".join(item.address for item in comparison.history)
    money = "" if comparison.cost_cents is None else f", {comparison.cost_cents:g} cents"
    return (
        f"the contract was compared with the archive ({addresses}): no contradiction named{money}"
    )


def prompt(
    goal: str, expected: str, contract: dict[str, str], history: tuple[archivum.Entry, ...]
) -> str:
    """The question: the new contract, then the decisions it might reverse, with their addresses."""
    lines = ["НОВЫЙ КОНТРАКТ", f"цель: {goal or 'не объявлена'}"]
    if expected:
        lines.append(f"ожидаемое: {expected}")
    for key in ("symptom", "visible", "boundaries", "missing"):
        value = contract.get(key)
        if value:
            lines.append(f"{key}: {value}")
    lines += ["", "РЕШЕНИЯ, УЖЕ ПРИНЯТЫЕ В ЭТИХ ПУТЯХ"]
    for entry in history:
        lines.append(f"- {entry.address}  {entry.at}  {entry.subject or entry.goal}")
        if entry.expected:
            lines.append(f"  ожидаемое: {entry.expected}")
        for name, clause in (entry.contract or {}).items():
            if clause:
                lines.append(f"  {name}: {clause}")
    return "\n".join(lines)


def command_ask(
    command: str, *, box_root: Path, model: str | None = None, seconds: int = DEFAULT_SECONDS
) -> Answer:
    """The box's agent as the reader: one question, one answer, one bill.

    The command shape is the reader's, not a new protocol: trajan already talks to the box's agent
    this way, so the validator asks the same command a different question.
    """
    argv = tuple(shlex.split(command))

    def ask(text: str) -> tuple[str, float | None]:
        request = {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": text},
            ],
            "box": {"root": str(box_root)},
        }
        env = dict(os.environ)
        if model:
            env["PANTHEON_REVIEW_MODEL"] = model
        env["PANTHEON_REVIEW_BOX"] = str(box_root)
        env["PANTHEON_REVIEW_SECONDS"] = str(seconds)
        try:
            done = subprocess.run(
                argv,
                input=json.dumps(request),
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=float(seconds) + 30,
            )
        except subprocess.TimeoutExpired as error:
            raise boxes.HarnessError(f"the validator did not answer in {seconds} s") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise boxes.HarnessError(f"the validator could not be asked: {error}") from error
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip().splitlines()
            raise boxes.HarnessError(
                "the validator failed: " + ("; ".join(detail[-3:]) or "no message")
            )
        try:
            answer = json.loads(done.stdout)
        except json.JSONDecodeError as error:
            raise boxes.HarnessError(f"the validator did not answer with JSON: {error}") from error
        if not isinstance(answer, dict) or not isinstance(answer.get("text"), str):
            raise boxes.HarnessError("the validator answered without text")
        raw_cost = answer.get("cost_cents")
        cost = float(raw_cost) if isinstance(raw_cost, (int, float)) else None
        return str(answer["text"]), cost

    return ask


def _citation(text: str, history: tuple[archivum.Entry, ...]) -> tuple[Citation | None, str | None]:
    """The reader's citation, checked against the history: the archive is what makes it evidence."""
    data = _first_object(text)
    if data is None:
        return None, "the answer is not one JSON object"
    raw = data.get("contradiction")
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "the contradiction is not an object"
    address = str(raw.get("address") or "").strip()
    old = _unlabel(str(raw.get("old") or "").strip())
    new = str(raw.get("new") or "").strip()
    if not address or not old or not new:
        return None, "the contradiction names no address or no clause"
    entry = next((item for item in history if item.address == address), None)
    if entry is None:
        return None, f"the answer cites {address}, which is not in the history it was given"
    if _squash(old) not in _clause(entry):
        return None, f"the clause cited from {address} is not there"
    return Citation(address=address, old=old, new=new, why=str(raw.get("why") or "").strip()), None


def _unlabel(clause: str) -> str:
    """The reader copies the line as it was shown, label and all; the label is ours, not the clause.

    A live run found this: the history is rendered as ``ожидаемое: <clause>``, the reader quoted
    the whole line, and the check said the clause was not in the archive — while it was, one
    label away. The label is stripped before the comparison, and what is compared stays verbatim.
    """
    lowered = clause.casefold()
    for label in LABELS:
        head = f"{label.casefold()}:"
        if lowered.startswith(head):
            clause = clause[len(head) :].strip()
            break
    for tail in ("…", "..."):
        if clause.endswith(tail):
            clause = clause[: -len(tail)].strip()
            break
    return clause


def _first_object(text: str) -> dict[str, object] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return {str(key): value for key, value in data.items()} if isinstance(data, dict) else None


def _clause(entry: archivum.Entry) -> str:
    parts = [entry.expected, entry.subject, entry.goal]
    parts += [str(value) for value in (entry.contract or {}).values() if value]
    return _squash(" ".join(parts))


def _squash(text: str) -> str:
    """Compare as words, not as whitespace: a citation is about the clause, not the line breaks."""
    return " ".join(text.split()).casefold()
