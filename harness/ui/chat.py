"""Two conversations about one box: the orchestrator's and the architect's.

The threads are the box's own volume (``castra/<box>/ui/chat/``), like every other record of
work on the project: a question asked a week ago is a file the page reads back, not a model's
memory. Each answer carries what it cost, because a conversation is paid work like a step.

The two roles differ by what they are asked to do, not by which model they use. The
orchestrator works on the tasks the box already has; the architect works on the frame, the
milestones and the shape of what comes next.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import augustus, campaign, executor, fluxus, frame, rails
from .. import box as boxes

ROLES = ("orchestrator", "architect")
MAX_MESSAGES = 80
MAX_TOKENS = 2048
TIMEOUT_SECONDS = 120.0
CHAT_DIR = Path("ui") / "chat"
URL_ENV = "PANTHEON_MODEL_URL"
DEFAULT_URL = "https://api.deepseek.com/chat/completions"
KEY_ENV = "PANTHEON_MODEL_KEY"
FALLBACK_KEY_ENV = "DEEPSEEK_API_KEY"
MODEL_ENV = "PANTHEON_MODEL"
DEFAULT_MODEL = "deepseek-chat"
PRICE_IN_ENV = "PANTHEON_MODEL_PRICE_IN"
PRICE_OUT_ENV = "PANTHEON_MODEL_PRICE_OUT"

ORCHESTRATOR = (
    "Ты оркестратор коробки Pantheon: одна коробка, один проект. Твоя работа — текущие задачи: "
    "что запущено, что ждёт, что отказано и почему, что делать следующим шагом. Отвечай только "
    "по фактам ниже; факта нет — скажи, что его нет, и назови, где он лежал бы. Ты ничего не "
    "запускаешь и не правишь: предложи шаг и назови команду, которую его сделает. Коротко, "
    "по-русски, без кода и логов."
)

ARCHITECT = (
    "Ты архитектор коробки Pantheon: одна коробка, один проект. Твоя работа — рамка, план "
    "(вехи, метрики, принципы), аудит, внедрение нового и архитектурные развороты. Отвечай "
    "только по фактам ниже; предложение не выдавай за принятое и называй цену решения. Ты "
    "ничего не правишь: предложи изменение и назови, какой отметкой оно принимается. Коротко, "
    "по-русски, без кода и логов."
)

PROMPTS = {"orchestrator": ORCHESTRATOR, "architect": ARCHITECT}


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
    """One question and one answer, both recorded, the answer's price named."""
    body = text.strip()
    if not body:
        raise boxes.HarnessError("the question is empty")
    if len(body) > 4000:
        raise boxes.HarnessError("the question is longer than 4000 characters")
    history = read(box, role)
    asked = {"at": _now(), "role": "user", "text": body}
    answer = _ask(box, role, [*history, asked])
    history.extend(
        [
            asked,
            {
                "at": _now(),
                "role": "assistant",
                "text": answer["answer"],
                "cents": answer["cents"],
                "tokens": answer["tokens"],
                "model": answer["model"],
            },
        ]
    )
    _write(box, role, history[-MAX_MESSAGES:])
    return answer


def _write(box: boxes.Box, role: str, messages: list[dict[str, Any]]) -> None:
    path = box.root / CHAT_DIR / f"{role}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=1), encoding="utf-8")


def _ask(box: boxes.Box, role: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    key = _key(box)
    if key is None:
        raise boxes.HarnessError(
            "no model key: put PANTHEON_MODEL_KEY in the environment, or copy the key into "
            f"{box.root / 'cella' / '.env'}"
        )
    model, price_in, price_out = _rates(box)
    url = os.environ.get(URL_ENV) or DEFAULT_URL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPTS[role] + "\n\n" + _context(box)},
            *[
                {"role": item.get("role", "user"), "content": item.get("text", "")}
                for item in messages
            ],
        ],
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise boxes.HarnessError(f"the endpoint answered {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise boxes.HarnessError(f"the endpoint is unreachable: {error}") from error
    except json.JSONDecodeError as error:
        raise boxes.HarnessError(
            f"the endpoint answered something that is not JSON: {error}"
        ) from error
    choices = answer.get("choices")
    if not isinstance(choices, list) or not choices:
        raise boxes.HarnessError("the endpoint answered without choices")
    first: dict[str, Any] = choices[0] if isinstance(choices[0], dict) else {}
    raw_message = first.get("message")
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise boxes.HarnessError("the endpoint answered without text")
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    return {
        "answer": text.strip(),
        "cents": _cost(prompt, completion, price_in, price_out),
        "tokens": {"prompt": prompt, "completion": completion},
        "model": model,
    }


def _key(box: boxes.Box) -> str | None:
    """The key from the environment, else from the agent's own home; never printed, never logged."""
    from_env = os.environ.get(KEY_ENV) or os.environ.get(FALLBACK_KEY_ENV)
    if from_env:
        return from_env
    path = box.root / "cella" / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith(f"{FALLBACK_KEY_ENV}="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None


def _rates(box: boxes.Box) -> tuple[str, float | None, float | None]:
    config = executor.read_config(box.root / executor.PROFILE_FILE)
    model = os.environ.get(MODEL_ENV) or config.model or DEFAULT_MODEL
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


def _count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _context(box: boxes.Box) -> str:
    """What the box says about itself, short enough to stand in a system prompt."""
    lines: list[str] = [
        f"коробка: {box.name}",
        f"проект: {box.project}",
        f"рамка: {frame.standing(box).line()}",
        f"план: {rails.standing(box).line()}",
    ]
    declared = rails.read(box.project)
    if declared is not None:
        for milestone in declared.milestones:
            lines.append(
                f"веха {milestone.id} «{milestone.title}» ({milestone.state}): "
                f"закрывается, когда {milestone.closes_when}; метрика {milestone.metric}"
            )
    table = augustus.read_routes(None)
    for path in campaign.campaign_files(box.project):
        try:
            report = campaign.report(box, campaign.read_campaign(path), routes=table)
        except boxes.HarnessError as error:
            lines.append(f"план {path.stem}: не читается — {error}")
            continue
        steps = ", ".join(
            f"{step.id}={report.state.steps[step.id].state}"
            if report.state is not None and step.id in report.state.steps
            else f"{step.id}=не начат"
            for step in report.campaign.steps
        )
        lines.append(
            f"план {report.campaign.id}: {report.verdict.status} "
            f"({report.verdict.closed} из {report.verdict.total}); шаги: {steps}"
        )
        for reason in report.problems:
            lines.append(f"план {report.campaign.id} не стартует: {reason}")
    recent = augustus.recent_tasks(box.root, limit=5)
    for task in recent:
        verify = task.verify or "not verified"
        lines.append(f"задача {task.id}: {task.state}, {verify}")
    for entry in fluxus.feed(box)[-5:]:
        reason = f": {entry.reason}" if entry.reason else ""
        lines.append(f"событие {entry.at} {entry.kind}{reason}")
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
