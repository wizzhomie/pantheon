"""The Anthropic client: text answers and the tool-based structured path."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from trajan.llm import (
    AnthropicClient,
    LLMAuthError,
    LLMRequest,
    Message,
)

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: Any) -> AnthropicClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicClient(
        model="claude-test", api_key="secret", http=http, backoff_s=0.0, **kwargs
    )


def request(schema: dict[str, Any] | None = None) -> LLMRequest:
    return LLMRequest(
        model="claude-test",
        messages=[
            Message(role="system", content="you extract data"),
            Message(role="user", content="extract it"),
        ],
        response_schema=schema,
    )


async def test_text_answer_is_normalised() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "model": "claude-test",
                "content": [{"type": "text", "text": "hello"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    response = await make_client(handler).complete(request())
    assert response.text == "hello"
    assert response.finish_reason == "stop"
    assert (response.prompt_tokens, response.completion_tokens) == (12, 3)
    assert response.request_id == "msg-1"


async def test_system_prompt_is_split_out_of_the_conversation() -> None:
    seen: dict[str, Any] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(http_request.content))
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
        )

    await make_client(handler).complete(request())
    assert seen["system"] == "you extract data"
    assert [message["role"] for message in seen["messages"]] == ["user"]


async def test_schema_uses_a_forced_tool_call() -> None:
    seen: dict[str, Any] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": "emit_result", "input": {"a": 1}}],
                "stop_reason": "tool_use",
            },
        )

    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    response = await make_client(handler).complete(request(schema=schema))
    assert seen["tool_choice"] == {"type": "tool", "name": "emit_result"}
    assert seen["tools"][0]["input_schema"] == schema
    assert json.loads(response.text) == {"a": 1}


async def test_max_tokens_is_visible_as_truncation() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "half"}], "stop_reason": "max_tokens"},
        )

    response = await make_client(handler).complete(request())
    assert response.truncated is True


async def test_empty_content_is_rejected() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    with pytest.raises(Exception, match="neither text nor tool_use"):
        await make_client(handler).complete(request())


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMAuthError, match="ANTHROPIC_API_KEY"):
        AnthropicClient(model="claude-test")
