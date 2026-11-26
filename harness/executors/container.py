"""The executor that runs the box's own agent, inside the box's container.

The host adapter (``deepseek.py``) is a fallback for a box without an agent. This one uses the
thing the box actually has: the running container, its profile, its model, its budget. The
harness hands it the same request as any executor; it turns the task into one prompt, asks the
agent for a diff, and answers with the diff and what the agent itself reported spending.

Why a diff and not a change: the project is mounted read-only inside the container, so the agent
cannot write even if it wanted to. The write happens on the host, through Caesar, after the
contract check. That is the architecture, not a convention.

The agent runs as the owner of its home directory, because ``hermes`` refuses to control a home
that belongs to another uid — and the home belongs to the host user, so the box's files stay the
host's files.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from harness import container
from harness.executor import previous_lines, turn_lines
from harness.executors.deepseek import extract_diff

DEFAULT_MODEL = "deepseek-flash"
HERMES = "/opt/hermes/.venv/bin/hermes"
SERVICE = "augustus"
USAGE_PATH = "/opt/data/.cache/executor-usage.json"
"""Where the agent writes its own bill; read back from the box's state directory."""
USAGE_IN_BOX = Path("cella") / ".cache" / "executor-usage.json"
STATE_DB = Path("cella") / "state.db"
"""The agent's own session store: a turn that never wrote its usage file still left a bill here."""
CARD = Path("persona") / "environment.md"
"""The environment card: what stays true between tasks, so the agent does not rediscover it."""


def main() -> int:
    try:
        raw: object = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as error:
        return _fail(f"the request is not JSON: {error}", billing="not_billed")
    if not isinstance(raw, dict):
        return _fail("the request is not a JSON object", billing="not_billed")
    request = {str(key): value for key, value in raw.items()}
    task = request.get("task")
    box = request.get("box")
    if not isinstance(task, dict) or not isinstance(box, dict):
        return _fail("the request carries no task or no box", billing="not_billed")
    root = Path(str(box.get("root", "")))
    name = str(box.get("name", ""))
    compose = root / "compose.yaml"
    if not name or not compose.is_file():
        return _fail(
            f"the box {name or '?'} has no compose file: `pantheon up` starts the agent",
            billing="not_billed",
        )
    limits = request.get("limits")
    seconds = int(limits.get("max_seconds", 300)) if isinstance(limits, dict) else 300
    model = str(request.get("model") or DEFAULT_MODEL)
    usage_file = root / USAGE_IN_BOX
    usage_file.parent.mkdir(parents=True, exist_ok=True)
    usage_file.unlink(missing_ok=True)
    card = _card(root)
    command = [
        *container.docker_prefix(),
        "compose",
        "-p",
        container.PROJECT_PREFIX + name,
        "-f",
        str(compose),
        "exec",
        "-T",
        "-u",
        str(os.getuid()),
        SERVICE,
        HERMES,
        "-z",
        _prompt(
            task,
            _context_lines(request),
            request.get("previous"),
            turn=request.get("turn"),
            turns=request.get("turns"),
            ceiling=ceiling(request),
            steps=request.get("engine") == "steps",
            card=card[0] if card else None,
        ),
        "-m",
        model,
        "--provider",
        container.AGENT_PROVIDER,
        "--usage-file",
        USAGE_PATH,
    ]
    started = time.time()
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=float(seconds)
        )
    except subprocess.TimeoutExpired:
        usage, billing = _bill(usage_file, root, started)
        return _fail(
            f"the agent did not answer within {seconds} s", billing=billing, usage=usage
        )
    except (OSError, subprocess.SubprocessError) as error:
        usage, billing = _bill(usage_file, root, started)
        return _fail(
            f"the container could not be asked: {error}", billing=billing, usage=usage
        )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        usage, billing = _bill(usage_file, root, started)
        return _fail(
            "the agent in the container failed: " + ("; ".join(detail[-3:]) or "no message"),
            billing=billing,
            usage=usage,
        )
    notes = ["executor: hermes in the box's container"]
    if card is not None:
        notes.append(f"environment card {card[1][:12]}")
    usage = _usage(usage_file)
    if done.stdout.strip().upper() == "DONE":
        answer: dict[str, object] = {
            "done": True,
            "model": model,
            "notes": notes,
            "billing_status": "billed",
        }
        if usage is not None:
            answer["usage"] = usage
        print(json.dumps(answer))
        return 0
    diff = extract_diff(done.stdout)
    if diff is None:
        problem = _error_line(done.stdout)
        usage = usage or _state_usage(root, started)
        return _fail(
            problem or "the agent answered without a unified diff",
            notes=[done.stdout[:400]],
            billing="billed",
            usage=usage,
        )
    cached = _cached_tokens(usage_file)
    if cached:
        notes.append(f"cache read {cached} tokens")
    answer = {"patch": diff, "model": model, "notes": notes, "billing_status": "billed"}
    if usage is not None:
        answer["usage"] = usage
    print(json.dumps(answer))
    return 0


