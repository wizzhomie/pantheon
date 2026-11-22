"""Client for any OpenAI-compatible chat-completions API.

One client covers OpenAI, DeepSeek, OpenRouter, Together, vLLM and a local llama.cpp
server: the shape of ``POST /chat/completions`` is the de-facto standard. That is why the
library depends on ``httpx`` instead of a vendor SDK — the payload stays visible, and
adding a provider is a base URL, not a new dependency.
"""

from __future__ import annotations

import os
from time import perf_counter
from typing import Any

import httpx

from ._http import post_json
from .base import FinishReason, LLMRequest, LLMResponse
from .errors import LLMAuthError, LLMBadResponse

FINISH_REASONS: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
}


class OpenAICompatibleClient:
    """Call a chat-completions endpoint and normalise the answer."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
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
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._api_key = key
        self._http = http

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = perf_counter()
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": request.response_schema,
                    "strict": True,
                },
            }
        data = await self._post(payload, request)
        return _parse(data, request, started)

    async def _post(self, payload: dict[str, Any], request: LLMRequest) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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


def _parse(data: dict[str, Any], request: LLMRequest, started: float) -> LLMResponse:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMBadResponse("response has no choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LLMBadResponse("first choice is not an object")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    text = _text_of(content)
    if text is None:
        raise LLMBadResponse("response has no message content")
    prompt_tokens, completion_tokens = _tokens(data.get("usage"))
    finish = FINISH_REASONS.get(str(choice.get("finish_reason")), "unknown")
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


def _text_of(content: object) -> str | None:
    """Accept a plain string or the list-of-parts form some gateways return."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _tokens(usage: object) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return (
        prompt if isinstance(prompt, int) and prompt >= 0 else 0,
        completion if isinstance(completion, int) and completion >= 0 else 0,
    )
