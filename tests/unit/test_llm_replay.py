"""Cassettes: record once, replay for free, fail loudly when the prompt changed."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentqa.llm import (
    CassetteMiss,
    FakeClient,
    LLMRequest,
    Message,
    ReplayClient,
    request_key,
)


def request(content: str = "hi") -> LLMRequest:
    return LLMRequest(model="m", messages=[Message(role="user", content=content)])


async def test_record_then_replay(tmp_path: Path) -> None:
    cassette = tmp_path / "cassettes" / "run.jsonl"
    recorder = ReplayClient(cassette, inner=FakeClient(["answer"]), mode="record")
    recorded = await recorder.complete(request())

    assert cassette.exists()
    assert recorded.cached is False
    assert len(recorder) == 1

    replay = ReplayClient(cassette, mode="replay")
    replayed = await replay.complete(request())
    assert replayed.text == "answer"
    assert replayed.cached is True
    assert replay.hits == 1
    assert replay.misses == 0


async def test_miss_reports_the_key(tmp_path: Path) -> None:
    replay = ReplayClient(tmp_path / "empty.jsonl", mode="replay")
    with pytest.raises(CassetteMiss) as caught:
        await replay.complete(request())
    assert caught.value.key == request_key(request())
    assert str(tmp_path) in caught.value.path


async def test_changed_prompt_misses_instead_of_reusing_an_old_answer(
    tmp_path: Path,
) -> None:
    cassette = tmp_path / "run.jsonl"
    recorder = ReplayClient(cassette, inner=FakeClient(["answer"]), mode="record")
    await recorder.complete(request("old prompt"))

    replay = ReplayClient(cassette, mode="replay")
    with pytest.raises(CassetteMiss):
        await replay.complete(request("new prompt"))


async def test_auto_mode_records_on_miss_and_replays_after(tmp_path: Path) -> None:
    cassette = tmp_path / "run.jsonl"
    client = ReplayClient(cassette, inner=FakeClient(["first"]), mode="auto")
    first = await client.complete(request())
    second = await client.complete(request())
    assert first.text == "first"
    assert second.cached is True
    assert len(client) == 1


async def test_record_mode_needs_an_inner_client(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inner client"):
        ReplayClient(tmp_path / "run.jsonl", mode="record")


async def test_malformed_cassette_is_reported_with_a_line_number(tmp_path: Path) -> None:
    cassette = tmp_path / "broken.jsonl"
    cassette.write_text('{"key": "x", "response": {}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=":1:"):
        ReplayClient(cassette, mode="replay")