PREVIOUS_HEAD = (
    "Предыдущая попытка не прошла. Ответь на эти факты, а не повторяй их:"
)


def ceiling(request: dict[str, object]) -> object:
    """The call ceiling the box declared, for the step heading: data, never a guess."""
    limits = request.get("limits")
    return limits.get("max_steps") if isinstance(limits, dict) else None


def _prompt(
    task: dict[str, object],
    context: list[str] | None = None,
    previous: object = None,
    *,
    turn: object = None,
    turns: object = None,
    ceiling: object = None,
    steps: bool = False,
    card: str | None = None,
) -> str:
    if task.get("role") == "architect":
        return _architect_prompt(task, context, previous, card=card)
    paths = task.get("paths")
    scope = ", ".join(str(item) for item in paths) if isinstance(paths, list) else "none declared"
    # The first block is the same for every task in the box: the environment card and the
    # rules of the trade sit before the task so the provider can keep them in its prefix cache.
    lines = [
        "Ты — Август коробки. Проект смонтирован только на чтение в /work/project.",
        "Проверки прогонит коробка после твоего ответа, и решают они. Проверить себя можно "
        "самому: скопируй проект в /opt/data/work/ и гоняй проверку оттуда — командой из "
        "карточки, если она там названа.",
    ]
    if card:
        lines += ["", "Среда проекта, как её видит коробка:", card]
    lines += [
        "",
        f"Задача: {task.get('goal') or 'цель не объявлена'}",
        f"Тип: {task.get('kind') or 'не объявлен'}",
        f"Объём (пути относительно корня проекта): {scope}",
        f"Критерий: {task.get('expected') or 'не объявлен'}",
    ]
    forbidden = task.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        lines.append(f"Запрещено: {', '.join(str(item) for item in forbidden)}")
    if context:
        lines += ["", *context]
    prior = previous_lines(previous)
    if prior:
        lines += ["", PREVIOUS_HEAD, *prior]
    step_lines = turn_lines(turn, turns, ceiling, steps=steps)
    if step_lines:
        lines += ["", *step_lines]
    lines += [
        "Каждый файл заканчивается переводом строки. Верни ОДИН унифицированный диф в формате "
        "git (пути a/<файл> и b/<файл>, заголовки @@), "
        "который применяется командой `git apply` от корня проекта; если ты проверял правку в "
        "своей копии, пути в дифе всё равно от корня проекта. Только файлы из объёма. "
        "Никаких пояснений и markdown-заборов. Если задача невыполнима — одна строка: "
        "ERROR: <причина>.",
    ]
    return "\n".join(lines)


