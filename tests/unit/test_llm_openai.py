"""The OpenAI-compatible client, exercised against a mocked transport (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from qagent.llm import (
    LLMAuthError,
    LLMBadResponse,
    LLMError,
    LLMRateLimited,
    LLMRequest,
    LLMTimeout,
    Message,
    OpenAICompatibleClient,
)

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: Any) -> OpenAICompatibleClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleClient(
        model="test-model", api_key="secret", http=http, backoff_s=0.0, **kwargs
    )


def request(schema: dict[str, Any] | None = None) -> LLMRequest:
    return LLMRequest(
        model="test-model",
        messages=[Message(role="user", content="extract it")],
        response_schema=schema,
    )


def completion(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "req-1",
        "model": "test-model",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


async def test_happy_path_normalises_the_answer() -> None:
    seen: dict[str, Any] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(http_request.content))
        return httpx.Response(200, json=completion('{"a": 1}'))

    response = await make_client(handler).complete(request())
    assert response.text == '{"a": 1}'
    assert response.model == "test-model"
    assert response.finish_reason == "stop"
    assert (response.prompt_tokens, response.completion_tokens) == (10, 5)
    assert response.request_id == "req-1"
    assert response.latency_ms >= 0
    assert response.truncated is False
    assert seen["messages"][0] == {"role": "user", "content": "extract it"}


async def test_schema_becomes_a_strict_response_format() -> None:
    seen: dict[str, Any] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(http_request.content))
        return httpx.Response(200, json=completion("{}"))

    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    await make_client(handler).complete(request(schema=schema))
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["response_format"]["json_schema"]["schema"] == schema


async def test_truncation_is_visible() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion('{"a": ', finish_reason="length"))

    response = await make_client(handler).complete(request())
    assert response.truncated is True


async def test_auth_failure_is_not_retried() -> None:
    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(LLMAuthError):
        await make_client(handler).complete(request())
    assert calls == 1


async def test_rate_limit_is_retried_then_reported() -> None:
    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})

    client = make_client(handler, max_retries=1)
    with pytest.raises(LLMRateLimited) as caught:
        await client.complete(request())
    assert calls == 2
    assert caught.value.retry_after_s == 0.0


async def test_server_error_is_retried_then_reported() -> None:
    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="upstream unavailable")

    client = make_client(handler, max_retries=1)
    with pytest.raises(LLMError, match="503"):
        await client.complete(request())
    assert calls == 2


async def test_timeout_becomes_a_retryable_error() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow")

    with pytest.raises(LLMTimeout):
        await make_client(handler, max_retries=0).complete(request())


async def test_empty_content_is_a_bad_response() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    with pytest.raises(LLMBadResponse):
        await make_client(handler).complete(request())


async def test_list_content_parts_are_joined() -> None:
    def handler(http_request: httpx.Request) -> httpx.Response:
        body = completion("ignored")
        body["choices"][0]["message"]["content"] = [{"type": "text", "text": '{"a": 1}'}]
        return httpx.Response(200, json=body)

    response = await make_client(handler).complete(request())
    assert response.text == '{"a": 1}'


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMAuthError, match="OPENAI_API_KEY"):
        OpenAICompatibleClient(model="test-model")


def test_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    client = OpenAICompatibleClient(model="test-model")
    assert client._api_key == "from-env"
