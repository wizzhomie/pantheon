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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.caesar import matches
from harness.executor import previous_lines, turn_lines

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
CONTEXT_CHARS = 120_000
TIMEOUT_SECONDS = 180.0

SYSTEM = (
    "You are the executor inside a gated engineering box. The box's write gate will apply your "
    "answer only if it is a unified diff that stays inside the declared paths. Answer with "
    "exactly one unified diff in git format (paths a/<file> and b/<file>, with @@ hunks) and "
    "nothing else — no explanation, no questions, no markdown fences. The diff must apply "
    "cleanly with `git apply` to the current tree. Touch only files under the declared paths. "
    "If the task genuinely cannot be done with a diff, answer with one line: ERROR: <reason>. "
    "Every file you create or change ends with a newline."
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
    read_chars = _read_chars(request)
    limits = request.get("limits")
    ceiling = limits.get("max_steps") if isinstance(limits, dict) else None
    prompt = _prompt(
        task,
        project,
        _context_lines(request),
        request.get("previous"),
        read_chars,
        turn=request.get("turn"),
        turns=request.get("turns"),
        ceiling=ceiling,
    )
    if (
        read_chars is not None
        and prompt.reading.get("files") == 0
        and prompt.reading.get("left_out")
    ):
        return _fail(
            f"the declared read budget of {read_chars} chars fits none of the files in scope: "
            "nothing was read, so the task was not sent; raise max_read_chars"
        )
    started = time.monotonic()
    try:
        answer = _post(
            os.environ.get(URL_ENV) or DEFAULT_URL,
            key,
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt.text},
                ],
                "temperature": 0,
                "max_tokens": _max_tokens(request),
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
    usage = answer.get("usage")
    table: dict[str, Any] = usage if isinstance(usage, dict) else {}
    prompt_tokens = _int(table.get("prompt_tokens"))
    completion_tokens = _int(table.get("completion_tokens"))
    cost = _cost(prompt_tokens, completion_tokens)
    if text.strip().upper() == "DONE":
        print(
            json.dumps(
                {
                    "done": True,
                    "model": reported_model or model,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_cents": cost,
                    },
                    "duration_ms": int(elapsed * 1000),
                    "notes": notes,
                    "reading": prompt.reading,
                }
            )
        )
        return 0
    diff = extract_diff(text)
    if diff is None:
        problem = _error_line(text)
        if not text.strip() and finish == "length":
            return _fail(
                "the answer was cut before any text: the model spent its token budget on "
                "reasoning and returned nothing",
                notes=[f"finish_reason=length; raise {MAX_TOKENS_ENV} and ask again"],
                usage=_usage_payload(answer),
                model=reported_model or model,
            )
        return _fail(
            problem or "the model answered without a unified diff",
            notes=[text[:400]],
            usage=_usage_payload(answer),
            model=reported_model or model,
        )
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
                "truncated": finish == "length",
                "notes": notes,
                "reading": prompt.reading,
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


@dataclass(frozen=True, slots=True)
class Prompt:
    """The words sent to the model, and what reading them cost before the call was made."""

    text: str
    reading: dict[str, object]


def _prompt(
    task: dict[str, Any],
    project: Path,
    context: list[str] | None = None,
    previous: object = None,
    read_chars: int | None = None,
    *,
    turn: object = None,
    turns: object = None,
    ceiling: object = None,
) -> Prompt:
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
    shape = task.get("shape")
    if isinstance(shape, str) and shape:
        lines += [
            "",
            f"THE SHAPE OF THIS CHANGE: {shape}. "
            + _shape_words(shape),
            "If the change cannot be made in that shape, answer with one line: "
            "ERROR: <what the change really needs>. The box records that as a wrong estimate "
            "instead of paying for a retry.",
        ]
    forbidden = task.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        lines.append(f"forbidden: {', '.join(str(item) for item in forbidden)}")
    own = task.get("self_check")
    if isinstance(own, list) and own:
        lines += [
            "",
            "THE CHECK YOU CAN SEE. The task names it, and the box runs it beside the verdict:",
            "  " + json.dumps(own, ensure_ascii=False),
            "The box also judges this change with its own check, over the same behaviour with",
            "different data, and you do not see it. Make the behaviour right; an answer fitted",
            "to the strings of the check above is not a change.",
        ]
    if context:
        lines += ["", *context]
    prior = previous_lines(previous)
    if prior:
        lines += ["", "THE PREVIOUS ATTEMPT DID NOT PASS. Answer these facts:", *prior]
    steps = turn_lines(turn, turns, ceiling)
    if steps:
        lines += ["", *steps]
    lines += _project_section(files, in_scope)
    block, reading = _scope(project, in_scope, read_chars)
    beyond = len(files) - FILE_LIST_LIMIT if not _narrow(files, in_scope) else 0
    if beyond > 0:
        left = reading["left_out"]
        assert isinstance(left, list)
        left.append(f"{beyond} file name(s) beyond the {FILE_LIST_LIMIT}-name list")
    lines += ["FILES IN THE DECLARED SCOPE:", *block]
    return Prompt("\n".join(lines), reading)


