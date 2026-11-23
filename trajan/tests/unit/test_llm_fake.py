"""The scripted client: order, exhaustion and recorded requests."""

from __future__ import annotations

import pytest

from qagent.llm import FakeClient, LLMError, LLMRequest, LLMResponse, Message


def request(content: str = "hi", model: str = "m") -> LLMRequest:
    return LLMRequest(model=model, messages=[Message(role="user", content=content)])


async def test_returns_scripted_text_in_order() -> None:
    client = FakeClient(["one", "two"])
    first = await client.complete(request())
    second = await client.complete(request())
    assert (first.text, second.text) == ("one", "two")
    assert first.finish_reason == "stop"
    assert len(client.requests) == 2


async def test_returns_a_full_response_untouched() -> None:
    scripted = LLMResponse(
        text="x", model="m", finish_reason="length", prompt_tokens=3, completion_tokens=4
    )
    client = FakeClient([scripted])
    got = await client.complete(request())
    assert got is scripted
    assert got.truncated is True


async def test_raises_scripted_exception() -> None:
    client = FakeClient([LLMError("provider down"), "fine"])
    with pytest.raises(LLMError):
        await client.complete(request())
    assert (await client.complete(request())).text == "fine"


async def test_exhaustion_is_explicit() -> None:
    client = FakeClient([])
    with pytest.raises(LLMError, match="exhausted"):
        await client.complete(request())


async def test_push_extends_the_script() -> None:
    client = FakeClient(["one"])
    await client.complete(request())
    client.push("two")
    assert (await client.complete(request())).text == "two"
    assert client.remaining == 0


async def test_requests_are_recorded_for_assertions() -> None:
    client = FakeClient(["ok", "ok"])
    await client.complete(request("first"))
    await client.complete(request("second"))
    assert [item.messages[0].content for item in client.requests] == ["first", "second"]
