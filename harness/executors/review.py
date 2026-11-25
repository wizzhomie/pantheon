"""The reviewer that is the box's own agent: it reads the change instead of writing one.

Same container, same model, same bill as the executor — a different question. The harness
hands this command to trajan (``--reviewer trajan.llm.command:from_env``); trajan sends the
system prompt and the rendered diff as one JSON object on stdin and then checks every finding
it gets against the repository. Nothing here anchors an answer: an unanchored finding is not
evidence, and the decision belongs to trajan.

Two rules this module keeps:

* the project is mounted read-only, so the agent cannot "fix" what it was asked to judge;
* what the call cost is written down next to the box (``cella/.cache/review-usage.jsonl``)
  and in the answer. An agent that reports no bill leaves a line with ``null``, not a zero:
  a review whose price nobody counted is not a free review.

The answer shape is trajan's ``LLMResponse``: text, model, tokens, finish reason. ``text`` is
what the agent said, verbatim — the reviewer's parser is the one that decides whether it is
an answer or a failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness import box as boxes
from harness import container
from harness.executors.container import HERMES, SERVICE, _usage

BOX_ENV = "PANTHEON_REVIEW_BOX"
"""Where the box is: a command carries no arguments it was not given, so the path is an env."""

SECONDS_ENV = "PANTHEON_REVIEW_SECONDS"
LOG_ENV = "PANTHEON_REVIEW_LOG"
"""Where to write the bill. The caller puts it in the run directory, so cost follows the run."""
DEFAULT_SECONDS = 240
"""One call. Longer than the executor's default, because a review reads a whole diff."""

DEFAULT_MODEL = "deepseek-flash"
PLACEHOLDER_MODEL = "reviewer"
"""What trajan puts in the request when no model is chosen; it is not a model name."""

USAGE_PATH = "/opt/data/.cache/review-usage.json"
"""Where the agent writes its own bill inside the container, as the executor already does."""

USAGE_IN_BOX = Path("cella") / ".cache" / "review-usage.json"
LOG_IN_BOX = Path("cella") / ".cache" / "review-usage.jsonl"
"""The last call's bill, and the running list of them: the harness reads the pair."""

RULES = (
    "Файлы не создавай и не правь: проект смонтирован только на чтение. "
    "Ответ — один JSON-объект, без пояснений и заборов."
)


def main() -> int:
    try:
        raw: object = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as error:
        return _fail(f"the request is not JSON: {error}")
    if not isinstance(raw, dict):
        return _fail("the request is not a JSON object")
    request = {str(key): value for key, value in raw.items()}
    root = _box_root()
    if root is None:
        return _fail(f"{BOX_ENV} is not set: the reviewer needs the box it reads for")
    try:
        box = boxes.read_box(root / "castrum.toml")
    except (OSError, boxes.HarnessError) as error:
        return _fail(f"the box at {root} cannot be read: {error}")
    if not box.compose.is_file():
        return _fail(f"the box {box.name} has no compose file: `pantheon up` starts the agent")
    model = _model(request)
    seconds = _seconds()
    command = [
        *container.docker_prefix(),
        "compose",
        "-p",
        container.PROJECT_PREFIX + box.name,
        "-f",
        str(box.compose),
        "exec",
        "-T",
        "-u",
        str(os.getuid()),
        SERVICE,
        HERMES,
        "-z",
        _prompt(request),
        "-m",
        model,
        "--provider",
        container.AGENT_PROVIDER,
        "--usage-file",
        USAGE_PATH,
    ]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=float(seconds)
        )
    except subprocess.TimeoutExpired:
        return _fail(f"the agent did not answer within {seconds} s")
    except (OSError, subprocess.SubprocessError) as error:
        return _fail(f"the container could not be asked: {error}")
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        return _fail(
            "the agent in the container failed: " + ("; ".join(detail[-3:]) or "no message")
        )
    text = done.stdout.strip()
    if not text:
        return _fail("the agent answered nothing", notes=[done.stderr.strip()[:400]])
    bill = _usage(box.root / USAGE_IN_BOX)
    log = Path(os.environ[LOG_ENV]) if os.environ.get(LOG_ENV) else box.root / LOG_IN_BOX
    log.parent.mkdir(parents=True, exist_ok=True)
    _record(log, model, bill)
    answer: dict[str, object] = {
        "text": text,
        "model": model,
        "prompt_tokens": _count(bill, "prompt_tokens"),
        "completion_tokens": _count(bill, "completion_tokens"),
        "finish_reason": "stop",
    }
    if bill is not None and bill.get("cost_cents") is not None:
        answer["cost_cents"] = bill["cost_cents"]
    print(json.dumps(answer))
    return 0


def _prompt(request: dict[str, Any]) -> str:
    """The review question as one prompt: the system message, the diff, and the rules.

    ``hermes -z`` takes one string. The roles are kept as headings rather than merged: the
    model must see which half is doctrine and which half is the change.
    """
    system: list[str] = []
    user: list[str] = []
    messages = request.get("messages")
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        (system if message.get("role") == "system" else user).append(content.strip())
    parts = []
    if system:
        parts.append("SYSTEM:\n" + "\n\n".join(system))
    if user:
        parts.append("REQUEST:\n" + "\n\n".join(user))
    parts.append("RULES:\n" + RULES)
    return "\n\n".join(parts)


def _model(request: dict[str, Any]) -> str:
    """The model trajan asked for, unless it only asked by its placeholder."""
    chosen = request.get("model")
    if isinstance(chosen, str) and chosen.strip() and chosen != PLACEHOLDER_MODEL:
        return chosen.strip()
    return os.environ.get("PANTHEON_REVIEW_MODEL") or DEFAULT_MODEL


def _seconds() -> int:
    raw = os.environ.get(SECONDS_ENV)
    try:
        return int(raw) if raw else DEFAULT_SECONDS
    except ValueError:
        return DEFAULT_SECONDS


def _box_root() -> Path | None:
    raw = os.environ.get(BOX_ENV)
    return Path(raw).expanduser() if raw else None


def _count(bill: dict[str, object] | None, key: str) -> int:
    value = bill.get(key) if bill is not None else None
    return value if isinstance(value, int) and value >= 0 else 0


def _record(path: Path, model: str, bill: dict[str, object] | None) -> None:
    """Append one line per call: the harness sums these into the run's record."""
    entry = {
        "model": model,
        "prompt_tokens": _count(bill, "prompt_tokens") or None,
        "completion_tokens": _count(bill, "completion_tokens") or None,
        "cost_cents": bill.get("cost_cents") if bill is not None else None,
    }
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fail(message: str, notes: list[str] | None = None) -> int:
    payload: dict[str, object] = {"error": message}
    if notes:
        payload["notes"] = notes
    print(json.dumps(payload))
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the shim
    sys.exit(main())
