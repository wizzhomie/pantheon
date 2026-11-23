"""Client for the Anthropic Messages API.

Anthropic does not have a JSON-schema response format; the supported way to force a
structured answer is a tool call with ``input_schema`` and ``tool_choice``. The client
therefore asks for a single ``emit_result`` tool and turns its input back into text, so the
rest of the library sees one uniform :class:`LLMResponse`.
"""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any

import httpx

from ._http import post_json
from .base import FinishReason, LLMRequest, LLMResponse
from .errors import LLMAuthError, LLMBadResponse

TOOL_NAME = "emit_result"
DEFAULT_VERSION = "2023-06-01"

FINISH_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "stop",
    "max_tokens": "length",
    "refusal": "content_filter",
}


class AnthropicClient:
    """Call ``POST /v1/messages`` and normalise the answer."""

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.anthropic.com",
        api_key: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        version: str = DEFAULT_VERSION,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.environ.get(api_key_env)
        if not key:
            raise LLMAuthError(f"no API key: pass api_key= or set {api_key_env}")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._api_key = key
        self._http = http

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = perf_counter()
        data = await self._post(_payload(request), request)
        return _parse(data, request, started)

    async def _post(self, payload: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self.version,
            "content-type": "application/json",
        }
        if self._http is not None:
            return await post_json(
                self._http,
                url,
                headers=headers,
                payload=payload,
                timeout_s=request.timeout_s or self.timeout_s,
                max_retries=self.max_retries,
                backoff_s=self.backoff_s,
            )
        async with httpx.AsyncClient() as http:
            return await post_json(
                http,
                url,
                headers=headers,
                payload=payload,
                timeout_s=request.timeout_s or self.timeout_s,
                max_retries=self.max_retries,
                backoff_s=self.backoff_s,
            )


def _payload(request: LLMRequest) -> dict[str, Any]:
    system = [message.content for message in request.messages if message.role == "system"]
    conversation = [
        {"role": message.role, "content": message.content}
        for message in request.messages
        if message.role != "system"
    ]
    payload: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "messages": conversation,
    }
    if system:
        payload["system"] = "\n\n".join(system)
    if request.response_schema is not None:
        payload["tools"] = [
            {
                "name": TOOL_NAME,
                "description": "Return the final structured result. Call this once.",
                "input_schema": request.response_schema,
            }
        ]
        payload["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
    return payload


def _parse(data: dict[str, Any], request: LLMRequest, started: float) -> LLMResponse:
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise LLMBadResponse("response has no content blocks")

    text_parts: list[str] = []
    tool_input: dict[str, Any] | None = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
            tool_input = block["input"]

    if tool_input is not None:
        text = json.dumps(tool_input, ensure_ascii=False)
    elif text_parts:
        text = "\n".join(text_parts)
    else:
        raise LLMBadResponse("response has neither text nor tool_use content")

    usage = data.get("usage")
    prompt_tokens = _count(usage, "input_tokens")
    completion_tokens = _count(usage, "output_tokens")
    finish = FINISH_REASONS.get(str(data.get("stop_reason")), "unknown")
    model = data.get("model")
    request_id = data.get("id")
    return LLMResponse(
        text=text,
        model=model if isinstance(model, str) else request.model,
        finish_reason=finish,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=(perf_counter() - started) * 1000,
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _count(usage: object, field: str) -> int:
    if not isinstance(usage, dict):
        return 0
    value = usage.get(field)
    return value if isinstance(value, int) and value >= 0 else 0
