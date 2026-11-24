"""A client whose provider is a command: the box asks its own agent instead of an API.

The harness already knows how to reach the agent inside a container (``hermes -z``, its
model, its bill). Repeating that knowledge in trajan would invert the dependency — trajan
must not know what a box is. So the boundary is a command: the request goes in on stdin as
JSON, the answer comes out on stdout as JSON.

The protocol is deliberately the same shape as :class:`LLMRequest` and
:class:`LLMResponse`, because that is the only vocabulary the two sides share:

in::

    {"schema_version": 1, "model": "...", "messages": [{"role": "...", "content": "..."}],
     "temperature": 0.0, "max_tokens": 1500, "response_schema": null}

out::

    {"text": "...", "model": "...", "prompt_tokens": 0, "completion_tokens": 0,
     "finish_reason": "stop", "cost_cents": 0.4}

``cost_cents`` is read by whoever runs the command and writes the bill down; trajan keeps
tokens, because tokens are what its report can check. A command that does not know its bill
omits the key — that is "unknown", not zero.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from collections.abc import Sequence

from .base import LLMRequest, LLMResponse
from .errors import LLMBadResponse, LLMError, LLMTimeout

SCHEMA_VERSION = 1
"""Bumped when the stdin/stdout shape changes incompatibly; the command reads it."""

DEFAULT_TIMEOUT_S = 180.0
"""A review is one call; two minutes is already a slow model, three is a hung one."""


class CommandClient:
    """Runs ``command`` with the request on stdin and parses the answer from stdout."""

    name = "command"

    def __init__(self, command: Sequence[str], *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        if not command:
            raise ValueError("the reviewer command is empty")
        self.command = list(command)
        self.timeout_s = timeout_s

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = _payload(request)
        timeout = request.timeout_s or self.timeout_s
        try:
            done = await asyncio.to_thread(
                subprocess.run,
                self.command,
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=dict(os.environ),
            )
        except subprocess.TimeoutExpired as expired:
            raise LLMTimeout(
                f"the reviewer command did not answer within {timeout:g}s: "
                f"{shlex.join(self.command)}"
            ) from expired
        except OSError as error:
            raise LLMError(
                f"the reviewer command could not run: {shlex.join(self.command)}: {error}"
            ) from error
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip().splitlines()
            tail = "; ".join(detail[-3:]) if detail else f"exit {done.returncode}"
            raise LLMError(f"the reviewer command failed: {tail}"[:400])
        return _parse(done.stdout, request)


def from_env() -> CommandClient:
    """``trajan verify --reviewer trajan.llm.command:from_env``."""
    command = os.environ.get("TRAJAN_REVIEW_COMMAND")
    if not command:
        raise ValueError(
            "TRAJAN_REVIEW_COMMAND is not set; it is the command that reads the change, "
            "e.g. bin/pantheon-review"
        )
    timeout = os.environ.get("TRAJAN_REVIEW_COMMAND_TIMEOUT")
    try:
        seconds = float(timeout) if timeout else DEFAULT_TIMEOUT_S
    except ValueError as error:
        raise ValueError(f"TRAJAN_REVIEW_COMMAND_TIMEOUT is not a number: {timeout!r}") from error
    return CommandClient(shlex.split(command), timeout_s=seconds)


def _payload(request: LLMRequest) -> dict[str, object]:
    """The request as the command receives it: the same fields, no object identity."""
    return {
        "schema_version": SCHEMA_VERSION,
        "model": request.model,
        "messages": [message.model_dump() for message in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "response_schema": request.response_schema,
    }


def _parse(text: str, request: LLMRequest) -> LLMResponse:
    """The command's answer, checked field by field: absent is not the same as empty."""
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise LLMBadResponse(f"the reviewer command answered no JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMBadResponse("the reviewer command answered JSON that is not an object")
    if data.get("error"):
        raise LLMError(f"the reviewer command failed: {str(data['error'])[:400]}")
    answer = data.get("text")
    if not isinstance(answer, str) or not answer.strip():
        raise LLMBadResponse("the reviewer command answered with no text")
    model = data.get("model")
    finish = data.get("finish_reason")
    return LLMResponse(
        text=answer,
        model=model if isinstance(model, str) and model else request.model or "command",
        finish_reason=finish if finish in _FINISHES else "unknown",
        prompt_tokens=_count(data.get("prompt_tokens")),
        completion_tokens=_count(data.get("completion_tokens")),
    )


_FINISHES = frozenset({"stop", "length", "content_filter", "tool_calls", "unknown"})


def _count(value: object) -> int:
    """A count a command reported; a missing or nonsensical one is 0, not a guess."""
    return value if isinstance(value, int) and value >= 0 else 0
