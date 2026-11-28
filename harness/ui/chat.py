"""Two conversations with the box's own agent: the orchestrator's and the architect's.

The threads are the box's own volume (``castra/<box>/ui/chat/``), like every other record of
work on the project: a question asked a week ago is a file the page reads back. Each answer
names the tokens it took, and its price when the box declares rates.

The two roles differ by the session they use, not by which brain answers. Both are the agent
that already lives in the box (``agent.py``): a real Hermes session with its own memory, skills
and tools. The orchestrator works on the tasks the box has; the architect works on the frame,
the milestones and the shape of what comes next. Nothing here sends a prompt to a model of the
page's own — the page is a client, not a second agent.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import box as boxes
from .. import executor
from . import agent

ROLES = ("orchestrator", "architect")
MAX_MESSAGES = 80
MAX_QUESTION = 4000
CHAT_DIR = Path("ui") / "chat"
PRICE_IN_ENV = "PANTHEON_MODEL_PRICE_IN"
PRICE_OUT_ENV = "PANTHEON_MODEL_PRICE_OUT"


def read(box: boxes.Box, role: str) -> list[dict[str, Any]]:
    """The thread as it stands on disk, oldest first."""
    if role not in ROLES:
        raise boxes.HarnessError(f"unknown role {role!r}; the two are {', '.join(ROLES)}")
    path = box.root / CHAT_DIR / f"{role}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise boxes.HarnessError(f"cannot read the {role} thread {path}: {error}") from error
    if not isinstance(data, list):
        raise boxes.HarnessError(f"the {role} thread {path} is not a list of messages")
    return [item for item in data if isinstance(item, dict)]


def send(box: boxes.Box, role: str, text: str) -> dict[str, Any]:
    """One turn with the agent, recorded: what was asked, what came back, what it cost."""
    body = text.strip()
    if not body:
        raise boxes.HarnessError("the question is empty")
    if len(body) > MAX_QUESTION:
        raise boxes.HarnessError(f"the question is longer than {MAX_QUESTION} characters")
    history = read(box, role)
    asked = {"at": _now(), "role": "user", "text": body}
    answer = agent.ask(box, role, body)
    prompt = answer.tokens.get("input")
    completion = answer.tokens.get("output")
    _, price_in, price_out = _rates(box)
    record: dict[str, Any] = {
        "at": _now(),
        "role": "assistant",
        "text": answer.text,
        "cents": _cost(prompt, completion, price_in, price_out),
        "tokens": {"prompt": prompt, "completion": completion},
        "model": "hermes",
        "session": answer.session,
        "seconds": answer.seconds,
    }
    history.extend([asked, record])
    _write(box, role, history[-MAX_MESSAGES:])
    return {
        "answer": answer.text,
        "cents": record["cents"],
        "tokens": record["tokens"],
        "model": "hermes",
        "session": answer.session,
        "seconds": answer.seconds,
    }


def _write(box: boxes.Box, role: str, messages: list[dict[str, Any]]) -> None:
    path = box.root / CHAT_DIR / f"{role}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=1), encoding="utf-8")


def _rates(box: boxes.Box) -> tuple[str, float | None, float | None]:
    """The box's declared rates: the executor's model, then the environment. Never invented."""
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    model = os.environ.get("PANTHEON_MODEL") or config.model or "hermes"
    price_in: float | None = None
    price_out: float | None = None
    for choice in config.models.values():
        if choice.model == model and choice.price_in is not None and choice.price_out is not None:
            price_in, price_out = choice.price_in, choice.price_out
            break
    if price_in is None:
        price_in = _rate(PRICE_IN_ENV)
    if price_out is None:
        price_out = _rate(PRICE_OUT_ENV)
    return model, price_in, price_out


def _rate(name: str) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _cost(
    prompt: int | None, completion: int | None, price_in: float | None, price_out: float | None
) -> float | None:
    if prompt is None or completion is None or price_in is None or price_out is None:
        return None
    dollars = (prompt * price_in + completion * price_out) / 1_000_000
    return round(dollars * 100, 4)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