SHAPE_WORDS = {
    "patch": "a few lines in the places the task names: do not restate the file or its imports",
    "file": "one file may be rewritten as a whole, but only the file the task names",
    "multi": "several files may change, each as one diff, and every one inside the declared paths",
}
"""What each shape asks for, in words the model can act on: the size and the answer together."""


def _shape_words(shape: str) -> str:
    return SHAPE_WORDS.get(shape, "the change stays inside the declared paths")


def _narrow(files: list[str], in_scope: list[str]) -> bool:
    """Is the task's scope a part of the project rather than the whole of it?"""
    return bool(in_scope) and len(in_scope) < len(files)


def _project_section(files: list[str], in_scope: list[str]) -> list[str]:
    """What the model is told about the project's files: a layout, not an inventory.

    A task that may touch one file does not need the names of the other hundred and eighty; the
    pilot measured that noise at about 1 200 tokens — more than the task and its check together,
    against a model whose answers start to loop just above 9 000. So a narrow scope gets the
    top-level layout and the count, and the names in scope come with their content below. A
    scope that covers the project still gets the list, because there the names are the work.
    """
    if not _narrow(files, in_scope):
        listed = [*files[:FILE_LIST_LIMIT]]
        lines = ["", "FILES IN THE PROJECT (relative to its root):", *listed, ""]
        return lines
    tops = sorted({name.split("/")[0] for name in files if "/" in name})
    loose = sum(1 for name in files if "/" not in name)
    where = ", ".join(tops) or "the root"
    tail = f", and {loose} file(s) at the root" if loose else ""
    return [
        "",
        f"THE PROJECT: {len(files)} files under {where}{tail}. The change may touch only the "
        "declared paths; the files in them come below, with their content.",
    ]


def _scope(
    project: Path, names: list[str], declared: int | None
) -> tuple[list[str], dict[str, object]]:
    """Read the declared scope within the budget, and name every file the budget left out.

    The budget is the task's if it declared one, never more than the module's own ceiling.
    A file that does not fit is not half-read: the read stops and the rest is counted, so a
    prompt that looks complete in the answer never hides a file the model did not see.
    """
    cap = CONTEXT_CHARS if declared is None else min(CONTEXT_CHARS, declared)
    parts: list[str] = []
    used = 0
    read = 0
    unreadable = 0
    after_budget = 0
    for index, name in enumerate(names):
        try:
            text = (project / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable += 1
            continue
        if used + len(text) > cap:
            after_budget = len(names) - index
            break
        parts += [f"--- {name} ---", text, ""]
        used += len(text)
        read += 1
    left_out: list[str] = []
    if after_budget:
        by = (
            "the declared read budget"
            if declared is not None
            else "the prompt size limit"
        )
        left_out.append(f"{after_budget} file(s) after {by} of {cap} chars")
    if unreadable:
        left_out.append(f"{unreadable} file(s) that could not be read as text")
    reading: dict[str, object] = {
        "files": read,
        "chars": used,
        "tokens_estimate": used // 4,
        "cap_chars": cap,
        "declared_chars": declared,
        "left_out": left_out,
    }
    return (parts or ["(nothing in the declared scope could be read)"]), reading


def _read_chars(request: dict[str, object]) -> int | None:
    """The task's read budget, if it declared one; the box sends it in ``limits``."""
    limits = request.get("limits")
    if not isinstance(limits, dict):
        return None
    value = limits.get("max_read_chars")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _context_lines(request: dict[str, object]) -> list[str]:
    """The pack the box assembled, if any: the lines are data, not instructions to parse."""
    context = request.get("context")
    if not isinstance(context, dict):
        return []
    lines = context.get("lines")
    if not isinstance(lines, list):
        return []
    return [str(item) for item in lines if isinstance(item, str)]


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


def _max_tokens(request: dict[str, object] | None = None) -> int:
    """The answer's room: the task's shape decides, then the box, then the built-in default.

    The shape is the box's own declaration of how big this change is (patch, file, several
    files), so the cap follows the work rather than one number for every task.
    """
    limits = request.get("limits") if isinstance(request, dict) else None
    if isinstance(limits, dict):
        chosen = limits.get("max_answer_tokens")
        if isinstance(chosen, int) and not isinstance(chosen, bool) and chosen > 0:
            return chosen
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


def _fail(
    message: str,
    notes: list[str] | None = None,
    usage: dict[str, object] | None = None,
    model: str | None = None,
) -> int:
    payload: dict[str, object] = {"error": message}
    if notes:
        payload["notes"] = notes
    if usage is not None:
        payload["usage"] = usage
    if model is not None:
        payload["model"] = model
    print(json.dumps(payload))
    return 1


def _usage_payload(answer: dict[str, object]) -> dict[str, object] | None:
    """What the call cost, even when the answer was not a change: a call happened either way."""
    usage = answer.get("usage")
    if not isinstance(usage, dict):
        return None
    table: dict[str, object] = {str(key): value for key, value in usage.items()}
    prompt_tokens = _int(table.get("prompt_tokens"))
    completion_tokens = _int(table.get("completion_tokens"))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_cents": _cost(prompt_tokens, completion_tokens),
    }


if __name__ == "__main__":  # pragma: no cover - exercised through the shim
    sys.exit(main())
