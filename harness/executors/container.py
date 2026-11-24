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

import json
import os
import subprocess
import sys
from pathlib import Path

from harness import container
from harness.executors.deepseek import extract_diff

DEFAULT_MODEL = "deepseek-flash"
HERMES = "/opt/hermes/.venv/bin/hermes"
SERVICE = "augustus"
USAGE_PATH = "/opt/data/.cache/executor-usage.json"
"""Where the agent writes its own bill; read back from the box's state directory."""
USAGE_IN_BOX = Path("state") / ".cache" / "executor-usage.json"


def main() -> int:
    try:
        raw: object = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as error:
        return _fail(f"the request is not JSON: {error}")
    if not isinstance(raw, dict):
        return _fail("the request is not a JSON object")
    request = {str(key): value for key, value in raw.items()}
    task = request.get("task")
    box = request.get("box")
    if not isinstance(task, dict) or not isinstance(box, dict):
        return _fail("the request carries no task or no box")
    root = Path(str(box.get("root", "")))
    name = str(box.get("name", ""))
    compose = root / "compose.yaml"
    if not name or not compose.is_file():
        return _fail(f"the box {name or '?'} has no compose file: `pantheon up` starts the agent")
    limits = request.get("limits")
    seconds = int(limits.get("max_seconds", 300)) if isinstance(limits, dict) else 300
    model = str(request.get("model") or DEFAULT_MODEL)
    usage_file = root / USAGE_IN_BOX
    usage_file.parent.mkdir(parents=True, exist_ok=True)
    usage_file.unlink(missing_ok=True)
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
        _prompt(task, _context_lines(request)),
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
    diff = extract_diff(done.stdout)
    if diff is None:
        problem = _error_line(done.stdout)
        return _fail(
            problem or "the agent answered without a unified diff", notes=[done.stdout[:400]]
        )
    usage = _usage(usage_file)
    notes = ["executor: hermes in the box's container"]
    cached = _cached_tokens(usage_file)
    if cached:
        notes.append(f"cache read {cached} tokens")
    answer: dict[str, object] = {"patch": diff, "model": model, "notes": notes}
    if usage is not None:
        answer["usage"] = usage
    print(json.dumps(answer))
    return 0


ARCHITECT = """\
Ты — архитектор коробки: вторая роль того же агента, советник, а не исполнитель.
Твоя работа — документы и модель, а не код: бриф, аудит, ADR, модель слоёв, требования.
Задача: {goal}
Критерий: {expected}
Объём (пути относительно корня проекта): {scope}
Проект смонтирован только на чтение в /work/project. Ты не пишешь код и тесты и не пушишь:
файлы вне объёма ворота всё равно откажут.
Верни ОДИН унифицированный диф в формате git (пути a/<файл> и b/<файл>, заголовки @@), который
применяется командой `git apply` от корня проекта и меняет только файлы из объёма. Никаких
пояснений и markdown-заборов. Если для вывода не хватает данных проекта — одна строка:
ERROR: <чего не хватает>.
"""
"""The architect's brief: the same agent, a different job — the frame, not the code."""


CONTEXT_HEAD = (
    "Контекст, собранный коробкой (пути записей — от корня коробки; "
    "в контейнере артефакты лежат в /work/artifacts):"
)


def _prompt(task: dict[str, object], context: list[str] | None = None) -> str:
    if task.get("role") == "architect":
        return _architect_prompt(task, context)
    paths = task.get("paths")
    scope = ", ".join(str(item) for item in paths) if isinstance(paths, list) else "none declared"
    lines = [
        f"Ты — Август коробки. Задача: {task.get('goal') or 'цель не объявлена'}",
        f"Тип: {task.get('kind') or 'не объявлен'}",
        f"Объём (пути относительно корня проекта): {scope}",
        f"Критерий: {task.get('expected') or 'не объявлен'}",
        "Проект смонтирован только на чтение в /work/project.",
    ]
    forbidden = task.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        lines.insert(4, f"Запрещено: {', '.join(str(item) for item in forbidden)}")
    if context:
        lines += ["", CONTEXT_HEAD, *context]
    lines += [
        "Верни ОДИН унифицированный диф в формате git (пути a/<файл> и b/<файл>, заголовки @@), "
        "который применяется командой `git apply` от корня проекта. Только файлы из объёма. "
        "Никаких пояснений и markdown-заборов. Если задача невыполнима — одна строка: "
        "ERROR: <причина>.",
    ]
    return "\n".join(lines)


def _architect_prompt(task: dict[str, object], context: list[str] | None = None) -> str:
    paths = task.get("paths")
    scope = ", ".join(str(item) for item in paths) if isinstance(paths, list) else "none declared"
    prompt = ARCHITECT.format(
        goal=task.get("goal") or "цель не объявлена",
        expected=task.get("expected") or "не объявлен",
        scope=scope,
    )
    if context:
        block = CONTEXT_HEAD + "\n" + "\n".join(context) + "\n"
        prompt = prompt.replace("Проект смонтирован", block + "Проект смонтирован")
    forbidden = task.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        prompt = prompt.replace(
            "Проект смонтирован только на чтение",
            f"Запрещено: {', '.join(str(item) for item in forbidden)}.\n"
            "Проект смонтирован только на чтение",
        )
    return prompt


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


def _fail(message: str, notes: list[str] | None = None) -> int:
    payload: dict[str, object] = {"error": message}
    if notes:
        payload["notes"] = notes
    print(json.dumps(payload))
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the shim
    sys.exit(main())