def _architect_prompt(
    task: dict[str, object],
    context: list[str] | None = None,
    previous: object = None,
    *,
    card: str | None = None,
) -> str:
    paths = task.get("paths")
    scope = ", ".join(str(item) for item in paths) if isinstance(paths, list) else "none declared"
    lines = [
        "Ты — архитектор коробки: вторая роль того же агента, советник, а не исполнитель.",
        "Твоя работа — документы и модель, а не код: бриф, аудит, ADR, модель слоёв, требования.",
        "Проект смонтирован только на чтение в /work/project. Ты не пишешь код и тесты и не "
        "пушишь: файлы вне объёма ворота всё равно откажут.",
    ]
    if card:
        lines += ["", "Среда проекта, как её видит коробка:", card]
    lines += [
        "",
        f"Задача: {task.get('goal') or 'цель не объявлена'}",
        f"Критерий: {task.get('expected') or 'не объявлен'}",
        f"Объём (пути относительно корня проекта): {scope}",
    ]
    forbidden = task.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        lines.append(f"Запрещено: {', '.join(str(item) for item in forbidden)}")
    if context:
        lines += ["", *context]
    prior = previous_lines(previous)
    if prior:
        lines += ["", PREVIOUS_HEAD, *prior]
    lines += [
        "",
        "Верни ОДИН унифицированный диф в формате git (пути a/<файл> и b/<файл>, заголовки @@), "
        "который применяется командой `git apply` от корня проекта и меняет только файлы из "
        "объёма. Никаких пояснений и markdown-заборов. Если для вывода не хватает данных "
        "проекта — одна строка: ERROR: <чего не хватает>.",
    ]
    return "\n".join(lines)


def _card(root: Path) -> tuple[str, str] | None:
    """The box's environment card and its digest, or nothing when it was never written."""
    path = root / CARD
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _context_lines(request: dict[str, object]) -> list[str]:
    """The pack the box assembled, if any: the lines are data, not instructions to parse."""
    context = request.get("context")
    if not isinstance(context, dict):
        return []
    lines = context.get("lines")
    if not isinstance(lines, list):
        return []
    return [str(item) for item in lines if isinstance(item, str)]


def _usage(path: Path) -> dict[str, object] | None:
    """What the agent says it spent, in the same shape the harness records for any executor."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    prompt_tokens = data.get("input_tokens")
    completion_tokens = data.get("output_tokens")
    cost = data.get("estimated_cost_usd")
    return {
        "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
        "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
        "cost_cents": round(cost * 100, 4) if isinstance(cost, (int, float)) else None,
    }


def _cached_tokens(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("cache_read_tokens") if isinstance(data, dict) else None
    return value if isinstance(value, int) and value else None


def _error_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip().upper().startswith("ERROR:"):
            return line.split(":", 1)[1].strip() or "the agent reported an error"
    return None


def _bill(
    usage_file: Path, root: Path, started: float
) -> tuple[dict[str, object] | None, str]:
    """What a failed call cost, and whether that is known.

    The agent's usage file is written when a turn ends normally; a killed turn leaves nothing
    there, and the ceiling is the most the call could have cost, not what it did cost. The
    agent's own session store answers the same question after a turn that never reported: the
    rows that started with this call carry tokens and the price the provider named.
    """
    usage = _usage(usage_file)
    if usage is not None and usage.get("cost_cents") is not None:
        return usage, "billed"
    from_store = _state_usage(root, started)
    if from_store is not None:
        return from_store, "billed"
    if usage is not None:
        return usage, "billed"
    return None, "unknown"


def _state_usage(root: Path, started: float) -> dict[str, object] | None:
    """The call's bill from the agent's session store, when the store has a row for it.

    Read-only, and only the rows that began with this call: a box runs one turn at a time, so
    anything that started after ``started`` belongs to it.
    """
    path = root / STATE_DB
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        try:
            row = db.execute(
                "select sum(input_tokens), sum(output_tokens), sum(estimated_cost_usd), "
                "count(*) from session_model_usage where first_seen >= ?",
                (started - 2.0,),
            ).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    if not row or not row[3]:
        return None
    prompt, completion, cost, _calls = row
    if not isinstance(cost, (int, float)):
        return None
    return {
        "prompt_tokens": int(prompt) if isinstance(prompt, (int, float)) else None,
        "completion_tokens": int(completion) if isinstance(completion, (int, float)) else None,
        "cost_cents": round(float(cost) * 100, 4),
    }


def _fail(
    message: str,
    notes: list[str] | None = None,
    *,
    billing: str = "unknown",
    usage: dict[str, object] | None = None,
) -> int:
    payload: dict[str, object] = {"error": message, "billing_status": billing}
    if usage is not None:
        payload["usage"] = usage
    if notes:
        payload["notes"] = notes
    print(json.dumps(payload))
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the shim
    sys.exit(main())
