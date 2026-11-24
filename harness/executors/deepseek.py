"""The reference executor: ask an OpenAI-compatible endpoint for a diff, report the bill.

This is the smallest honest adapter for the protocol in ``harness/executor.py``: it reads the
task JSON from stdin, puts the declared scope and the files in it into a prompt, calls the
endpoint, pulls one unified diff out of the answer, and prints JSON with the patch and what it
spent. It never prints the key, and it never writes to the project: the diff is returned, and
Caesar's gate decides whether it may be applied.

Any command that speaks the same protocol can replace it. That is the point — the adapter for
a subscription CLI (``claude -p``, ``codex exec``) is the next one, and the harness does not
need to know which of them ran.

Configuration is environment, never a file in the repository:

* ``PANTHEON_MODEL_KEY`` (or ``DEEPSEEK_API_KEY``) — the key;
* ``PANTHEON_MODEL_URL`` — default ``https://api.deepseek.com/chat/completions``;
* ``PANTHEON_MODEL`` — the model, unless the task's executor profile names one;
* ``PANTHEON_MODEL_MAX_TOKENS`` — the answer's cap, default 4096;
* ``PANTHEON_MODEL_PRICE_IN`` / ``PANTHEON_MODEL_PRICE_OUT`` — dollars per million tokens.
  Without them the answer carries no money, and the box records "cost not reported" instead
  of inventing a number.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from harness.caesar import matches

DEFAULT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
KEY_ENV = "PANTHEON_MODEL_KEY"
FALLBACK_KEY_ENV = "DEEPSEEK_API_KEY"
URL_ENV = "PANTHEON_MODEL_URL"
MODEL_ENV = "PANTHEON_MODEL"
MAX_TOKENS_ENV = "PANTHEON_MODEL_MAX_TOKENS"
PRICE_IN_ENV = "PANTHEON_MODEL_PRICE_IN"
PRICE_OUT_ENV = "PANTHEON_MODEL_PRICE_OUT"
DEFAULT_MAX_TOKENS = 4096
FILE_LIST_LIMIT = 300
CONTEXT_CHARS = 60_000
FILE_CHARS = 8_000
TIMEOUT_SECONDS = 180.0

SYSTEM = (
    "You are the executor inside a gated engineering box. The box's write gate will apply your "
    "answer only if it is a unified diff that stays inside the declared paths. Answer with "
    "exactly one unified diff in git format (paths a/<file> and b/<file>, with @@ hunks) and "
    "nothing else — no explanation, no questions, no markdown fences. The diff must apply "
    "cleanly with `git apply` to the current tree. Touch only files under the declared paths. "
    "If the task genuinely cannot be done with a diff, answer with one line: ERROR: <reason>."
)


class ApiError(Exception):
    """The endpoint answered with an error, or did not answer at all."""


def main() -> int:
    """stdin: the request; stdout: the answer; the key never appears in either."""
    try:
        raw: object = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as error:
        return _fail(f"the request is not JSON: {error}")
    if not isinstance(raw, dict):
        return _fail("the request is not a JSON object")
    request = {str(key): value for key, value in raw.items()}
    key = os.environ.get(KEY_ENV) or os.environ.get(FALLBACK_KEY_ENV)
    if not key:
        return _fail(f"no model key: set {KEY_ENV} (or {FALLBACK_KEY_ENV})")
    task = request.get("task")
    if not isinstance(task, dict):
        return _fail("the request carries no task")
    project = _project_path(request)
    model = str(request.get("model") or os.environ.get(MODEL_ENV) or DEFAULT_MODEL)
    started = time.monotonic()
    try:
        answer = _post(
            os.environ.get(URL_ENV) or DEFAULT_URL,
            key,
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": _prompt(task, project)},
                ],
                "temperature": 0,
                "max_tokens": _max_tokens(),
            },
        )
    except ApiError as error:
        return _fail(str(error))
    elapsed = round(time.monotonic() - started, 1)
    try:
        text, reported_model, finish = _content(answer)
    except ApiError as error:
        return _fail(str(error))
    notes: list[str] = []
    diff = extract_diff(text)
    if diff is None:
        problem = _error_line(text)
        return _fail(problem or "the model answered without a unified diff", notes=[text[:400]])
    usage = answer.get("usage")
    table: dict[str, Any] = usage if isinstance(usage, dict) else {}
    prompt_tokens = _int(table.get("prompt_tokens"))
    completion_tokens = _int(table.get("completion_tokens"))
    cost = _cost(prompt_tokens, completion_tokens)
    if cost is None:
        notes.append(
            "no price is configured: the box will record the tokens and say the cost is unknown"
        )
    if finish == "length":
        notes.append("the answer hit the token cap: the diff may be incomplete")
    print(
        json.dumps(
            {
                "patch": diff,
                "model": reported_model or model,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_cents": cost,
                },
                "duration_ms": int(elapsed * 1000),
                "notes": notes,
            }
        )
    )
    return 0


def extract_diff(text: str) -> str | None:
    """One unified diff out of the answer, fenced or bare; ``None`` when there is none."""
    for match in FENCE.finditer(text):
        block = match.group(1)
        if _looks_like_diff(block):
            return block.strip() + "\n"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- "):
            candidate = "\n".join(lines[index:])
            if _looks_like_diff(candidate):
                return candidate.strip() + "\n"
    return None


FENCE: re.Pattern[str] = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _looks_like_diff(text: str) -> bool:
    return "@@" in text and "\n+++ " in "\n" + text


def _error_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip().upper().startswith("ERROR:"):
            return line.split(":", 1)[1].strip() or "the model reported an error"
    return None


def _project_path(request: dict[str, object]) -> Path:
    project = request.get("project")
    if isinstance(project, dict):
        path = project.get("path")
        if isinstance(path, str) and path:
            return Path(path)
    return Path.cwd()


def _prompt(task: dict[str, Any], project: Path) -> str:
    paths = task.get("paths")
    patterns = [item for item in paths if isinstance(item, str)] if isinstance(paths, list) else []
    files = _files(project)
    in_scope = [name for name in files if any(matches(pattern, name) for pattern in patterns)]
    lines = [
        f"TASK {task.get('id')}: {task.get('goal') or 'no goal declared'}",
        f"kind: {task.get('kind') or 'not declared'}",
        f"role: {task.get('role') or 'executor'}",
        f"paths the change may touch: {', '.join(patterns) or 'none declared'}",
        f"expected: {task.get('expected') or 'not declared'}",
    ]
    forbidden = task.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        lines.append(f"forbidden: {', '.join(str(item) for item in forbidden)}")
    lines += ["", "FILES IN THE PROJECT (relative to its root):", *files[:FILE_LIST_LIMIT], ""]
    lines += ["FILES IN THE DECLARED SCOPE:", *_contents(project, in_scope)]
    return "\n".join(lines)


def _files(project: Path) -> list[str]:
    try:
        done = subprocess.run(
            ["git", "-C", str(project), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    return [line for line in done.stdout.splitlines() if line]


def _contents(project: Path, names: list[str]) -> list[str]:
    parts: list[str] = []
    left = CONTEXT_CHARS
    for name in names:
        if left <= 0:
            parts.append("… more files in scope were left out: the prompt has a size limit")
            break
        try:
            text = (project / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        snippet = text[:FILE_CHARS]
        if len(text) > FILE_CHARS:
            snippet += "\n… (truncated)"
        parts += [f"--- {name} ---", snippet, ""]
        left -= len(snippet)
    return parts or ["(nothing in the declared scope could be read)"]


def _post(url: str, key: str, payload: dict[str, object]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            decoded = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise ApiError(f"the endpoint said {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ApiError(f"the endpoint is unreachable: {error}") from error
    try:
        parsed: object = json.loads(decoded)
    except json.JSONDecodeError as error:
        raise ApiError(f"the endpoint's answer is not JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ApiError("the endpoint's answer is not a JSON object")
    return parsed


def _content(answer: dict[str, Any]) -> tuple[str, str | None, str | None]:
    choices = answer.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ApiError("the endpoint's answer has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ApiError("the endpoint's answer has no message")
    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ApiError("the endpoint's answer has no message content")
    model = answer.get("model")
    finish = first.get("finish_reason")
    return (
        content,
        model if isinstance(model, str) else None,
        finish if isinstance(finish, str) else None,
    )


def _max_tokens() -> int:
    raw = os.environ.get(MAX_TOKENS_ENV)
    try:
        value = int(raw) if raw else DEFAULT_MAX_TOKENS
    except ValueError:
        return DEFAULT_MAX_TOKENS
    return value if value > 0 else DEFAULT_MAX_TOKENS


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _price(name: str) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _cost(prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    """Cents for one call, or ``None`` when a price is missing: a number needs both halves."""
    price_in = _price(PRICE_IN_ENV)
    price_out = _price(PRICE_OUT_ENV)
    if price_in is None or price_out is None:
        return None
    if prompt_tokens is None or completion_tokens is None:
        return None
    dollars = (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
    return round(dollars * 100, 4)


def _fail(message: str, notes: list[str] | None = None) -> int:
    payload: dict[str, object] = {"error": message}
    if notes:
        payload["notes"] = notes
    print(json.dumps(payload))
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the shim
    sys.exit(main())
